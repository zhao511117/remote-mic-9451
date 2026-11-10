# SPDX-License-Identifier: GPL-3.0-only
import unittest
from rm9451.buttons import decode_selectors,SelectorState,selected_paths

class NavigationTests(unittest.TestCase):
    def test_captured_selectors_are_not_bitmasks(self):
        self.assertEqual(decode_selectors(bytes.fromhex('020300')),frozenset({3}))
        self.assertEqual(decode_selectors(bytes.fromhex('020400')),frozenset({4}))
        self.assertEqual(decode_selectors(bytes.fromhex('020100')),frozenset({1}))
        self.assertEqual(decode_selectors(bytes.fromhex('020200')),frozenset({2}))
        self.assertEqual(decode_selectors(bytes.fromhex('020500')),frozenset({5}))
    def test_edges_repeat_dedup_and_disconnect_release(self):
        edges=[];state=SelectorState(lambda k,u:edges.append((k,u)) or True)
        state.update(bytes.fromhex('020300'));state.update(bytes.fromhex('020300'))
        state.update(bytes.fromhex('020400'));state.release()
        self.assertEqual(edges,[(3,False),(3,True),(4,False),(4,True)])
        self.assertEqual(state.down,set())
    def test_only_selected_container_is_eligible(self):
        self.assertLessEqual(len(selected_paths()),1)
