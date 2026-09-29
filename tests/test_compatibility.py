"""The old and new names must never create different runtime types."""
import importlib
import unittest
from unittest.mock import patch
import canable
import canable25

class CompatibilityTests(unittest.TestCase):
    def test_public_api_has_single_identity(self):
        self.assertEqual(canable.__version__, canable25.__version__)
        for name in canable.__all__:
            self.assertIs(getattr(canable, name), getattr(canable25, name))

    def test_submodules_have_single_identity(self):
        for name in ("models", "errors", "protocol", "interfaces", "winusb", "device", "channel", "connection"):
            with self.subTest(module=name):
                self.assertIs(importlib.import_module("canable." + name),
                              importlib.import_module("canable25." + name))

    def test_legacy_patch_reaches_canonical_implementation(self):
        with patch("canable25.connection.discover", return_value=[]) as discover:
            self.assertEqual(canable.list_devices(), [])
            discover.assert_called_once()
