#!/usr/bin/env python3


from csv import DictWriter
from dataclasses import dataclass
from datetime import datetime
from enum import Enum, auto
from gpiozero import Button
from logging import getLogger, FileHandler, Formatter, Logger, INFO, DEBUG, WARNING, ERROR, CRITICAL
from os import fsync, rename
from pathlib import Path
from pynmea2 import parse, ParseError
from serial import Serial
from threading import Thread, Event, Lock
from time import perf_counter, sleep, time
from typing import Any, Final
from yaml import safe_load, YAMLError
from zoneinfo import ZoneInfo


# === Class Definitions ===


class TriggerBehavior(Enum):
    """ Enum to define what happens when the trigger is pressed,
    either take a single measurement or turn on/off continuous data taking."""
    SINGLE = auto()  # Trigger a single measurement
    TOGGLE_CONTINUOUS = auto()  # Toggle between CONTINUOUS and STOP


class Settings:
    """ Class to encapsulate the settings for the SQM logger.
    This class provides thread-safe access to the settings and allows for getting and setting
    the measurements per trigger, measurement interval, extra measurement flag, and local timezone."""

    def __init__(self,
                 measurements_per_trigger: int = 1,
                 measurement_interval: int = 0,
                 extra_measurement: bool = True,
                 local_timezone: str = "America/Los_Angeles",
                 trigger_behavior: TriggerBehavior = TriggerBehavior.SINGLE,
                 logging_active: bool = False) -> None:
        self._lock = Lock()
        self._measurements_per_trigger = measurements_per_trigger
        self._measurement_interval = measurement_interval
        self._extra_measurement = extra_measurement
        self._local_timezone = local_timezone
        self._trigger_behavior = trigger_behavior
        self._logging_active = logging_active

    # Getters and setters for the settings
    def get_measurements_per_trigger(self) -> int:
        with self._lock:
            return self._measurements_per_trigger

    def set_measurements_per_trigger(self, value: int) -> None:
        with self._lock:
            self._measurements_per_trigger = value

    def get_measurement_interval(self) -> int:
        with self._lock:
            return self._measurement_interval

    def set_measurement_interval(self, value: int) -> None:
        with self._lock:
            self._measurement_interval = value

    def is_extra_measurement_enabled(self) -> bool:
        with self._lock:
            return self._extra_measurement

    def set_extra_measurement(self, value: bool) -> None:
        with self._lock:
            self._extra_measurement = value

    def get_local_timezone(self) -> str:
        with self._lock:
            return self._local_timezone

    def set_local_timezone(self, value: str) -> None:
        with self._lock:
            self._local_timezone = value

    def get_trigger_behavior(self) -> TriggerBehavior:
        with self._lock:
            return self._trigger_behavior

    def set_trigger_behavior(self, value: TriggerBehavior) -> None:
        with self._lock:
            self._trigger_behavior = value

    def is_logging_active(self) -> bool:
        with self._lock:
            return self._logging_active

    def set_logging_active(self, value: bool) -> None:
        with self._lock:
            self._logging_active = value

    @property
    def measurements_per_trigger(self) -> int:
        return self.get_measurements_per_trigger()

    @measurements_per_trigger.setter
    def measurements_per_trigger(self, value: int) -> None:
        self.set_measurements_per_trigger(value)

    @property
    def measurement_interval(self) -> int:
        return self.get_measurement_interval()

    @measurement_interval.setter
    def measurement_interval(self, value: int) -> None:
        self.set_measurement_interval(value)

    @property
    def extra_measurement(self) -> bool:
        return self.is_extra_measurement_enabled()

    @extra_measurement.setter
    def extra_measurement(self, value: bool) -> None:
        self.set_extra_measurement(value)

    @property
    def local_timezone(self) -> str:
        return self.get_local_timezone()

    @local_timezone.setter
    def local_timezone(self, value: str) -> None:
        self.set_local_timezone(value)

    @property
    def trigger_behavior(self) -> TriggerBehavior:
        return self.get_trigger_behavior()

    @trigger_behavior.setter
    def trigger_behavior(self, value: TriggerBehavior) -> None:
        self.set_trigger_behavior(value)

    @property
    def logging_active(self) -> bool:
        return self.is_logging_active()

    @logging_active.setter
    def logging_active(self, value: bool) -> None:
        self.set_logging_active(value)


@dataclass(frozen=True)
class GPSReport:
    """ Class to encapsulate the GPS report data.
    This class provides a frozen dataclass to ensure immutability of the GPS report data.
    It contains the UTC time, local time, latitude, longitude, altitude, speed, and number of satellites.
    """
    time_utc: str
    time_local: str
    latitude: str
    longitude: str
    altitude: str
    speed: str
    satellites: str


@dataclass(frozen=True)
class SQMReading:
    """ Class to encapsulate the SQM reading data.
    This class provides a frozen dataclass to ensure immutability of the SQM reading data.
    It contains the brightness, frequency, count, period, and temperature.
    """
    brightness: str
    frequency: str
    count: str
    period: str
    temperature: str


class SafeDictWriter(DictWriter):
    """ Custom DictWriter that flushes the file to disk after each write."""
    def __init__(self, file_path: Path, logger : Logger, *args, **kwargs):
        try:
            self.file = open(file_path, "w", newline="", buffering=1)  # The file path is the first argument
            self.logger = logger
            super().__init__(self.file, *args, **kwargs)
        except FileNotFoundError as exc:
            self.logger.critical(f"Data file not found. Please ensure the data directory exists. Data file path: {file_path}")
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


class FlushFileHandler(FileHandler):
    """ Custom FileHandler that flushes the file to disk after each log entry,
    with thread-safe access to the file stream."""
    def emit(self, record):
        super().emit(record)
        self.flush()
        fsync(self.stream.fileno())


# === Helper Functions ===


# Check if the input is a number
def is_number(n: Any) -> bool:
    """ Check if the input can be converted to an integer.
    :param n: Input to check.
    :return: True if n is a number, False otherwise.
    """

    try:
        int(n)
        return True
    except ValueError:
        return False


def wait_for_gps_fix(gps_serial: Serial, logger: Logger, timeout=120) -> None:
    """
    Waits for a valid GPS fix using GGA sentences.
    If a valid fix is obtained, it returns, otherwise it raises an exception after the timeout.
    :param gps_serial: The serial port connected to the GPS module.
    :param logger: Logger instance to log messages.
    :param timeout: Maximum time to wait for a GPS fix in seconds.
    :raises Exception: If a valid GPS fix is not obtained within the timeout period.
    """

    gps_serial.reset_input_buffer()
    start_time = time()

    while time() - start_time < timeout:
        try:
            line = gps_serial.readline().decode(errors='ignore').strip()

            if line.startswith('$GNGGA') or line.startswith('$GPGGA'):
                msg = parse(line)

                fix_quality = int(msg.gps_qual)
                num_sat = int(msg.num_sats)

                if fix_quality >= 1:
                    logger.info(f"GPS fix obtained, satellites used: {num_sat}")
                    return
                else:
                    logger.info(f"Waiting for GPS fix... Satellites: {num_sat}, Quality: {fix_quality}")

        except ParseError:
            continue
        except Exception as ex:
            logger.critical(f"Unexpected error while waiting for GPS fix: {ex}")
            raise ex

        sleep(1)  # Avoid hammering the CPU

    logger.critical("GPS fix not acquired.")
    raise Exception("GPS fix not acquired")

# This function reads two NMEA sentences from the GPS: GNGGA and GNRMC.
# It returns a GPSReport object containing the parsed data.
def get_gps_data(gps_serial: Serial, logger: Logger, local_tz: str) -> GPSReport:
    """ Reads GPS data from the serial port and returns a GPSReport object.
    :param gps_serial: The serial port connected to the GPS module.
    :param logger: Logger instance to log messages.
    :param local_tz: The local timezone to convert the UTC time to.
    :return: A GPSReport object containing the parsed GPS data.
    """

    date_utc = None
    time_utc = None
    lat = None
    lon = None
    alt = None
    speed = None
    num_sat = None

    got_gga = False
    got_rmc = False

    gps_serial.reset_input_buffer()

    while True:
        try:
            line = gps_serial.readline().decode().strip()
        except UnicodeDecodeError as exc:
            logger.error(f"Error decoding GPS data: {exc}")
            continue
        except Exception as exc:
            logger.critical(f"Unexpected error reading GPS data: {exc}")
            raise exc

        # Support both NMEA and regular GPS sentences
        if line.startswith('$GNGGA') or line.startswith('$GPGGA'):
            try:
                msg = parse(line)
                lat = msg.latitude
                lon = msg.longitude
                alt = msg.altitude
                num_sat = msg.num_sats
                time_utc = msg.timestamp
                got_gga = True
            except ParseError:
                continue

        elif line.startswith('$GNRMC') or line.startswith('$GPRMC'):
            try:
                msg = parse(line)
                date_utc = msg.datestamp
                speed = msg.spd_over_grnd  # knots
                got_rmc = True
            except ParseError:
                continue

        # If both sentences have been parsed, return the data
        if got_gga and got_rmc:
            dt_utc = datetime.combine(date_utc, time_utc).replace(tzinfo=ZoneInfo("UTC"))

            return GPSReport(
                time_utc=dt_utc.strftime("%Y-%m-%dT%H:%M:%S"),
                time_local=(dt_utc.astimezone(ZoneInfo(local_tz))).strftime("%Y-%m-%dT%H:%M:%S"),
                latitude=lat,
                longitude=lon,
                altitude=alt,
                speed=speed,
                satellites=num_sat)

def get_sqm_data(sqm_serial: Serial, logger: Logger) -> SQMReading:
    """ Reads a reading from the SQM serial port and returns a SQMReading object.
    :param sqm_serial: The serial port connected to the SQM device.
    :param logger: Logger instance to log messages.
    :return: A SQMReading object containing the parsed SQM data.
    """

    sqm_serial.reset_input_buffer()

    # The "rx" command is used to request a reading from the SQM
    try:
        sqm_serial.write(b"rx")
    except Exception as exc:
        logger.critical(f"Unexpected error sending command to SQM: {exc}")
        raise exc

    try:
        response = sqm_serial.readline().decode().strip()
    except UnicodeDecodeError as exc:
        logger.critical(f"Error decoding SQM response: {exc}")
        raise exc
    except Exception as exc:
        logger.critical(f"Unexpected error reading SQM response: {exc}")
        raise exc

    return SQMReading(brightness=response[2:8],
                      frequency=response[10:20],
                      count=response[23:33],
                      period=response[35:46],
                      temperature=response[49:54])

def log_measurement(sqm_serial: Serial,
                    gps_serial: Serial,
                    data_file_writer: SafeDictWriter,
                    trigger_id: int,
                    measurement_id: int,
                    logger: Logger,
                    settings: Settings) -> None:
    """ Logs a single measurement by reading from the SQM and GPS, and writing to the data file.
    :param sqm_serial: The serial port connected to the SQM device.
    :param gps_serial: The serial port connected to the GPS module.
    :param data_file_writer: The CSV writer for the data file.
    :param trigger_id: The unique ID for the trigger event.
    :param measurement_id: The unique ID for the measurement within the trigger event.
    :param logger: Logger instance to log messages.
    :param settings: Settings instance containing the configuration for the logger.
    """

    start_time = perf_counter()
    gps_report = get_gps_data(gps_serial, logger, settings.local_timezone)
    end_gps_time = perf_counter()
    sqm_reading = get_sqm_data(sqm_serial, logger)
    end_sqm_time = perf_counter()
    data = {"brightness" : sqm_reading.brightness,
            "count" : sqm_reading.count,
            "frequency": sqm_reading.frequency,
            "period" : sqm_reading.period,
            "temperature" : sqm_reading.temperature,
            "time_utc" : gps_report.time_utc,
            "time_local" : gps_report.time_local,
            "latitude" : f"{round(float(gps_report.latitude), 5)}",
            "longitude" : f"{round(float(gps_report.longitude), 5)}",
            "altitude" : gps_report.altitude,
            "speed" : gps_report.speed,
            "satellites" : gps_report.satellites,
            "trigger_id": str(trigger_id).zfill(5),
            "measurement_id": str(measurement_id).zfill(3),
            "gps_time": f"{round((end_gps_time - start_time), 4)}",
            "sqm_time": f"{round((end_sqm_time - end_gps_time), 4)}"}

    data_file_writer.writerow(data)
    logger.info(f"Logged SQM & GPS data.")

# TODO: re-examine the inner while loop, I feel like it is not needed, or could be done differently
# TODO: check the logic in the continuous mode, it may never exit the inner loop
def logging_worker(sqm_serial: Serial,
                   gps_serial: Serial,
                   data_file_writer: SafeDictWriter,
                   logger: Logger,
                   settings: Settings,
                   logging_event: Event) -> None:
    """ Worker thread that handles the logging of SQM readings and GPS data.
    :param sqm_serial: The serial port connected to the SQM device.
    :param gps_serial: The serial port connected to the GPS module.
    :param data_file_writer: The CSV writer for the data file.
    :param logger: Logger instance to log messages.
    :param settings: Settings instance containing the configuration for the logger.
    :param logging_event: Event to trigger the logging process.
    """

    try:
        # Thread initialization
        logging_event.clear()
        data_file_writer.writeheader()  # Write the header to the file
        trigger_id = 0  # Unique ID for each trigger event, incremented on each trigger

        while True:
            logging_event.wait()
            logging_event.clear()  # Clear the event to wait for the next trigger
            settings.logging_active = True
            measurement_id = 0  # Unique ID for each measurement within a trigger event

            while True:
                # If extra measurement is enabled, take an additional SQM reading before the main measurements
                # to account for temperature error
                if settings.extra_measurement:
                    _ = get_sqm_data(sqm_serial, logger)

                for _ in range(settings.measurements_per_trigger):
                    log_measurement(sqm_serial, gps_serial, data_file_writer, trigger_id, measurement_id, logger, settings)
                    measurement_id += 1
                    sleep(settings.measurement_interval)

                trigger_id += 1

                # if the mode is set to SINGLE, or if TOGGLE_CONTINUOUS and the logging_event was set again, exit
                if settings.trigger_behavior == TriggerBehavior.SINGLE or (settings.trigger_behavior == TriggerBehavior.TOGGLE_CONTINUOUS and logging_event.is_set()):
                    break

            settings.logging_active = False
            logging_event.clear()
    except Exception as e:
        logger.critical(f"Unexpected error in logging worker thread: {e}")
        raise e


# === Entry Point ===


def main():
    # Constants
    CONFIG_FILE_PATH: Final = Path(__file__).resolve().parent / "config.yaml" # config file should be in the same directory as this script
    DATA_FILE_HEADER: Final = ["brightness",
                               "count",
                               "frequency",
                               "period",
                               "temperature",
                               "time_utc",
                               "time_local",
                               "latitude",
                               "longitude",
                               "altitude",
                               "speed",
                               "satellites",
                               "trigger_id",
                               "measurement_id",
                               "gps_time",
                               "sqm_time"]

    # Variables
    logging_event = Event()
    settings = Settings()
    diagnostic_file_path = Path(__file__).resolve().parent / "diagnostics" / f"temp.log"  # Temporary filename, will be renamed with GPS timestamp once GPS fix is acquired

    # Construct logger
    log = getLogger("SQMLogger")
    log.setLevel(INFO)  # Default to INFO level, will be changed later based on config file
    log_handler = FlushFileHandler(diagnostic_file_path, mode="w")
    log_formatter = Formatter("%(asctime)s - %(levelname)s - %(funcName)s - %(message)s",
                                      datefmt="%Y-%m-%dT%H:%M:%S")
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

    # Set the measurements per trigger based on the configuration file
    settings.measurements_per_trigger = config_file.get("measurements_per_trigger", 1)
    log.info(f"Measurements per trigger set to {settings.measurements_per_trigger} based on configuration file")

    # Set the measurements interval based on the configuration file
    settings.measurement_interval = config_file.get("measurement_interval", 0)
    log.info(f"Measurements interval set to {settings.measurement_interval} seconds based on configuration file")

    # Set the extra measurement flag based on the configuration file
    settings.extra_measurement = config_file.get("extra_measurement", True)
    log.info(f"Extra measurement set to {'enabled' if settings.extra_measurement else 'disabled'} based on configuration file")

    # Set the local timezone based on the configuration file
    settings.local_timezone = config_file.get("local_timezone", "America/Los_Angeles")
    log.info(f"Local timezone set to {settings.local_timezone} based on configuration file")

    # Set the trigger behavior based on the configuration file
    trigger_behavior_str = config_file.get("trigger_behavior", "SINGLE").upper()
    if trigger_behavior_str == "SINGLE":
        settings.trigger_behavior = TriggerBehavior.SINGLE
        log.info("Trigger behavior set to SINGLE based on configuration file")
    elif trigger_behavior_str == "TOGGLE_CONTINUOUS":
        settings.trigger_behavior = TriggerBehavior.TOGGLE_CONTINUOUS
        log.info("Trigger behavior set to TOGGLE_CONTINUOUS based on configuration file")
    else:
        log.error(f"Invalid trigger behavior '{trigger_behavior_str}' in configuration file. Defaulting to SINGLE.")
        settings.trigger_behavior = TriggerBehavior.SINGLE

    # Set the logging level based on the configuration file
    logging_level = config_file.get("logging_level", "INFO").upper()
    if logging_level == "DEBUG":
        log.setLevel(DEBUG)
        log_handler.setLevel(DEBUG)
        log.debug("Log level set to DEBUG based on configuration file")
    elif logging_level == "INFO":
        log.setLevel(INFO)
        log_handler.setLevel(INFO)
        log.info("Log level set to INFO based on configuration file")
    elif logging_level == "WARNING":
        log.setLevel(WARNING)
        log_handler.setLevel(WARNING)
        log.warning("Log level set to WARNING based on configuration file")
    elif logging_level == "ERROR":
        log.setLevel(ERROR)
        log_handler.setLevel(ERROR)
        log.error("Log level set to ERROR based on configuration file")
    elif logging_level == "CRITICAL":
        log.setLevel(CRITICAL)
        log_handler.setLevel(CRITICAL)
        log.critical("Log level set to CRITICAL based on configuration file")
    else:
        log.error(f"Invalid logging level '{logging_level}' in configuration file. Defaulting to INFO.")
        log.setLevel(INFO)
        log_handler.setLevel(INFO)

    # Initialize the button trigger (only on raspberry pi)
    if config_file.get("trigger_button_gpio_pin") != "NONE":
        try:
            button = Button(config_file.get("trigger_button_gpio_pin"), pull_up=True, bounce_time=0.25)
            button.when_pressed = lambda: logging_event.set()
            log.info(f"Successfully initialized button on GPIO pin {config_file.get('trigger_button_gpio_pin')}")
        except ValueError as e:
            log.error(
                f"Invalid GPIO pin number: {config_file.get('trigger_button_gpio_pin')}. Please check the configuration file. Error: {e}")
        except Exception as e:
            log.error(f"Error initializing button: {e}")

    # Initialize the SQM serial port
    try:
        sqm = Serial(config_file.get("sqm_serial_port"), baudrate=115200, timeout=30)
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
    wait_for_gps_fix(gps, log, 180)

    # Get the GPS data to set the start time for file naming
    try:
        program_start_time = get_gps_data(gps, log, settings.local_timezone).time_local.replace(":", "-").replace("T", "_")
        log.info(f"Start time is {program_start_time}")
    except Exception as e:
        log.critical(f"Error getting GPS data for start time: {e}")
        raise e

    # Rename the data file path
    try:
        # The data file hasn't been created yet, so just set the path to the new name
        new_datafile_path = Path(__file__).resolve().parent / "data" / f"{program_start_time}.csv"
        data_file_path = new_datafile_path
        log.info(f"Data file path created: {data_file_path}")
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
        rename(diagnostic_file_path, new_diagnostic_file_path)
        diagnostic_file_path = new_diagnostic_file_path

        # Recreate the handler with the new file path
        log_handler = FlushFileHandler(diagnostic_file_path, mode="a")
        log_handler.setFormatter(log_formatter)
        log.addHandler(log_handler)

        log.info(f"Successfully renamed diagnostic file using GPS timestamp: {diagnostic_file_path}")
    except Exception as e:
        log.critical(f"Error renaming data or diagnostic file: {e}")
        raise e

    # Open the csv data file writer
    try:
        data_file_writer = SafeDictWriter(data_file_path,
                                          logger=log,
                                          delimiter=',',
                                          fieldnames=DATA_FILE_HEADER)
        log.info("Successfully opened data file")
    except Exception as e:
        log.critical(f"Error opening data file {data_file_path}: {e}")
        raise e

    # Spawn measurement thread
    logging_thread = Thread(target=logging_worker,
                            args=(sqm, gps, data_file_writer, log, settings, logging_event),
                            name="LoggingWorkerThread",
                            daemon=True)
    logging_thread.start()

    # Main loop to handle cli commands and triggers
    try:
        while True:
            cmd = input("Enter command: ")
            if cmd.strip().lower() == 't': # t for trigger
                logging_event.set()
            elif cmd.strip().lower() == 'm': # m for mode switch
                if settings.trigger_behavior == TriggerBehavior.SINGLE:
                    settings.trigger_behavior = TriggerBehavior.TOGGLE_CONTINUOUS
                    log.info("Trigger behavior set to TOGGLE_CONTINUOUS")
                else:
                    settings.trigger_behavior = TriggerBehavior.SINGLE
                    log.info("Trigger behavior set to SINGLE")
            elif cmd.strip().lower() == 'x': # x for extra measurement
                settings.extra_measurement = not settings.extra_measurement
                log.info(f"Extra measurement set to {'enabled' if settings.extra_measurement else 'disabled'}")
            elif cmd.strip().lower() == 'q':
                log.info("User requested to quit the program.")
                print("Exiting program...")
                break
            elif cmd.strip().lower().startswith('n') and is_number(cmd.strip()[1:]):
                settings.measurements_per_trigger = int(cmd.strip()[1:])
                log.info(f"User set measurements per trigger to {settings.measurements_per_trigger}")
            elif cmd.strip().lower().startswith('i') and is_number(cmd.strip()[1:]) and int(cmd.strip()[1:]) >= 0:
                settings.measurement_interval = int(cmd.strip()[1:])
                log.info(f"User set measurements interval to {settings.measurement_interval} seconds")
            elif cmd.strip().lower().startswith('data') and is_number(cmd.strip()[4:]): # Print the last N lines of the data file
                with open(data_file_path, "r", newline="") as read_file:
                    lines = "".join(read_file.readlines()[-int(cmd.strip()[4:]):]).replace(",", ", ")
                print(lines)
            elif cmd.strip().lower().startswith('diag') and is_number(cmd.strip()[4:]): # Print the last N lines of the data file
                with open(diagnostic_file_path, "r", newline="") as read_file:
                    lines = "".join(read_file.readlines()[-int(cmd.strip()[4:]):]).replace(",", ", ")
                print(lines)
            elif cmd.strip().lower() == 's': # s for settings
                print(f"Measurements per trigger: {settings.measurements_per_trigger}\n"
                      f"Measurement interval: {settings.measurement_interval} seconds\n"
                      f"Extra measurement: {'Yes' if settings.extra_measurement else 'No'}\n"
                      f"Trigger behavior mode: {settings.trigger_behavior}\n"
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
        log_handler.close()


if __name__ == "__main__":
    main()