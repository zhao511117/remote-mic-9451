# SPDX-License-Identifier: GPL-3.0-only
import sys,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'vendor'))
from rm9451.doubao import create_physicalizer

class AdapterStartupTests(unittest.TestCase):
    def test_failed_attach_cleans_up_and_reports_failure(self):
        stopped=[]
        class Failed:
            error='cancelled attach'
            def start(self,*_):return False
            def stop(self):stopped.append(True)
        with patch('ovb_rc003.doubao_rpc.DoubaoPhysicalizer',Failed),patch('rm9451.doubao.threading.Timer') as timer:
            with self.assertRaisesRegex(RuntimeError,'cancelled attach'):
                create_physicalizer(lambda *_:None)
            timer.return_value.cancel.assert_called_once()
        self.assertEqual(stopped,[True])

    def test_successful_attach_cancels_startup_deadline(self):
        class Ready:
            status='active'
            def start(self,*_):return True
        with patch('ovb_rc003.doubao_rpc.DoubaoPhysicalizer',Ready),patch('rm9451.doubao.threading.Timer') as timer:
            result=create_physicalizer(lambda *_:None)
            self.assertEqual(result.status,'active')
            timer.return_value.cancel.assert_called_once()
