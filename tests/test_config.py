# SPDX-License-Identifier: GPL-3.0-only
import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from rm9451 import config

class ConfigurationTests(unittest.TestCase):
    def test_no_device_identity_is_shipped_and_connection_requires_configuration(self):
        with patch.object(config,'CONFIG_PATH',Path('nonexistent-local-config-test.json')):
            self.assertEqual(config.device_identity(),(0,''))
            self.assertFalse(config.extra_keys_enabled())
            with self.assertRaises(ValueError):config.require_device()
    def test_private_identity_roundtrip_and_strict_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'config.local.json'
            with patch.object(config,'CONFIG_PATH',path):
                path.write_text(json.dumps({'device':{'bluetooth_address':'12:34:56:78:9A:BC','container_key':'a'*64}}))
                self.assertEqual(config.require_device(),(0x123456789abc,'a'*64))
                path.write_text(json.dumps({'device':{'bluetooth_address':'invalid','container_key':'a'*64}}))
                with self.assertRaises(ValueError):config.require_device()
