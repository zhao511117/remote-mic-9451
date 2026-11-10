# SPDX-License-Identifier: GPL-3.0-only
import unittest
from rm9451.login_start import launch_when_ready

class LoginStartTests(unittest.TestCase):
    def run_launcher(self,states,*,running=lambda:False,cancel_after=None):
        now=[0.];events=[];launched=[];watchdogs=[];index=[0]
        def wait(seconds):now[0]+=seconds;return cancel_after is not None and now[0]>=cancel_after
        def names():
            value=states[min(index[0],len(states)-1)];index[0]+=1;return value
        result=launch_when_ready(is_running=running,names=names,start_watchdog=lambda:watchdogs.append(True),
            start_receiver=lambda:launched.append(True),wait=wait,cancelled=lambda:cancel_after is not None and now[0]>=cancel_after,
            clock=lambda:now[0],timeout=8,initial_delay=0,log=events.append)
        return result,launched,watchdogs,events

    def test_existing_receiver_never_starts_second_instance_or_watchdog(self):
        result,launched,watchdogs,_=self.run_launcher([set()],running=lambda:True)
        self.assertEqual(result,'already_running');self.assertEqual(launched,[]);self.assertEqual(watchdogs,[])

    def test_slow_ime_starts_watchdog_once_then_waits_for_stable_service(self):
        result,launched,watchdogs,_=self.run_launcher([set(),set(),{'imewatchdog.exe'},{'imeservice.exe'}])
        self.assertEqual(result,'receiver_started');self.assertEqual(len(launched),1);self.assertEqual(len(watchdogs),1)

    def test_ime_failure_does_not_launch_receiver_or_repeat_uac_initialization(self):
        result,launched,watchdogs,_=self.run_launcher([set()])
        self.assertEqual(result,'ime_startup_timeout');self.assertEqual(launched,[]);self.assertEqual(len(watchdogs),1)

    def test_stop_during_startup_cancels_pending_receiver(self):
        result,launched,_,_=self.run_launcher([{'imeservice.exe'}],cancel_after=1)
        self.assertEqual(result,'cancelled');self.assertEqual(launched,[])

    def test_existing_watchdog_is_not_restarted(self):
        result,launched,watchdogs,_=self.run_launcher([{'imewatchdog.exe'},{'imewatchdog.exe','imeservice.exe'}])
        self.assertEqual(result,'receiver_started');self.assertEqual(len(launched),1);self.assertEqual(watchdogs,[])
