import datetime
import logging

from influxdb import InfluxDBClient
from influxdb_datalogger import *
import os
import copy


class InfluxWriter(DatabaseWriter):
    def __init__(self,
                 influx_ip,
                 influx_port,
                 database_name="NSO",
                 username=None,
                 password=None):
        self.influx_ip = influx_ip
        self.influx_port = influx_port
        self.database_name = database_name
        self.username = username
        self.password = password

    def write_data(self, datalogger: DataLogger):
        client = InfluxDBClient(self.influx_ip, self.influx_port, database=self.database_name, username=self.username, password=self.password)
        client.create_database(self.database_name)
        success = client.write_points(datalogger.dataset)
        if success:
            print("Test data written to influxdb")
        else:
            print("Failed to write test-data to influxdb")
