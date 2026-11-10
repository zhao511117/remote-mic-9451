# SPDX-License-Identifier: GPL-3.0-only
"""Bounded process recovery; failed starts never become an elevation loop."""
class RecoveryGuard:
    def __init__(self,now,*,initial_rescue=False,grace=45,stable=60,delay=3,limit=2,window=3600):
        self.created=now;self.initial_rescue=initial_rescue;self.grace=grace;self.stable=stable
        self.delay=delay;self.limit=limit;self.window=window
        self.up_since=None;self.missing_since=None;self.armed=False;self.attempts=[]
    def observe(self,running,now):
        if running:
            if self.up_since is None:self.up_since=now
            if now-self.up_since>=self.stable:self.armed=True
            self.missing_since=None
            return False
        self.up_since=None
        if self.missing_since is None:self.missing_since=now
        if now-self.missing_since<self.delay:return False
        eligible=self.armed or (self.initial_rescue and now-self.created>=self.grace)
        self.attempts=[t for t in self.attempts if now-t<self.window]
        if not eligible or len(self.attempts)>=self.limit:return False
        self.armed=False;self.initial_rescue=False;self.attempts.append(now)
        return True

def supervise(*,receiver_running,mapper_running,start_receiver,start_mapper,
              mapper_disabled,wait,cancelled,log,clock):
    receiver=RecoveryGuard(clock())
    mapper=RecoveryGuard(clock())
    last_receiver=True
    log('supervisor_ready')
    while not cancelled():
        now=clock();running=receiver_running()
        if receiver.observe(running,now):
            log('recover_receiver')
            start_receiver()
        if running:
            if not last_receiver:
                # A receiver restart must not reset the mapper's hourly budget.
                mapper.up_since=None;mapper.missing_since=None;mapper.armed=False
            if not mapper_disabled() and mapper.observe(mapper_running(),now):
                log('recover_key_mapper');start_mapper()
        last_receiver=running
        if wait(1):break
    log('supervisor_stopped')
