# SPDX-License-Identifier: GPL-3.0-only
"""Device-tested fallback for ZTKA's nonfunctional MIC_EXTEND implementation.

Physical HTT release (00 02) remains authoritative across host-driven restarts.
Close/open are queued back-to-back so the remote cannot enter slave sleep.
Only our own stop acknowledgments are hidden from the logical IME session.
"""
import asyncio,time

class CloneHoldCycle:
    INTERVAL=10.0
    RESTART_TIMEOUT=2.0
    MAX_HOLD=120.0

    def __init__(self,write,lock,log,on_stop):
        self.write=write;self.lock=lock;self.log=log;self.on_stop=on_stop
        self.active=False;self.pending=False;self.closed=False
        self.generation=0;self.started=0.;self.next_cycle=0.;self.pending_until=0.
        self.tasks=set()

    def reset(self):
        self.active=False;self.pending=False;self.generation+=1

    def _schedule_close(self):
        generation=self.generation
        async def close_orphan():
            async with self.lock:
                if not self.closed and not self.active and generation==self.generation:
                    await self.write(b'\x0d')
        task=asyncio.create_task(close_orphan());self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    def control(self,data,now=None):
        now=time.monotonic() if now is None else now
        if self.closed:return False
        if data[0]==4 and len(data)>=2:
            if data[1]==3:
                self.reset();self.active=True;self.started=now
                self.next_cycle=now+self.INTERVAL
                return True
            if self.active and self.pending and data[1] in (0,1):
                self.pending=False;self.next_cycle=now+self.INTERVAL
                self.log('hold_cycle_resumed',{})
                return True
            if not self.active:
                self.log('orphan_start_closed',{})
                self._schedule_close()
                return False
        if data[0]==0:
            reason=data[1] if len(data)>=2 else None
            if self.active and self.pending and reason in (0,1):
                self.log('hold_cycle_stop_ack',{'reason':reason})
                return False
            was_pending=self.pending
            self.reset()
            if was_pending:self._schedule_close()
        return True

    async def _abort(self,reason):
        self.reset();self.log('hold_cycle_aborted',{'reason':reason})
        self.on_stop(reason)
        await self.write(b'\x0d')

    async def tick(self,now):
        generation=self.generation
        if self.closed or not self.active:return
        async with self.lock:
            if self.closed or not self.active or generation!=self.generation:return
            if now-self.started>=self.MAX_HOLD:
                await self._abort('session_limit');return
            if self.pending:
                if now>=self.pending_until:await self._abort('cycle_restart_timeout')
                return
            if now<self.next_cycle:return
            self.pending=True;self.pending_until=now+self.RESTART_TIMEOUT
            close_ok=await self.write(b'\x0d')
            if not self.active or generation!=self.generation:return
            if not close_ok:
                await self._abort('cycle_close_failed');return
            # Do not sleep or wait for STOP here: the peripheral can otherwise
            # sleep for ~1.2 s (observed interval12, slave latency79).
            open_ok=await self.write(b'\x0c')
            if not self.active or generation!=self.generation:return
            if not open_ok:
                await self._abort('cycle_open_failed');return
            self.log('hold_cycle_sent',{'seconds':round(now-self.started,2)})

    async def shutdown(self):
        self.closed=True;self.reset()
        tasks=list(self.tasks)
        for task in tasks:task.cancel()
        if tasks:await asyncio.gather(*tasks,return_exceptions=True)
        self.tasks.clear()
