# -*- mode: python; python-indent: 4 -*-
"""A micro-framework for running background processes in Cisco NSO Python VM.

Running any kind of background workers in Cisco NSO can be rather tricky. This
will help you out! Just define a function that does what you want and create a
Process instance to run it!

We react to:
 - background worker process dying (will restart it)
 - NCS package events, like redeploy
 - configuration changes (disable the background worker)
 - HA events (if we are a secondary/slave)
"""
import enum
import logging
import logging.handlers
import multiprocessing
import os
import select
import socket
import threading
import time
import traceback
from typing import Any, Callable, ClassVar, Iterable, Optional, Tuple, Union, cast

from _ncs import events
import ncs

try:
    from ncs.cdb import Subscriber          # type: ignore
except ImportError:
    from ncs.experimental import Subscriber # type: ignore


# NSO 6.0 renamed the HA master and slave roles to primary and secondary, and
# also changed the data models for operational data and HA actions to match.
# The code in bgworker uses the new terms and is backwards compatible - it will
# run on NSO versions older than 6.0 with "incorrect" role names.
class HaWhen(enum.Enum):
    PRIMARY = 'primary'
    SECONDARY = 'secondary'
    ALWAYS = 'always'
try:
    _HA_INFO_IS_PRIMARY = events.HA_INFO_IS_PRIMARY
    _HA_INFO_SECONDARY_INITIALIZED = events.HA_INFO_SECONDARY_INITIALIZED
    _TFNM_PREFIX = 'tfnm2'
except AttributeError:
    _HA_INFO_IS_PRIMARY = events.HA_INFO_IS_MASTER
    _HA_INFO_SECONDARY_INITIALIZED = events.HA_INFO_SLAVE_INITIALIZED
    _TFNM_PREFIX = 'tfnm'


def _get_handler_impls(logger: logging.Logger) -> Iterable[logging.Handler]:
    """For a given Logger instance, find the registered handlers.

    A Logger instance may have handlers registered in the 'handlers' list.
    Usually there is one handler registered to the Root Logger.
    This function uses the same algorithm as Logger.callHandlers to find
    all relevant handlers.
    """

    c: Union[logging.Logger, logging.PlaceHolder, None] = logger
    while c is not None:
        if isinstance(c, logging.Logger):
            for hdlr in c.handlers:
                yield hdlr
            if not c.propagate:
                c = None    #break out
            else:
                c = c.parent
        elif isinstance(c, logging.PlaceHolder):
            # we silence mypy here, as logging.PlaceHolder _has_ the parent
            # attribute, but it is defined at runtime
            c = c.parent    # type: ignore

def _bg_wrapper(pipe_unused, log_q, log_config_q, log_level, bg_fun, *bg_fun_args):
    """Internal wrapper for the background worker function.

    Used to set up logging via a QueueHandler in the child process. The other end
    of the queue is observed by a QueueListener in the parent process.
    """
    queue_hdlr = logging.handlers.QueueHandler(log_q)
    root = logging.getLogger()
    root.setLevel(log_level)
    root.addHandler(queue_hdlr)

    # thread to monitor log level changes and reconfigure the root logger level
    log_reconf = LogReconfigurator(log_config_q, root)
    log_reconf.start()

    try:
        bg_fun(*bg_fun_args)
    except Exception as e:
        root.error('Unhandled error in {} - {}: {}'.format(bg_fun.__name__, type(e).__name__, e))
        root.debug(traceback.format_exc())


class LogReconfigurator(threading.Thread):
    def __init__(self, q, log_root):
        super(LogReconfigurator, self).__init__()
        self.daemon = True
        self.q = q
        self.log_root = log_root

    def run(self):
        while True:
            k, v = self.q.get()
            if k == 'exit':
                return

            self.log_root.setLevel(v)

    def stop(self):
        self.q.put(('exit', None))


class Process(threading.Thread):
    """Supervisor for running the main background process and reacting to
    various events

    We hardcode the decision to use the 'spawn' method for worker processes.
    While this is heavier than a fork, it is the safest method that make sure
    *no locks* can be "forked" over to the worker while not being unlocked. We
    spent a lot of time chasing down deadlocks in the logging and file access
    modules.
    """

    # The context is actually a singleton, so it is safe to declare this as a
    # class attribute once and access it where needed.
    # TODO: mypy 0.670 (currently installed) needs the additional cast to get
    # the annotaton right
    mp_ctx: ClassVar[multiprocessing.context.SpawnContext] = cast(multiprocessing.context.SpawnContext, multiprocessing.get_context('spawn'))

    def __init__(self, app, bg_fun: Callable[..., Any], bg_fun_args: Optional[Tuple[Any, ...]] = None, config_path=None, ha_when: HaWhen = HaWhen.PRIMARY, backoff_timer=1, run_during_upgrade=False):
        super(Process, self).__init__()
        self.app = app
        self.bg_fun = bg_fun
        self.bg_fun_args = bg_fun_args or ()
        self.config_path = config_path
        self.ha_when = ha_when
        self.backoff_timer = backoff_timer
        self.parent_pipe = None
        self.run_during_upgrade = run_during_upgrade

        self.log = app.log
        self.name = "{}.{}".format(self.app.__class__.__module__,
                                   self.app.__class__.__name__)
        self.log.info("{} supervisor starting".format(self.name))

        self.vmid = self.app._ncs_id

        self.q = self.new_queue()

        # start the config subscriber thread
        if self.config_path is not None:
            self.config_subscriber = Subscriber(app=self.app, log=self.log)
            subscriber_iter = ConfigSubscriber(self.q, self.config_path)
            subscriber_iter.register(self.config_subscriber)
            self.config_subscriber.start()

        # start the HA event listener thread
        self.event_listener = EventListener(app=self.app, q=self.q)
        self.event_listener.start()

        # start the logging QueueListener thread
        hdlrs = list(_get_handler_impls(self.app._logger))
        self.log_queue = self.new_queue()
        self.queue_listener = logging.handlers.QueueListener(self.log_queue, *hdlrs, respect_handler_level=True)
        self.queue_listener.start()
        self.current_log_level = self.app._logger.getEffectiveLevel()

        # start log config CDB subscriber
        self.log_config_q = self.new_queue()
        self.log_config_subscriber = Subscriber(app=self.app, log=self.log)
        log_subscriber_iter = LogConfigSubscriber(self.log_config_q, self.vmid)
        log_subscriber_iter.register(self.log_config_subscriber)
        self.log_config_subscriber.start()

        self.worker: Optional[multiprocessing.context.SpawnProcess] = None

        self.in_upgrade = False

        # Read initial configuration, using two separate transactions
        with ncs.maapi.Maapi() as m:
            with ncs.maapi.Session(m, '{}_supervisor'.format(self.name), 'system'):
                # in the 1st transaction read config data from the 'enabled' leaf
                with m.start_read_trans() as t_read:
                    if config_path is not None:
                        enabled = t_read.get_elem(self.config_path)
                        self.config_enabled = bool(enabled)
                    else:
                        # if there is no config_path we assume the process is always enabled
                        self.config_enabled = True

                # In the 2nd transaction read operational data regarding HA.
                # This is an expensive operation invoking a data provider, thus
                # we don't want to incur any unnecessary locks
                with m.start_read_trans(db=ncs.OPERATIONAL) as oper_t_read:
                    # check if HA is enabled
                    if oper_t_read.exists(f"/tfnm:ncs-state/{_TFNM_PREFIX}:ha"):
                        self.ha_enabled = True
                    else:
                        self.ha_enabled = False

                    # determine HA state if HA is enabled
                    if self.ha_enabled:
                        ha_mode = str(ncs.maagic.get_node(oper_t_read, f'/tfnm:ncs-state/{_TFNM_PREFIX}:ha/{_TFNM_PREFIX}:mode'))
                        # always use primary/secondary names, makes the internals and logging consistent with input configuration
                        if ha_mode == 'master':
                            self.ha_mode = 'primary'
                        elif ha_mode == 'slave':
                            self.ha_mode = 'secondary'
                        else:
                            self.ha_mode = ha_mode


    def run(self):
        self.app.add_running_thread(self.name + ' (Supervisor)')

        has_run = False
        while True:
            try:
                if self.config_enabled:
                    if self.in_upgrade and not self.run_during_upgrade:
                        should_run = False
                    elif not self.ha_enabled:
                        should_run = True
                    else:
                        if self.ha_when == HaWhen.ALWAYS or self.ha_when.value == self.ha_mode:
                            should_run = True
                        else:
                            should_run = False
                else:
                    should_run = False

                if should_run:
                    if self.worker is None or not self.worker.is_alive():
                        self.log.info("Background worker process should run but is not running, starting")
                        self.worker_stop()
                        if has_run:
                            time.sleep(self.backoff_timer)
                        self.worker_start()
                    has_run = True
                else:
                    if not self.config_enabled:
                        self.log.info("Background worker is disabled")
                    elif self.in_upgrade and not self.run_during_upgrade:
                        self.log.info("Background worker disabled while NSO is in upgrade mode")
                    elif self.ha_enabled:
                        self.log.info(f"Background worker will not run when HA-when={self.ha_when.value} and HA-mode={self.ha_mode}")

                    if self.worker is not None and self.worker.is_alive():
                        self.log.info("Background worker process is running but should not run, stopping")
                        self.worker_stop()
                    has_run = False

                # check for input
                waitable_rfds = [self.q._reader] # type: ignore
                if should_run and self.parent_pipe is not None:
                    waitable_rfds.append(self.parent_pipe)

                rfds, _, _ = select.select(waitable_rfds, [], [])
                for rfd in rfds:
                    if rfd == self.q._reader: # type: ignore
                        k, v = self.q.get()

                        if k == 'exit':
                            return
                        elif k == 'enabled':
                            self.config_enabled = v
                        elif k == "ha-mode":
                            self.ha_mode = v
                        elif k == "upgrade":
                            if v in ('commited', 'aborted'):
                                self.in_upgrade = False
                            else:
                                self.in_upgrade = True
                        elif k == 'restart':
                            self.log.info("Restarting the background worker process")
                            self.worker_stop()
                            self.worker_start()

                    if rfd == self.parent_pipe:
                        # getting a readable event on the pipe should mean the
                        # child is dead - wait for it to die and start again
                        # we'll restart it at the top of the loop
                        self.log.info("Child process died")
                        # silence mypy on the next line, v0.670 can't deal
                        assert isinstance(self.worker, multiprocessing.process.BaseProcess) # type: ignore
                        if self.worker.is_alive():
                            self.worker.join()

            except Exception as e:
                self.log.error('Unhandled exception in the supervisor thread: {} ({})'.format(type(e).__name__, e))
                self.log.error(traceback.format_exc())
                time.sleep(1)


    def stop(self):
        """stop is called when the supervisor thread should stop and is part of
        the standard Python interface for threading.Thread
        """
        # stop the HA event listener
        self.log.debug("{}: stopping HA event listener".format(self.name))
        self.event_listener.stop()

        # stop config CDB subscriber
        self.log.debug("{}: stopping config CDB subscriber".format(self.name))
        if self.config_path is not None:
            self.config_subscriber.stop()

        # stop log config CDB subscriber
        self.log.debug("{}: stopping log config CDB subscriber".format(self.name))
        self.log_config_subscriber.stop()

        # stop the logging QueueListener
        self.log.debug("{}: stopping logging QueueListener".format(self.name))
        self.queue_listener.stop()

        # stop us, the supervisor
        self.log.debug("{}: stopping supervisor thread".format(self.name))

        self.q.put(('exit', None))
        if self.is_alive():
            self.join()
        self.app.del_running_thread(self.name + ' (Supervisor)')

        # stop the background worker process
        self.log.debug("{}: stopping background worker process".format(self.name))
        self.worker_stop()


    def worker_start(self):
        """Starts the background worker process
        """
        self.log.info("{}: starting the background worker process".format(self.name))
        # Instead of using the usual worker thread, we use a separate process here.
        # This allows us to terminate the process on package reload / NSO shutdown.

        # using multiprocessing.Pipe which is shareable across a spawned
        # process, while os.pipe only works, per default over to a forked
        # child
        self.parent_pipe, child_pipe = self.mp_ctx.Pipe(duplex=True)

        # Instead of calling the bg_fun worker function directly, call our
        # internal wrapper to set up things like inter-process logging through
        # a queue.
        args = (child_pipe, self.log_queue, self.log_config_q, self.current_log_level, self.bg_fun, *self.bg_fun_args)
        self.worker = self.mp_ctx.Process(target=_bg_wrapper, args=args)
        self.worker.start()

        # close child pipe in parent so only child is in possession of file
        # handle, which means we get EOF when the child dies
        child_pipe.close()


    def worker_stop(self):
        """Stops the background worker process
        """
        if self.worker is None:
            self.log.info("{}: asked to stop worker but background worker does not exist".format(self.name))
            return
        if not self.worker.is_alive():
            self.log.info("{}: asked to stop worker but background worker is not running".format(self.name))
            return
        self.log.info("{}: stopping the background worker process".format(self.name))
        self.worker.terminate()
        try:
            self.worker.join(timeout=1)
        except AssertionError as exc:
            if str(exc) == "can only join a started process":
                pass
            else:
                raise exc
        if self.worker.is_alive():
            self.log.error("{}: worker not terminated on time, alive: {}  process: {}".format(self, self.worker.is_alive(), self.worker))


    def restart(self):
        """Restart the background worker process

        We ask the supervisor to restart
        """
        self.log.info("Requesting supervisor to restart the background worker process")
        self.q.put(('restart', None))

    def emergency_stop(self):
        """Stop the background worker process

        This only has effect while this package is running. Reloading packages
        or restarting NSO will start the background worker again (if enabled in
        configuration).
        """
        # Disable the worker immediately
        self.log.info("Requesting supervisor to stop the background worker process")
        self.q.put(('enabled', False))

    def is_running(self):
        """Returns true if the background worker has started and is alive"""
        return self.worker is not None and self.worker.is_alive()

    @classmethod
    def new_queue(cls) -> multiprocessing.Queue:
        """Create a new Queue object using the correct (shared) multiprocessing context"""
        return cls.mp_ctx.Queue()


class ConfigSubscriber(object):
    """CDB subscriber for background worker process

    It is assumed that there is an 'enabled' leaf that controls whether a
    background worker process should be enabled or disabled. Given the path to
    that leaf, this subscriber can monitor it and send any changes to the
    supervisor which in turn starts or stops the background worker process.

    The enabled leaf has to be a boolean where true means the background worker
    process is enabled and should run.
    """
    def __init__(self, q, config_path):
        self.q = q
        self.config_path = config_path

    def register(self, subscriber):
        subscriber.register(self.config_path, priority=101, iter_obj=self)

    def pre_iterate(self):
        return {'enabled': False}

    def iterate(self, keypath_unused, operation_unused, oldval_unused, newval, state):
        state['enabled'] = newval
        return ncs.ITER_RECURSE

    def should_post_iterate(self, state_unused):
        return True

    def post_iterate(self, state):
        self.q.put(("enabled", bool(state['enabled'])))


class LogConfigSubscriber(object):
    """CDB subscriber for python-vm logging level

    This subscribers monitors /python-vm/logging/level and
    /python-vm/logging/vm-levels{vmid}/level and propagates any changes to the
    child process which can then in turn log at the appropriate level. VM
    specific level naturally take precedence over the global level.
    """
    def __init__(self, q, vmid):
        self.q = q
        self.vmid = vmid

        # read in initial values for these nodes
        # a CDB subscriber only naturally gets changes but if we are to emit a
        # sane value we have to know what both underlying (global & vm specific
        # log level) are and thus must read it in first
        with ncs.maapi.single_read_trans('', 'system') as t_read:
            try:
                self.global_level = ncs.maagic.get_node(t_read, '/python-vm/logging/level')
            except Exception:
                self.global_level = None
            try:
                self.vm_level = ncs.maagic.get_node(t_read, '/python-vm/logging/vm-levels{{{}}}/level'.format(self.vmid))
            except Exception:
                self.vm_level = None

    def register(self, subscriber):
        subscriber.register('/python-vm/logging/level', priority=101, iter_obj=self)
        # we don't include /level here at the end as then we won't get notified
        # when the whole vm-levels list entry is deleted, we still get when
        # /levels is being set though (and it is mandatory so we know it will
        # always be there for whenever a vm-levels list entry exists)
        subscriber.register('/python-vm/logging/vm-levels{{{}}}'.format(self.vmid), priority=101, iter_obj=self)

    def pre_iterate(self):
        return {}

    def iterate(self, keypath, operation_unused, oldval_unused, newval, unused_state):
        if str(keypath[-3]) == "vm-levels":
            self.vm_level = newval
        else:
            self.global_level = newval
        return ncs.ITER_RECURSE

    def should_post_iterate(self, unused_state):
        return True

    def post_iterate(self, unused_state):
        # the log level enum in the YANG model maps the integer values to
        # python log levels quite nicely just by adding 1 and multiplying by 10
        configured_level = int(self.vm_level or self.global_level)
        new_level = (configured_level+1)*10
        self.q.put(("log-level", new_level))


class EventListener(threading.Thread):
    """Event Listener for HA & upgrade events

    The NSO notification API exposes various events. We are interested in the
    HA events, like HA-mode transitions, and events related to a system upgrade.
    We listen on that and forward relevant messages over the queue to the
    supervisor which can act accordingly.

    We use a WaitableEvent rather than a threading.Event since the former
    allows us to wait on it using a select loop. The events are received over a
    socket which can also be waited upon using a select loop, thus making it
    possible to wait for the two inputs we have using a single select loop.
    """
    def __init__(self, app, q):
        super(EventListener, self).__init__()
        self.app = app
        self.log = app.log
        self.q = q
        self.log.info('{} EventListener: init'.format(self))
        self.exit_flag = WaitableEvent()

    def run(self):
        self.app.add_running_thread(self.__class__.__name__ + ' (HA event listener)')

        self.log.info('run() HA event listener')
        mask = events.NOTIF_HA_INFO + events.NOTIF_UPGRADE_EVENT
        event_socket = socket.socket()
        events.notifications_connect(event_socket, mask, ip='127.0.0.1', port=ncs.PORT)
        while True:
            rl, _, _ = select.select([self.exit_flag, event_socket], [], [])
            if self.exit_flag in rl:
                event_socket.close()
                return

            event = events.read_notification(event_socket)
            if event['type'] == events.NOTIF_UPGRADE_EVENT:
                upgrade_event_map = {
                    1: 'init-started',
                    2: 'init-succeeded',
                    3: 'performed',
                    4: 'commited',
                    5: 'aborted'
                }
                et = upgrade_event_map[event['upgrade']['event']]
                self.q.put(('upgrade', et))

            elif event['type'] == events.NOTIF_HA_INFO:
                # Can this fail? Could we get a KeyError here? Afraid to catch it
                # because I don't know what it could mean.
                ha_notif_type = event['hnot']['type']

                # Always use primary/secondary names, makes the internals and
                # logging consistent with input configuration
                if ha_notif_type == _HA_INFO_IS_PRIMARY:
                    self.q.put(('ha-mode', 'primary'))
                elif ha_notif_type == events.HA_INFO_IS_NONE:
                    self.q.put(('ha-mode', 'none'))
                elif ha_notif_type == _HA_INFO_SECONDARY_INITIALIZED:
                    self.q.put(('ha-mode', 'secondary'))

    def stop(self):
        self.exit_flag.set()
        self.join()
        self.app.del_running_thread(self.__class__.__name__ + ' (HA event listener)')


class WaitableEvent:
    """Provides an abstract object that can be used to resume select loops with
    indefinite waits from another thread or process. This mimics the standard
    threading.Event interface."""
    def __init__(self):
        self._read_fd, self._write_fd = os.pipe()

    def wait(self, timeout=None):
        rfds, _, _ = select.select([self._read_fd], [], [], timeout)
        return self._read_fd in rfds

    def is_set(self):
        return self.wait(0)

    def isSet(self):
        return self.wait(0)

    def clear(self):
        if self.isSet():
            os.read(self._read_fd, 1)

    def set(self):
        if not self.isSet():
            os.write(self._write_fd, b'1')

    def fileno(self):
        """Return the FD number of the read side of the pipe, allows this
        object to be used with select.select()
        """
        return self._read_fd

    def __del__(self):
        os.close(self._read_fd)
        os.close(self._write_fd)


class EmergencyStop(ncs.dp.Action):
    def init(self, worker):  # pylint: disable=arguments-differ
        self.worker = worker

    @ncs.dp.Action.action
    def cb_action(self, uinfo, name, kp, action_input, action_output, t_read):
        self.worker.emergency_stop()
        action_output.result = 'Background worker temporarily stopped. Disable in configuration to make it permanent. Package reload or restart of NSO will start it again. To manually restart, issue the \'restart\' action.'


class RestartWorker(ncs.dp.Action):
    def init(self, worker):  # pylint: disable=arguments-differ
        self.worker = worker

    @ncs.dp.Action.action
    def cb_action(self, uinfo, name, kp, action_input, action_output, t_read):
        # Check if the worker is enabled in config. If not, then the user
        # should simply enable it and the worker will start. If it is enabled
        # but not running, it was likely stopped through emergency-stop and we
        # can start it again.
        if self.worker.config_path:
            enabled = t_read.get_elem(self.worker.config_path)

            if enabled:
                self.worker.restart()
                action_output.result = 'Requested supervisor to restart background worker'
            else:
                action_output.result = 'The background worker is disabled in configuration. To restart, enable {self.worker.config_path}'
        else:
            self.worker.restart()
            action_output.result = 'Requested supervisor to restart background worker'
