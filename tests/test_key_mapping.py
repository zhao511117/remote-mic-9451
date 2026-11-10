# SPDX-License-Identifier: GPL-3.0-only
import unittest,os
from rm9451.key_mapping import ACTIONS,dispatch,make_script,selected_host,app_windows

class MappingTests(unittest.TestCase):
    def test_actions_and_unknown_do_not_run_extra_keys(self):
        chords=[];apps=[]
        for key in (6,7,10,11,12,13,9,8,0):
            result=dispatch(key,send_chord=lambda keys:chords.append(keys) or True,
                            activate=lambda kind:apps.append(kind) or True)
            self.assertEqual(result,key in ACTIONS)
        self.assertEqual(chords,[(0x1b,),(0x5b,0x44),(0x5b,0x09)])
        self.assertEqual(apps,['workbuddy','codex'])
    @unittest.skipUnless(os.environ.get('RM9451_HARDWARE_TESTS')=='1','requires configured physical remote')
    def test_source_selection_and_pinned_script(self):
        pid,address=selected_host()
        self.assertGreater(pid,0)
        source=make_script(address)
        self.assertNotIn('__CONFIG__',source)
        self.assertIn('96428',source)
    @unittest.skipUnless(os.environ.get('RM9451_HARDWARE_TESTS')=='1','requires desktop applications')
    def test_app_lookup_is_read_only(self):
        # WorkBuddy may be closed to its tray; launch path handles that case.
        self.assertIsInstance(app_windows('workbuddy'),list)
        self.assertTrue(app_windows('codex'))
