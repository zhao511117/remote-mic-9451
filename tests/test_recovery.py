# SPDX-License-Identifier: GPL-3.0-only
import unittest
from rm9451.recovery import RecoveryGuard,supervise

class RecoveryTests(unittest.TestCase):
    def test_failed_initial_receiver_does_not_repeat_attach(self):
        guard=RecoveryGuard(0)
        self.assertFalse(any(guard.observe(False,t) for t in range(200)))
    def test_healthy_then_lost_recovers_once_and_failed_restart_does_not_loop(self):
        guard=RecoveryGuard(0)
        for t in range(61):self.assertFalse(guard.observe(True,t))
        results=[t for t in range(61,200) if guard.observe(False,t)]
        self.assertEqual(results,[64])
    def test_missing_sidecar_gets_one_rescue_after_startup_grace(self):
        guard=RecoveryGuard(0,initial_rescue=True)
        self.assertEqual([t for t in range(200) if guard.observe(False,t)],[45])
    def test_short_lived_process_does_not_rearm(self):
        guard=RecoveryGuard(0,initial_rescue=True)
        guard.observe(False,0);self.assertTrue(guard.observe(False,45))
        for t in range(46,50):guard.observe(True,t)
        self.assertFalse(any(guard.observe(False,t) for t in range(50,200)))
    def test_hourly_limit_even_when_repeatedly_killed_after_healthy_start(self):
        guard=RecoveryGuard(0,stable=1,delay=0)
        results=[]
        for t in range(0,12,3):
            guard.observe(True,t);guard.observe(True,t+1)
            results.append(guard.observe(False,t+2))
        self.assertEqual(results,[True,True,False,False])
    def test_explicit_stop_does_not_restart_anything(self):
        events=[]
        supervise(receiver_running=lambda:False,mapper_running=lambda:False,
                  start_receiver=lambda:events.append('bad'),start_mapper=lambda:events.append('bad'),
                  mapper_disabled=lambda:False,wait=lambda _:True,cancelled=lambda:True,
                  log=events.append,clock=lambda:0)
        self.assertNotIn('bad',events)
    def test_receiver_restarts_do_not_reset_mapper_hourly_limit(self):
        now=[0];events=[]
        def wait(_):now[0]+=1;return now[0]>=260
        supervise(receiver_running=lambda:not any(a<=now[0]<b for a,b in [(70,80),(160,170)]),
                  mapper_running=lambda:not any(a<=now[0]<b for a,b in [(61,66),(145,150),(235,240)]),
                  start_receiver=lambda:None,start_mapper=lambda:events.append('mapper'),
                  mapper_disabled=lambda:False,wait=wait,cancelled=lambda:False,
                  log=lambda _:None,clock=lambda:now[0])
        self.assertEqual(events.count('mapper'),2)
