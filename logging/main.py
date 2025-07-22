#!/usr/bin/env python3
from csv import DictWriter
from dataclasses import dataclass
from datetime import datetime
from enum import Enum, auto
from gpiozero import Button
import logging
from os import fsync, rename, name
from pathlib import Path
import pynmea2
from serial import Serial
from threading import Thread, Event, Lock
from time import perf_counter, sleep, time
from yaml import safe_load, YAMLError
from zoneinfo import ZoneInfo


# TODO: Get more of the global variables from the config file
# TODO: Use prompt_toolkit for better command line interface (input prompt below live output)


# === CLASS DEFS ===

# Enum to define what happens when the trigger is pressed, either take a single measurement or
# turn on/off continuous data taking
class TriggerBehavior(Enum):
    SINGLE = auto()  # Trigger a single measurement
    TOGGLE_CONTINUOUS = auto()  # Toggle between CONTINUOUS and STOP

# Wrapper around csv.DictWriter to ensure the file is flushed to disk after each write
class SafeDictWriter(DictWriter):
    def __init__(self, *args, **kwargs):
        try:
            self.file = open(args[0], "w", newline="", buffering=1)  # The file path is the first argument
            super().__init__(self.file, *args[1:], **kwargs)
        except FileNotFoundError as exc:
            log.critical(f"Data file not found. Please ensure the data directory exists. Data file path: {args[0]}")
            raise exc

    def writeheader(self):
        super().writeheader()
        self.file.flush()
        fsync(self.file.fileno())

    def writerow(self, data):
        super().writerow(data)
        self.file.flush()
        fsync(self.file.fileno())

    def close(self):
        self.file.close()

# Custom file handler for logger that flushes to disk after each log entry
class FlushFileHandler(logging.FileHandler):
    def emit(self, record):
        super().emit(record)
        self.flush()
        fsync(self.stream.fileno())

# Encapsulates the GPS report data
@dataclass(frozen=True)
class GPSReport:
    time_utc: str
    time_local: str
    latitude: str
    longitude: str
    altitude: str
    speed: str
    satellites: str

# Encapsulates the SQM response to the "rx" command
@dataclass(frozen=True)
class SQMReading:
    brightness: str
    frequency: str
    count: str
    period: str
    temperature: str


# === GLOBAL CONSTANTS AND VARIABLES ===


# Constants
CONFIG_FILE_PATH = Path(__file__).resolve().parent / "config.yaml"
DATA_FILE_HEADER = ["trigger_id",
                    "time_utc",
                    "time_local",
                    "latitude",
                    "longitude",
                    "altitude",
                    "speed",
                    "satellites",
                    "gps_time",
                    "sqm_time",
                    "temperature",
                    "count",
                    "frequency",
                    "brightness"]

# Variables
DATA_FILE_PATH = "" # Will be renamed with GPS timestamp once GPS fix is acquired
DIAGNOSTIC_FILE_PATH = Path(__file__).resolve().parent / "diagnostics" / f"temp.log" # Temporary filename, will be renamed with GPS timestamp once GPS fix is acquired
trigger_id = 0  # Unique ID for each trigger event, incremented on each trigger
measurements_per_trigger = 1  # Default number of measurements per trigger
measurement_interval = 0  # Default interval between measurements in seconds
extra_measurement = True # Flag to indicate if an extra sqm measurement should be taken in order to account for the temperature error
trigger_behavior = TriggerBehavior.SINGLE
logging_active = False  # Flag to indicate if logging is active
logging_lock = Lock()
logging_event = Event()


# === HELPER FUNCTIONS ===


# Check if the input is a number
def is_number(n):
    try:
        int(n)
        return True
    except ValueError:
        return False


def is_raspberry_pi_os():
    try:
        with open("/etc/os-release", "r") as f:
            return "Raspbian" in f.read() or "Raspberry Pi OS" in f.read()
    except FileNotFoundError:
        return False


def wait_for_gps_fix(gps_serial, timeout=120):
    """
    Waits for a valid GPS fix using GGA sentences.
    Returns True if fix is obtained within timeout, else False.
    """
    gps_serial.reset_input_buffer()
    start_time = time()

    while time() - start_time < timeout:
        try:
            line = gps_serial.readline().decode(errors='ignore').strip()

            if line.startswith('$GNGGA') or line.startswith('$GPGGA'):
                msg = pynmea2.parse(line)

                fix_quality = int(msg.gps_qual)
                num_sats = int(msg.num_sats)

                if fix_quality >= 1:
                    log.info(f"GPS fix obtained, satellites used: {num_sats}")
                    return True
                else:
                    log.info(f"Waiting for GPS fix... Satellites: {num_sats}, Quality: {fix_quality}")

        except pynmea2.ParseError:
            continue
        except Exception as ex:
            log.critical(f"Unexpected error while waiting for GPS fix: {ex}")
            raise ex

        sleep(1)  # Avoid hammering the CPU

    log.critical("GPS fix not acquired.")
    raise Exception("GPS fix not acquired")


# This function reads two NMEA sentences from the GPS: GNGGA and GNRMC.
# It returns a GPSReport object containing the parsed data.
def get_gps_data():
    date_utc = None
    time_utc = None
    lat = None
    lon = None
    alt = None
    speed = None
    num_sats = None

    got_gga = False
    got_rmc = False

    gps.reset_input_buffer()

    while True:
        try:
            line = gps.readline().decode().strip()
        except UnicodeDecodeError as exc:
            log.error(f"Error decoding GPS data: {exc}")
            continue
        except Exception as exc:
            log.critical(f"Unexpected error reading GPS data: {exc}")
            raise exc

        # Support both NMEA and regular GPS sentences
        if line.startswith('$GNGGA') or line.startswith('$GPGGA'):
            try:
                msg = pynmea2.parse(line)
                lat = msg.latitude
                lon = msg.longitude
                alt = msg.altitude
                num_sats = msg.num_sats
                time_utc = msg.timestamp
                got_gga = True
            except pynmea2.ParseError:
                continue

        elif line.startswith('$GNRMC') or line.startswith('$GPRMC'):
            try:
                msg = pynmea2.parse(line)
                date_utc = msg.datestamp
                speed = msg.spd_over_grnd  # knots
                got_rmc = True
            except pynmea2.ParseError:
                continue

        # If both sentences have been parsed, return the data
        if got_gga and got_rmc:
            dt_utc = datetime.combine(date_utc, time_utc).replace(tzinfo=ZoneInfo("UTC"))

            return GPSReport(
                time_utc=dt_utc.strftime("%Y-%m-%dT%H:%M:%S"),
                time_local=(dt_utc.astimezone(ZoneInfo(config_file.get("local_timezone")))).strftime("%Y-%m-%dT%H:%M:%S"),
                latitude=lat,
                longitude=lon,
                altitude=alt,
                speed=speed,
                satellites=num_sats)


def get_sqm_reading() -> SQMReading:
    sqm.reset_input_buffer()

    try:
        sqm.write(b"rx")
    except Exception as exc:
        log.critical(f"Unexpected error sending command to SQM: {exc}")
        raise exc

    try:
        response = sqm.readline().decode().strip()
    except UnicodeDecodeError as exc:
        log.critical(f"Error decoding SQM response: {exc}")
        raise exc
    except Exception as exc:
        log.critical(f"Unexpected error reading SQM response: {exc}")
        raise exc

    return SQMReading(brightness=response[3:9],
                      frequency=response[10:22],
                      count=response[23:34],
                      period=response[35:47],
                      temperature=response[49:55])


def toggle_trigger_behavior():
    """Toggle the trigger behavior between SINGLE and TOGGLE_CONTINUOUS."""
    global trigger_behavior

    with logging_lock:
        if trigger_behavior == TriggerBehavior.SINGLE:
            trigger_behavior = TriggerBehavior.TOGGLE_CONTINUOUS
            log.info("Trigger behavior set to toggle continuous mode.")
        else:
            trigger_behavior = TriggerBehavior.SINGLE
            log.info("Trigger behavior set to single measurement.")
        print(f"Trigger behavior set to {trigger_behavior.name}.")


def log_measurement():
    global data_file_writer, trigger_id

    with logging_lock:
        tr_id = trigger_id

    start_time = perf_counter()
    gps_report = get_gps_data()
    end_gps_time = perf_counter()
    sqm_reading = get_sqm_reading()
    end_sqm_time = perf_counter()
    data = {"trigger_id" : tr_id,
            "time_utc" : gps_report.time_utc,
            "time_local" : gps_report.time_local,
            "latitude" : gps_report.latitude,
            "longitude" : gps_report.longitude,
            "altitude" : gps_report.altitude,
            "speed" : gps_report.speed,
            "satellites" : gps_report.satellites,
            "gps_time" : f"{(end_gps_time - start_time):.4f}",
            "sqm_time" : f"{(end_sqm_time - end_gps_time):.4f}",
            "temperature" : sqm_reading.temperature,
            "count" : sqm_reading.count,
            "frequency" : sqm_reading.frequency,
            "brightness" : sqm_reading.brightness}

    data_file_writer.writerow(data)
    log.info(f"Logged SQM & GPS data.")


def logging_worker():
    global logging_active, extra_measurement, measurements_per_trigger, measurement_interval, trigger_behavior, trigger_id
    while True:
        logging_event.wait()

        while True:
            with logging_lock:
                la = logging_active
                em = extra_measurement
                mpt = measurements_per_trigger
                mi = measurement_interval
                tb = trigger_behavior

            if not la:
                break

            if em: # If extra measurement is enabled, take an additional SQM reading before the main measurements
                _ = get_sqm_reading()

            for _ in range(mpt):
                log_measurement()
                sleep(mi)

            with logging_lock:
                trigger_id += 1

            if tb == TriggerBehavior.SINGLE:
                with logging_lock:
                    logging_active = False
                break

        logging_event.clear()


def handle_trigger():
    global trigger_behavior, logging_active

    with logging_lock:
        if trigger_behavior == TriggerBehavior.SINGLE:
            logging_active = True
            logging_event.set()
        elif trigger_behavior == TriggerBehavior.TOGGLE_CONTINUOUS:
            logging_active = not logging_active
            logging_event.set()


# === SETUP ===


# Set up the logger
log = logging.getLogger("SQM Logger")
log.setLevel(logging.INFO) # Default to INFO level, will be changed later based on config file
log_handler = FlushFileHandler(DIAGNOSTIC_FILE_PATH, mode="w")
log_formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(funcName)s - %(message)s", datefmt="%Y-%m-%dT%H:%M:%S")
log_handler.setFormatter(log_formatter)
log.addHandler(log_handler)

# Open the configuration file
try:
    with open(CONFIG_FILE_PATH, "r") as rpi_conf_file:
        config_file = safe_load(rpi_conf_file)
    log.info("Successfully loaded configuration file")
except FileNotFoundError:
    log.critical(f"{CONFIG_FILE_PATH} not found. Please ensure the configuration file exists.")
except YAMLError as e:
    log.critical(f"Error parsing config.yaml: {e}")
except Exception as e:
    log.critical(f"Unexpected error reading config.yaml: {e}")

# Set the logging level based on the configuration file
logging_level = config_file.get("logging_level", "INFO").upper()
if logging_level == "DEBUG":
    log.setLevel(logging.DEBUG)
    log_handler.setLevel(logging.DEBUG)
    log.debug("Log level set to DEBUG based on configuration file")
elif logging_level == "INFO":
    log.setLevel(logging.INFO)
    log_handler.setLevel(logging.INFO)
    log.info("Log level set to INFO based on configuration file")
elif logging_level == "WARNING":
    log.setLevel(logging.WARNING)
    log_handler.setLevel(logging.WARNING)
    log.warning("Log level set to WARNING based on configuration file")
elif logging_level == "ERROR":
    log.setLevel(logging.ERROR)
    log_handler.setLevel(logging.ERROR)
    log.error("Log level set to ERROR based on configuration file")
elif logging_level == "CRITICAL":
    log.setLevel(logging.CRITICAL)
    log_handler.setLevel(logging.CRITICAL)
    log.critical("Log level set to CRITICAL based on configuration file")

# Set the measurements per trigger based on the configuration file
measurements_per_trigger = config_file.get("measurements_per_trigger", 1)
log.info(f"Measurements per trigger set to {measurements_per_trigger} based on configuration file")

# Set the measurements interval based on the configuration file
measurement_interval = config_file.get("measurement_interval")
log.info(f"Measurements interval set to {measurement_interval} seconds based on configuration file")

# Initialize the button trigger (only on raspberry pi)
if config_file.get("trigger_button_gpio_pin") != "NONE":
    try:
        button = Button(config_file.get("trigger_button_gpio_pin"), pull_up=True, bounce_time=0.25)
        button.when_pressed = lambda: handle_trigger()
        log.info(f"Successfully initialized button on GPIO pin {config_file.get('trigger_button_gpio_pin')}")
    except ValueError as e:
        log.error(f"Invalid GPIO pin number: {config_file.get('trigger_button_gpio_pin')}. Please check the configuration file. Error: {e}")
    except Exception as e:
        log.error(f"Error initializing button: {e}")

# Initialize the SQM serial port
try:
    sqm = Serial(config_file.get("sqm_serial_port"), baudrate=115200, timeout=30)
    sqm.flush()  # Clear any existing data in the input buffer
    log.info(f"Successfully opened SQM serial port {config_file.get('sqm_serial_port')}")
except Exception as e:
    log.critical(f"Unexpected error opening SQM serial port: {e}")
    raise e

# Initialize the GPS serial port
try:
    gps = Serial(config_file.get("gps_serial_port"), baudrate=4800, timeout=2)
    log.info(f"Successfully opened GPS serial port {config_file.get('gps_serial_port')}")
except Exception as e:
    log.critical(f"Unexpected error opening GPS serial port: {e}")
    raise e

# Wait for GPS fix before starting measurements
wait_for_gps_fix(gps, 120)

# Get the GPS data to set the start time for file naming
try:
    program_start_time = get_gps_data().time_local.replace(":", "-").replace("T", "_")
    log.info(f"Start time is {program_start_time}")
except Exception as e:
    log.critical(f"Error getting GPS data for start time: {e}")
    raise e

# Rename the data file path
try:
    # The data file hasn't been created yet, so just set the path to the new name
    new_datafile_path = Path(__file__).resolve().parent / "data" / f"{program_start_time}.csv"
    DATA_FILE_PATH = new_datafile_path
    log.info(f"Data file path created: {DATA_FILE_PATH}")
except Exception as e:
    log.critical(f"Error setting new data file path: {e}")
    raise e

# Rename the diagnostic file path
try:
    new_diagnostic_file_path = Path(__file__).resolve().parent / "diagnostics" / f"{program_start_time}.log"

    # Close the old handler and rename the file
    log.removeHandler(log_handler)
    log_handler.close()

    # Rename the diagnostic file
    rename(DIAGNOSTIC_FILE_PATH, new_diagnostic_file_path)
    DIAGNOSTIC_FILE_PATH = new_diagnostic_file_path

    # Recreate the handler with the new file path
    log_handler = FlushFileHandler(DIAGNOSTIC_FILE_PATH, mode="a")
    log_handler.setFormatter(log_formatter)
    log.addHandler(log_handler)

    log.info(f"Successfully renamed diagnostic file using GPS timestamp: {DIAGNOSTIC_FILE_PATH}")
except Exception as e:
    log.critical(f"Error renaming data or diagnostic file: {e}")
    raise e

# Open the csv data file writer
try:
    data_file_writer = SafeDictWriter(DATA_FILE_PATH,
                                      delimiter=',',
                                      fieldnames=DATA_FILE_HEADER)
    data_file_writer.writeheader()  # Write the header to the file
    log.info("Successfully opened data file")
except Exception as e:
    log.critical(f"Error opening data file {DATA_FILE_PATH}: {e}")
    raise e


# === THREADING / TRIGGER SETUP ===


logging_thread = Thread(target=logging_worker)
logging_thread.daemon = True  # Ensure the thread exits when the main program exits
logging_thread.start()


# === MAIN FUNCTION ===


def main():
    global measurement_interval, measurements_per_trigger, trigger_behavior, extra_measurement
    try:
        while True:
            cmd = input("Enter command: ")
            if cmd.strip().lower() == 't': # t for trigger
                handle_trigger()
            elif cmd.strip().lower() == 'm': # m for mode switch
                toggle_trigger_behavior()
            elif cmd.strip().lower() == 'x': # x for extra measurement
                with logging_lock:
                    extra_measurement = not extra_measurement
                    log.info(f"Extra measurement set to {'enabled' if extra_measurement else 'disabled'}")
                    print(f"Extra measurement {'enabled' if extra_measurement else 'disabled'}")
            elif cmd.strip().lower() == 'q':
                log.info("User requested to quit the program.")
                print("Exiting program...")
                break
            elif cmd.strip().lower().startswith('n') and is_number(cmd.strip()[1:]):
                with logging_lock:
                    measurements_per_trigger = int(cmd.strip()[1:])
                    log.info(f"User set measurements per trigger to {measurements_per_trigger}")
                    print(f"Measurements per trigger set to {measurements_per_trigger}")
            elif cmd.strip().lower().startswith('i') and is_number(cmd.strip()[1:]) and int(cmd.strip()[1:]) >= 0:
                with logging_lock:
                    measurement_interval = int(cmd.strip()[1:])
                    log.info(f"User set measurements interval to {measurement_interval} seconds")
                    print(f"Measurements interval set to {measurement_interval} seconds")
            elif cmd.strip().lower().startswith('data') and is_number(cmd.strip()[4:]): # Print the last N lines of the data file
                with open(DATA_FILE_PATH, "r", newline="") as read_file:
                    lines = "".join(read_file.readlines()[-int(cmd.strip()[4:]):]).replace(",", ", ")
                print(lines)
            elif cmd.strip().lower().startswith('diag') and is_number(cmd.strip()[4:]): # Print the last N lines of the data file
                with open(DIAGNOSTIC_FILE_PATH, "r", newline="") as read_file:
                    lines = "".join(read_file.readlines()[-int(cmd.strip()[4:]):]).replace(",", ", ")
                print(lines)
            elif cmd.strip().lower() == 's': # s for settings
                with logging_lock:
                    print(f"Measurements per trigger: {measurements_per_trigger}\n"
                          f"Measurement interval: {measurement_interval} seconds\n"
                          f"Extra measurement: {'Yes' if extra_measurement else 'No'}\n"
                          f"Trigger behavior mode: {trigger_behavior}\n"
                          f"Currently logging: {'Yes' if logging_event.is_set() else 'No'}")
            else:
                print("Unknown command. Use one of the following:\n"
                      "t - trigger measurement\n"
                      "m - toggle trigger behavior mode (single measurement or toggle continuous mode)\n"
                      "x - toggle extra measurement (to account for temperature error)\n"
                      "n<number> - set number of measurements per trigger\n"
                      "i<number> - set interval between measurements in seconds\n"
                      "data<number> - print the last N lines from the data file\n"
                      "diag<number> - print the last N lines from the diagnostic file\n"
                      "s - show current settings\n"
                      "q - quit the program")
    except KeyboardInterrupt:
        print("\nExiting program...")
    finally:
        data_file_writer.close()
        gps.close()
        sqm.close()
        log.info("Closed GPS & SQM serial ports, and data file.")
        log.info("Program terminated.")


if __name__ == "__main__":
    main()