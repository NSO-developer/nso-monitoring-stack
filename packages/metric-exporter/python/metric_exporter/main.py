# -*- mode: python; python-indent: 4 -*-
# An applicaton for testing the bgworker library.
# We set up three different background processes
# - tbgw - uses default config to only run when HA is disabled or HA-mode=master
# - tbgw-ha-always: should always run, even when HA-mode=none
# - tbgw-ha-secondary: runs when HA-mode=secondary/slave

import logging
import random
import sys
import json
import socket

from .influx_writer import InfluxWriter
import influxdb_datalogger

import time

import prometheus_client

import ncs
from ncs.application import Service

from bgworker import background_process

from _ncs import events, maapi, stream_connect

try:
    events.HA_INFO_IS_PRIMARY
except AttributeError:
    _HA_PRIMARY_NAME = 'master'
    _HA_SECONDARY_NAME = 'slave'
else:
    _HA_PRIMARY_NAME = 'primary'
    _HA_SECONDARY_NAME = 'secondary'


def bg_worker(config_path):
    log = logging.getLogger()
    log.info(f"TBGW starting on YANG: {config_path}")

    counters_and_gauges = dict()

    with ncs.maapi.single_read_trans('bgworker', 'system', db=ncs.OPERATIONAL) as oper_trans_read:
        root = ncs.maagic.get_root(oper_trans_read)
        update_period= root.metric_exporter.update_period
        prometheus_enabled = root.metric_exporter.prometheus.enabled
        if prometheus_enabled:
            prometheus_port = root.metric_exporter.prometheus.port
        influxdb_enabled = root.metric_exporter.influxdb.enabled
        if influxdb_enabled:
            influxdb_host = root.metric_exporter.influxdb.host
            influxdb_port = root.metric_exporter.influxdb.port
            influx_database = root.metric_exporter.influxdb.database
            log.info(f"Influxdb extra tags: {root.metric_exporter.influxdb.extra_tags}")
            new_tag = influxdb_datalogger.Tag("hostname")
            tags = (new_tag,)
            tags_values = (new_tag, socket.gethostname())
            for item in root.metric_exporter.influxdb.extra_tags:
                new_tag = influxdb_datalogger.Tag(item.name)
                tags = (new_tag,)+tags
                tags_values = (new_tag, item.value)+tags_values
            log.info(f"Influxdb extra tag tags: {tags} tag_values: {tags_values}")
            tags_map = influxdb_datalogger.TagMap.build(*tags_values)
            log.info(f"Influxdb init done")

    log.info(f"Exporting to: {'prometheus' if prometheus_enabled else ''}  {'influxdb' if influxdb_enabled else ''}")


    if prometheus_enabled:
        prometheus_client.start_http_server(prometheus_port)

    if influxdb_enabled:
        writer = InfluxWriter(influxdb_host, influxdb_port, database_name=influx_database)
        if_logger = influxdb_datalogger.DataLogger(writer)

    def recv_all_and_close(t, c_sock, c_id):
        data = ''
        while True:
            buf = c_sock.recv(4096)
            if buf:
                data += buf.decode('utf-8')
            else:
                c_sock.close()
                return data, t.maapi.save_config_result(c_id)

    def read_config(t, path):
        dev_flags = (maapi.CONFIG_JSON | maapi.CONFIG_OPER_ONLY)
        c_id = t.save_config(dev_flags, path)
        c_sock = socket.socket()
        (ncsip, ncsport) = t.maapi.msock.getpeername()
        stream_connect(c_sock, c_id, 0, ncsip, ncsport)
        return recv_all_and_close(t, c_sock, c_id);

    def handle_gauge_counter(path, value):
        name = '_'.join(path).replace("-", "_")
        #log.info(f"Value {name} {type(value)}: {value}")
        if "counter" in name:
            handle_counter(name, value)
        else:
            handle_gauge(name, value)

    def handle_counter(name, value):
        if prometheus_enabled:
            if name in counters_and_gauges.keys():
                increment = value - counters_and_gauges[name]["last_count"]
                counters_and_gauges[name]["last_count"] = value
            else:
                increment = value
                counters_and_gauges[name] = dict()
                counters_and_gauges[name]["last_count"] = value
                counters_and_gauges[name]["counter"] = prometheus_client.Counter(name, "NSO counter")
            counters_and_gauges[name]["counter"].inc(increment)
        if influxdb_enabled:
            f = influxdb_datalogger.Field("counter")
            fm = influxdb_datalogger.FieldMap.build(f, value)
            m = influxdb_datalogger.Measurement(name, f, *tags)
            if_logger.log(m, fm, tags_map)

    def handle_gauge(name, value):
        if prometheus_enabled:
            if name not in counters_and_gauges.keys():
                counters_and_gauges[name] = dict()
                counters_and_gauges[name]["gauge"] = prometheus_client.Gauge(name, "NSO gauge")
            counters_and_gauges[name]["gauge"].set(value)

        if influxdb_enabled:
            f = influxdb_datalogger.Field("gauge")
            fm = influxdb_datalogger.FieldMap.build(f, value)
            m = influxdb_datalogger.Measurement(name, f, *tags)
            if_logger.log(m, fm, tags_map)

    def update_prometheus_with_metrics(prometheus_data, path, metric):
        for key, value in metric.items():
            if ':' in key:
               key = key.split(':')[1]
            tmp_path = path.copy()
            tmp_path.append(key)
            if type(value) is dict:
                update_prometheus_with_metrics(prometheus_data, tmp_path, value)
            elif type(value) is list:
                for v in value:
                    if 'rate' in v.keys():
                        handle_gauge_counter(tmp_path+[v['name']],
                                             float(v['rate']))
                    elif 'value' in v.keys():
                        handle_gauge_counter(tmp_path+[v['name']],
                                             float(v['value']))
                    else:
                        #log.info(f"Unhandled dict value {v}")
                        pass
            elif type(value) is int:
                handle_gauge_counter(tmp_path, value)
            elif type(value) is str:
                try:
                    handle_gauge_counter(tmp_path, int(value))
                except ValueError:
                    pass
            else:
                log.error(f"Unhandled value {tmp_path} {type(value)}: {value}")

    while True:
        with ncs.maapi.single_read_trans('bgworker', 'system', db=ncs.OPERATIONAL) as oper_trans_read:
            c = read_config(oper_trans_read, "/metric")
            json_result = json.loads(c[0])
            #log.info(f"json_result: {json.dumps(json_result, indent=4)}")
            update_prometheus_with_metrics(1, [], json_result['data']['tailf-ncs:metric'])
        if influxdb_enabled:
            if_logger.write_data()
        time.sleep(update_period)


class Main(ncs.application.Application):
    def setup(self):
        self.log.info('Main RUNNING')
        self.worker = background_process.Process(self, bg_worker, ["bgw"], config_path='/metric-exporter/enabled',
                                                 ha_when=_HA_PRIMARY_NAME)
        self.worker.start()

    def teardown(self):
        self.log.info('Main FINISHED')
        self.worker.stop()
