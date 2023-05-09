#!/usr/bin/env python3
# -*- mode: python; python-indent: 4 -*-

import influxdb_datalogger
from influxdb_datalogger import *
from influxdb import InfluxDBClient
import os
import copy

class InfluxWriter(DatabaseWriter):
    def __init__(self, influx_ip, influx_port, database_name="nso"):
        self.influx_ip = influx_ip
        self.influx_port = influx_port
        self.database_name = database_name

    def write_data(self, datalogger: DataLogger):
        client = InfluxDBClient(self.influx_ip, self.influx_port, database=self.database_name)
        client.create_database(self.database_name)
        success = client.write_points(datalogger.dataset)
        if success:
            print("Test data written to influxdb")
        else:
            print("Failed to write test-data to influxdb")


tags = (
    influxdb_datalogger.Tag("hostname"),
    influxdb_datalogger.Tag("user")
    )

tags_map = influxdb_datalogger.TagMap.build(
    tuple(itertools.chain(*zip(tags,
        (
            socket.gethostname(),
            "ulrik"
        )
    )))
)


writer = InfluxWriter('localhost', 8086, database_name='nso')
if_logger = influxdb_datalogger.DataLogger(writer)


f = influxdb_datalogger.Field("counter")
fm = influxdb_datalogger.FieldMap.build(f, value)
m = influxdb_datalogger.Measurement(name, f, *tags)
if_logger.log(m, fm, tags_map)

f = influxdb_datalogger.Field("gauge")
fm = influxdb_datalogger.FieldMap.build(f, value)
m = influxdb_datalogger.Measurement(name, f, *tags)
if_logger.log(m, fm, tags_map)


if_logger.write_data()
