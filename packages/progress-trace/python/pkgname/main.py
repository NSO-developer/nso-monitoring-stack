# -*- mode: python; python-indent: 4 -*-
import ncs
from ncs.application import Service
from ncs.dp import Action


# ---------------
# ACTIONS EXAMPLE
# ---------------
class ReportAction(Action):
    @Action.action
    def cb_action(self, uinfo, name, kp, input, output, trans):
        self.log.info("Logging progress trace")
        with ncs.maapi.single_read_trans('admin', 'test_context') as t:
            t.report_progress(ncs.VERBOSITY_NORMAL, input.msg, "progress-trace")
        self.log.info("Logged progress trace")


# ---------------------------------------------
# COMPONENT THREAD THAT WILL BE STARTED BY NCS.
# ---------------------------------------------
class Main(ncs.application.Application):
    def setup(self):
        self.register_action('report-action', ReportAction)

    def teardown(self):
        self.log.info('Main FINISHED')
