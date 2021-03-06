#!/usr/bin/python

# Importing libraries
import json
import time
import rethinkdb as r
from time import sleep
import threading

from Watcher import WatchTable
from RethinkDB import RethinkDBConnection

# Importing libraries
import opc
from usbiss.spi import SPI

# Setting error message format for visibility
def exit_error(e, message):
    print('----------------------------------------------------------------------')
    print('\t', message)
    print(e)
    print('----------------------------------------------------------------------')
    exit(1)


# code to connect to the instrument
def get_instrument(port):
    print('Trying to connect to instrument', port, '...')

    # Build the connector
    try:
        instrument = SPI("/dev/" + port)

        print('Connected to instrument!', instrument)

        return instrument
    except Exception as e:
        exit_error('Could not connect to /dev/' + port)


def get_alpha(spi):
    # Set the SPI mode and clock speed
    spi.mode = 1
    spi.max_speed_hz = 500000

    try:
        alpha = opc.OPCN2(spi, debug=True)

        if alpha is None:
            raise Exception('Could not connect!')

        print('Connected to', alpha)
        return alpha

    except Exception as e:
        exit_error(e, 'Could not start alpha controller')


def device_status(alpha):
    print('Device status:')
    print(alpha.read_pot_status())


def get_bin_name(bin_name):
    bin_name = bin_name.lower()

    if " " in bin_name:
        return bin_name.replace(" ", "_")

    return bin_name


def get_type(bin_name):
    """"Sometimes the bin_name has capital letters or spaces
    Here we'll remove those spaces, and normalize the names"""
    bin_name = get_bin_name(bin_name)

    if "bin_" in bin_name:
        return 'bin'

    if 'mtof' in bin_name:
        return 'mtof'

    return bin_name


class WorkOPC(RethinkDBConnection):

    def __init__(self, **kwargs):
        super(WorkOPC, self).__init__(**kwargs)

        self.config = kwargs.get("instrumentConfig", None)
        self.port = kwargs.get("port", "ttyACM0")

        self.configWatcher = WatchTable(
            "config",
            self.callback,
            config="database-config.json"
        )

    def check_collect_data_switch(self):
        """Check to see if the Collect Data button is on, in which case run main script"""
        while True:
            if self.config("active") == "true":
                break

            sleep(0.5)

    def wait_for_config_from_remote(self):
        """We should load the current configuration from the server"""
        while True:
            if self.config:
                break

            sleep(0.5)

    def runOPC(self):
        self.wait_for_config_from_remote()
        self.check_collect_data_switch()
        self.main()

    def initiate(self):
        # Turn on the device
        self.alpha.on()
        device_status(self.alpha)

        sleep(2)

        self.alpha.toggle_fan(True)
        self.alpha.toggle_laser(True)

        power = 255
        self.alpha.set_fan_power(power)
        # alpha.set_laser_power(power)

        device_status(self.alpha)

    def perform(self):

        print('----------------------------------------------------------------------')
        ts = time.gmtime()
        print(time.strftime("%Y-%m-%d %H:%M:%S", ts))
        histogram = self.alpha.histogram()

        if histogram is None:
            raise Exception('Could not load histogram')

        for key in histogram:
            self.save_data(key, histogram[key])

    def send_error_to_remote(self, error):
        try:
            self.runQuery(
                r.db('telemetry').table('errors').insert({
                    "error": error,
                    "shouldAlert": True
                })
            )
        except Exception as e:
            print("Error while sending error to remote:", e)

    def save_to_remote(self, key, data):
        """Save data to remote host"""
        try:
            currentSample = self.config["config"]["sampleName"]["value"]

            self.runQuery(
                r.db('telemetry').table('data').insert({
                    "name": key,
                    "sampleName": currentSample,
                    "type": get_type(key),
                    "time": time.time(),
                    "value": data
                })
            )
        except Exception as e:
            print("Error while saving to remote:", e)

    def open_local_file(self, file):
        self.file = open("testData_1.csv", "a")

    def save_to_local(self, key, data):
        """save data to local sim card in case of connection loss"""
        # https://stackoverflow.com/questions/4706499/how-do-you-append-to-a-file
        # self.file.write (because we don't want to open the file each time, it's slow)
        ts = time.gmtime()
        print(time.strftime("%Y-%m-%d %H:%M:%S", ts))
        histogram = self.alpha.histogram()

        try:
            self.file.write(ts, key, histogram[key])

        except Exception as e:
            print("Error while saving to local:", e)

    def save_data(self, key, data):
        self.save_to_local(key, data)
        self.save_to_remote(key, data)

    def shut_down(self):
        sleep(2)
        print('----------------------------------------------------------------------')
        ts = time.gmtime()
        print(time.strftime("%Y-%m-%d %H:%M:%S", ts))
        # Turn the device off
        self.alpha.off()

        print(self.alpha, '- Instrument finished getting data')

    def main(self):
        spi = get_instrument(self.port)
        self.alpha = get_alpha(spi)
        print('Alphasense instrument processing request')
        print('-----------------------------------------------------------------------')

        self.initiate()

        while True:
            try:
                sleep(2)
                self.perform()

            except KeyboardInterrupt as e:
                print('Goodbye...')
                break

            except Exception as e:
                self.shut_down()
                exit_error(e, 'Failed while retrieving results, this is still not working...')

        self.shut_down()

    def callback(self, change):
        self.config = change["new_val"]

if __name__ == '__main__':
    print('Welcome to the OPC-N2 interfacing programme')

    opcDriver = WorkOPC(config="database-config.json").runOPC()
