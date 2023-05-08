# -*- mode: python; python-indent: 4 -*-

import atexit
import copy
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
import logging
import os
import socket
import sys
from threading import Thread, Event
import time
import traceback

import ncs
import _ncs
import psutil
import requests

from .influx_writer import InfluxWriter
import influxdb_datalogger


# Last metrics variable
metrics = None

log = logging.getLogger()


def find_ncs_smp_parent_process(me):
    p = me.parent()
    if p is not None:
        # Name diffs between official and local build.
        if p.name() in ['ncs.smp', 'beam.smp']:
            return p
        return find_ncs_smp_parent_process(p)
    else:
        return None


def find_ncs_parent():
    me = psutil.Process(pid=os.getpid())
    return find_ncs_smp_parent_process(me)


def find_java_python_processes(ncs):
    erl_child_setup = ncs.children()[0] # Assuming only 1 child
    assert erl_child_setup.name() == 'erl_child_setup' 
    java = None
    python = []
    # TODO: Make the subprocess discovery generic for all types (java, python)
    for child in erl_child_setup.children():
        name = child.name()
        if name == 'java':
            if java is not None:
                log.warning("More than one java process found.")
            java = child
        if name[:6] == 'python':
            pn = get_python_service_name(child)
            pnc = []
            python.append((pn, (child, pnc)))
            for sp in child.children():
                pnc.append((f'{pn}-{sp.pid}', sp))
    if java is None:
        log.warning("No java-vm was discovered.")
    return (java, python)


def get_connections(p):
    try:
        return len(p.connections())
    except OSError as e:
        if e.errno == 38:
            return len(p.connections())
        else:
            return None


def to_dict(metrics):
    return {f: getattr(metrics, f) for f in metrics._fields}


metrics_functions = {
        ('gauge', 'mem'): lambda p: p.memory_info()[0]/1048576,  # RSS MB
        ('gauge', 'cpu'): lambda p: p.cpu_percent(),
        ('gauge', 'conn'): lambda p: get_connections(p),
        ('gauge', 'num_fds'): lambda p: p.num_fds(),
        ('gauge', 'num_threads'): lambda p: p.num_threads(),
        ('gauge', 'mem_info'): lambda p: to_dict(p.memory_info()),
        ('counter', 'num_ctx_switches'): lambda p: to_dict(p.num_ctx_switches()),
        ('counter', 'cpu_times'): lambda p: to_dict(p.cpu_times()),
}
if sys.platform != 'darwin':
    metrics_functions.update({
        ('counter', 'io_counters'): lambda p: to_dict(p.io_counters()),
    })


def get_process_info(p):
    with p.oneshot():
        return { k: f(p) for k, f in metrics_functions.items() }


def get_python_service_name(p):
    try:
        cmdline = p.cmdline()
        i = cmdline.index('-i')
        return cmdline[i+1]
    except ValueError:
        pass
    except IndexError:
        pass
    return p.name()


def add_metrics(d, s):
    for k,v in d.items():
        if type(v) == dict:
            add_metrics(v, s[k])
        else:
            d[k] = v + s[k]


def get_process_metrics(ncs, java, python):
    update = False
    m = {
        'ncs': get_process_info(ncs)
    }
    if java is not None:
        try:
            m['java-vm'] = get_process_info(java)
        except psutil.NoSuchProcess as e:
            update = True
    for pn, (p, pnc) in python:
        try:
            pm = get_process_info(p)
            m[pn] = pm
            m_pkg = copy.deepcopy(pm)
            for c_pn, c_p in pnc:
                try:
                    pm = get_process_info(c_p)
                    m[c_pn] = pm
                    add_metrics(m_pkg, pm)
                except psutil.NoSuchProcess as e:
                    update = True
            m[pn].update({ (k[0],f'{k[1]}_pkg'): v for k,v in m_pkg.items() })
        except psutil.NoSuchProcess as e:
            update = True
    return m, update


def find_by_name(n, l):
    if l is None:
        return None
    for o in l:
        ln, sp = o
        if n == ln:
            return o
    return None


def is_identical(sb, sp):
    sbn, sbc = sb
    spn, spc = sp
    # TODO: Improve check of subprocess pids.
    if sbn != spn or len(sbc) != len(spc):
        return False
    return True


def update_processes(ncs, jvm, pyvms):
    # TODO: More intelligent process update (to avoid break in metrics)
    jp, pps = find_java_python_processes(ncs)
    if jvm != jp:
        jvm = jp
        log.info("jvm updated")
    npyvms = []
    if pyvms is not None:
        for n, sb in pyvms:
            o = find_by_name(n, pps)
            if o is None:
                log.info("pyvm removed " + n)
            else:
                ln, sp = o
                if not is_identical(sb, sp):
                    npyvms.append(o)
                    pps.remove(o)
                    log.info("pyvm updated " + n)
                else:
                    # Keep existing
                    #log.info("pyvm keep " + n)
                    pps.remove(o)
                    npyvms.append((n, sb))
    if len(pps):
        log.info("New processes: " + str(pps))
        npyvms += pps
    return jvm, npyvms


def decrypt_string(node, value):
    """Decrypts an encrypted tailf:aes-cfb-128-encrypted-string type leaf

    :param node: any maagic Node
    :param value: the encrypted leaf value
    """
    if value is None:
        return None

    if isinstance(node._backend, ncs.maagic._TransactionBackend):
        node._backend.maapi.install_crypto_keys()
    elif isinstance(node._backend, ncs.maagic._MaapiBackend):
        node._backend.install_crypto_keys()
    else:
        raise ValueError("Unknown MaagicBackend for leaf")

    return _ncs.decrypt(value)  # pylint: disable=no-member


class MetricsCollector(Thread):
    def __init__(self):
        super().__init__()
        self._running = False
        self.log = logging.getLogger()
        self.event = Event()
        self.influxdb_enabled = False
        self.influxdb_host = None
        self.influxdb_port = None
        self.influxdb_database = None
        self.influxdb_tags_map = None
        self.influxdb_username = None
        self.influxdb_password = None
        self.prometheus_enabled = False
        self.prometheus_port = None
        self.update_period = 5

    def stop(self):
        self._running = False
        self.event.set()
        self.join()

    def run(self):
        global ncs_process, jvm, pyvms, metrics
        self._running = True
        last_process_update = time.monotonic()
        self.log.info("collector started")
        while self._running:
            self.get_exporter_config()
            if ncs_process is None:
                ncs_process = find_ncs_parent()
                jvm, pyvms = update_processes(ncs_process, jvm, pyvms)
            # Update metrics
            metrics, update = get_process_metrics(ncs_process, jvm, pyvms)

            t = time.monotonic()
            if self.influxdb_enabled:
                self.update_influxdb(metrics)

            if update or t-last_process_update >= 15:
                last_process_update = t
                jvm, pyvms = update_processes(ncs_process, jvm, pyvms)
            self.event.wait(self.update_period)
        self.log.info("collector exited")

    def update_influxdb(self, metrics):
        writer = InfluxWriter(self.influxdb_host, self.influxdb_port,
                              database_name=self.influxdb_database,
                              username=self.influxdb_username,
                              password=self.influxdb_password)
        if_logger = influxdb_datalogger.DataLogger(writer)

        for process, measurements in metrics.items():
            fields = []
            values = []
            for measurement, value in measurements.items():
                if type(value) is dict:
                    for value_type, detailed_value in value.items():
                        fields.append(influxdb_datalogger.Field(f"{measurement[1]}-{value_type}"))
                        values.append(detailed_value)

                else:
                    fields.append(influxdb_datalogger.Field(measurement[1]))
                    values.append(value)

            fields = tuple(fields)
            fieldsmap = tuple([j for i in zip(fields, values) for j in i])

            fm = influxdb_datalogger.FieldMap.build(*fieldsmap)
            process_tag = influxdb_datalogger.Tag("process")

            process_tags = (process_tag,)+self.influxdb_tags
            process_tag_values = (process_tag, process)+self.influxdb_tags_values
            process_tag_map = influxdb_datalogger.TagMap.build(*process_tag_values)
            m = influxdb_datalogger.Measurement("process_exporter", *fields, *process_tags)
            if_logger.log(m, fm, process_tag_map)
        try:
            if_logger.write_data()
        except Exception as e:  # This is the correct syntax
            self.log.exception(f"Got exception")

    def get_exporter_config(self):
        with ncs.maapi.single_read_trans('admin', 'system', db=ncs.OPERATIONAL) as oper_trans_read:
            root = ncs.maagic.get_root(oper_trans_read)
            exporter = root.process_exporter.exporter
            self.update_period = exporter.update_period
            self.prometheus_enabled = exporter.prometheus.enabled
            if self.prometheus_enabled:
                self.log.debug(f"Prometheus enabled")
                self.prometheus_port = exporter.prometheus.port
            self.influxdb_enabled = exporter.influxdb.enabled
            if self.influxdb_enabled:
                self.log.debug(f"Influxdb enabled")
                self.influxdb_host = exporter.influxdb.host
                self.influxdb_port = exporter.influxdb.port
                self.influxdb_database = exporter.influxdb.database
                self.influxdb_username = exporter.influxdb.username
                if exporter.influxdb.password is None:
                    self.influxdb_password = None
                else:
                    self.influxdb_password = decrypt_string(root, exporter.influxdb.password)

                hostname_tag = influxdb_datalogger.Tag("hostname")
                self.influxdb_tags = (hostname_tag,)
                self.influxdb_tags_values = (hostname_tag, socket.gethostname())
                for item in exporter.influxdb.extra_tags:
                    new_tag = influxdb_datalogger.Tag(item.name)
                    self.influxdb_tags = (new_tag,)+self.influxdb_tags
                    self.influxdb_tags_values = (new_tag, item.value)+self.influxdb_tags_values


class MetricsServer(Thread):
    def __init__(self):
        super().__init__()
        self._running = False
        self.log = logging.getLogger()

        hostName = "0.0.0.0"
        serverPort = 9099
        self.webServer = HTTPServer((hostName, serverPort), MetricsHandler)

    def stop(self):
        self.webServer.shutdown()
        self.join()
        self._running = False

    def run(self):
        self._running = True
        self.webServer.serve_forever()


ncs_process = None
jvm = None
pyvms = None


class MetricsHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global metrics
        try:
            if self.path == '/metrics':
                m = metrics
                if m:
                    self.send_response(200)
                    self.send_header("Content-type", "text/plain")
                    self.end_headers()
                    for pn, pmetrics in m.items():
                        for mn, value in pmetrics.items():
                            name = f'nso_proc_{mn[1]}'
                            self.wfile.write(bytes(f"# TYPE {name} {mn[0]}\n", "utf-8"))
                            if type(value) == dict:
                                for k,v in value.items():
                                    self.wfile.write(bytes(f'{name}{{name="{pn}" value="{k}"}} {v}\n', "utf-8"))
                            else:
                                self.wfile.write(bytes(f'{name}{{name="{pn}"}} {value}\n', "utf-8"))
                else:
                    self.send_response(400)
            else:
                self.send_response(200)
                self.send_header("Content-type", "text/html")
                self.end_headers()
                self.wfile.write(bytes("""<html>
                <head><title>NSO Process Exporter</title></head>
                <body>
                <h1>NSO Process Exporter</h1>
                <p><a href="/metrics">Metrics</a></p>
                </body>
                </html>\n""", "utf-8"))
        except Exception as e:
            self.send_response(500)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            self.wfile.write(bytes(f"{type(e)}\n", "utf-8"))
            self.wfile.write(bytes(f"{e}\n", "utf-8"))
            self.wfile.write(bytes(f"{pyvms}\n", "utf-8"))
            self.wfile.write(bytes(f"{traceback.format_exc()}\n", "utf-8"))


class Main(ncs.application.Application):
    def setup(self):
        self.log.info(f'Main RUNNING')
        self.metrics_server = MetricsServer()
        self.metrics_server.start()
        self.metrics_collector = MetricsCollector()
        self.metrics_collector.start()

    def teardown(self):
        self.metrics_server.stop()
        self.metrics_collector.stop()
        self.log.info('Main FINISHED')
