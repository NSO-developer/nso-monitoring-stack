# -*- mode: python; python-indent: 4 -*-
import ncs
from ncs.application import Service
import time
import _ncs
import _ncs.dp as dp
import logging
import os
import select
import threading
import socket

def start_dp(cp_name, log):
    log.info(f"start_dp pid: {os.getpid()}")
    daemon = threading.Thread(target=daemon_thread, args=(cp_name, log,))
    daemon.start()
    return daemon

def daemon_thread(cp_name, log):
    ctx = dp.init_daemon(cp_name)
    ctlsock = socket.socket(socket.AF_INET, socket.SOCK_STREAM, 0)
    wrksock = socket.socket(socket.AF_INET, socket.SOCK_STREAM, 0)
    try:
        dp.connect(ctx,
                   ctlsock,
                   dp.CONTROL_SOCKET,
                   '127.0.0.1',
                   _ncs.NCS_PORT,
                   '/')
        dp.connect(ctx,
                   wrksock,
                   dp.WORKER_SOCKET,
                   '127.0.0.1',
                   _ncs.NCS_PORT,
                   '/')

        tcb = TransCbs(wrksock)
        dp.register_trans_cb(ctx, tcb)
        dcb = DataCallbacks(log)
        dp.register_data_cb(ctx, cp_name, dcb)
        dp.register_done(ctx)
        _r = [ctlsock, wrksock]
        _w = []
        _e = []

        log.info("daemon thread starting")
        while True:
            (r, w, e) = select.select(_r, _w, _e, 1)
            for rs in r:
                if rs.fileno() == ctlsock.fileno():
                    try:
                        dp.fd_ready(ctx, ctlsock)
                    except (_ncs.error.Error) as e:
                        if e.confd_errno is not _ncs.ERR_EXTERNAL:
                            raise e
                elif rs.fileno() == wrksock.fileno():
                    try:
                        dp.fd_ready(ctx, wrksock)
                    except (_ncs.error.Error) as e:
                        if e.confd_errno is not _ncs.ERR_EXTERNAL:
                            raise e
    except Exception as e:
        log.error(str(e))
    finally:
        log.info("daemon thread exited")
        ctlsock.close()
        wrksock.close()
        dp.release_daemon(ctx)


class TransCbs(object):
    def __init__(self, workersocket):
        self._workersocket = workersocket

    def cb_init(self, tctx):
        try:
            dp.trans_set_fd(tctx, self._workersocket)
            return _ncs.CONFD_OK
        except:
            traceback.print_exc()

    def cb_finish(self, tctx):
        return _ncs.CONFD_OK


states = {
            'ulrik': 42,
            'cb_create': 0
}

class DataCallbacks(object):
    """
        DataCallbacks implements the DP callback functions.
    """
    def __init__(self, log):
        self.log = log
        self.log.info("Datacallback setup")
        self.states = None

    #
    # Data callbacks
    #
    def cb_get_elem(self, tctx, kp):
        k = str(kp[1][0])
        _ncs.dp.data_reply_value(tctx, _ncs.Value(self.states[k],
                                                  _ncs.C_UINT32))
        return _ncs.CONFD_OK

    def cb_get_next(self, tctx, kp, next):
        if next == -1:
            self.states = states.copy()
            self.keys = list(self.states.keys())
        n = next+1
        if n < len(self.keys):
            key = [ _ncs.Value(self.keys[n], _ncs.C_BUF) ]
            _ncs.dp.data_reply_next_key(tctx, key, n)
        else:
            _ncs.dp.data_reply_next_key(tctx, None, 0)
        return _ncs.CONFD_OK


# ------------------------
# SERVICE CALLBACK EXAMPLE
# ------------------------
class ServiceCallbacks(Service):

    # The create() callback is invoked inside NCS FASTMAP and
    # must always exist.
    @Service.create
    def cb_create(self, tctx, root, service, proplist):
        global states
        states['cb_create'] += 1
        self.log.info('Service create(service=', service._path, ')')
        time.sleep(service.delay/1000)
        vars = ncs.template.Variables()
        for t in service.template:
            #vars.add('DUMMY', '127.0.0.1')
            template = ncs.template.Template(service)
            template.apply(t, vars)



# ---------------------------------------------
# COMPONENT THREAD THAT WILL BE STARTED BY NCS.
# ---------------------------------------------
class Main(ncs.application.Application):
    def setup(self):
        # The application class sets up logging for us. It is accessible
        # through 'self.log' and is a ncs.log.Log instance.
        self.log.info('Main RUNNING')

        # Service callbacks require a registration for a 'service point',
        # as specified in the corresponding data model.
        #
        self.register_service('python-service-servicepoint', ServiceCallbacks)

        # If we registered any callback(s) above, the Application class
        # took care of creating a daemon (related to the service/action point).

        # When this setup method is finished, all registrations are
        # considered done and the application is 'started'.
        start_dp('python-service-dp', self.log)

    def teardown(self):
        # When the application is finished (which would happen if NCS went
        # down, packages were reloaded or some error occurred) this teardown
        # method will be called.

        self.log.info('Main FINISHED')
