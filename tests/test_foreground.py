# SPDX-License-Identifier: GPL-3.0-only
import unittest
from rm9451.foreground import activate_window,toggle_window

class Fake:
    def __init__(self,*,locked=True,minimized=False,lie=False):
        self.front=1;self.locked=locked;self.minimized=minimized;self.lie=lie;self.restores=0;self.unlocks=0
    def is_minimized(self,hwnd):return self.minimized
    def restore(self,hwnd):self.restores+=1;self.minimized=False
    def foreground(self):return self.front
    def set_foreground(self,hwnd):
        if not self.locked and not self.lie:self.front=hwnd
        return not self.locked
    def minimize(self,hwnd):self.minimized=True;self.front=1
    def unlock(self):self.unlocks+=1;self.locked=False;return True

class ForegroundTests(unittest.TestCase):
    def test_active_app_button_minimizes_then_next_press_restores(self):
        api=Fake(locked=False);api.front=9
        self.assertTrue(toggle_window(9,api=api,wait=lambda _:None))
        self.assertTrue(api.minimized)
        self.assertTrue(toggle_window(9,api=api,wait=lambda _:None))
        self.assertFalse(api.minimized);self.assertEqual(api.front,9)
    def test_background_app_is_raised_instead_of_minimized(self):
        api=Fake()
        self.assertTrue(toggle_window(9,api=api,wait=lambda _:None))
        self.assertEqual(api.front,9);self.assertFalse(api.minimized)
    def test_browser_in_front_needs_unlock(self):
        api=Fake()
        self.assertTrue(activate_window(9,api=api,wait=lambda _:None))
        self.assertEqual(api.front,9);self.assertEqual(api.unlocks,1)
    def test_maximized_is_not_restored(self):
        api=Fake(locked=False)
        self.assertTrue(activate_window(9,api=api,wait=lambda _:None))
        self.assertEqual(api.restores,0);self.assertEqual(api.unlocks,0)
    def test_minimized_is_restored(self):
        api=Fake(minimized=True)
        self.assertTrue(activate_window(9,api=api,wait=lambda _:None))
        self.assertEqual(api.restores,1)
    def test_api_success_is_not_enough_without_real_foreground(self):
        api=Fake(locked=False,lie=True)
        self.assertFalse(activate_window(9,api=api,wait=lambda _:None))
