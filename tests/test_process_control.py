# SPDX-License-Identifier: GPL-3.0-only
import threading,uuid,unittest
from rm9451.process_control import ProcessControl

class ProcessControlTests(unittest.TestCase):
    def test_duplicate_is_rejected_and_stop_reaches_owner(self):
        name='Local\\RemoteMic-Test-'+uuid.uuid4().hex
        a=ProcessControl(name);b=ProcessControl(name);stop=threading.Event()
        try:
            self.assertTrue(a.start());self.assertFalse(b.start())
            a.watch(stop)
            self.assertTrue(b.request_stop());self.assertTrue(stop.wait(1))
        finally:a.close();b.close()
        self.assertFalse(ProcessControl(name).request_stop())
