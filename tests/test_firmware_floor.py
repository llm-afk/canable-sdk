import unittest
from unittest.mock import patch
from canable import Device, DeviceInfo, Feature, UnsupportedError

class FirmwareFloorTests(unittest.TestCase):
    def test_supported_and_rejected_versions(self):
        for version, elmue, accepted in [(0x260618, True, True), (0x260803, True, True),
                                         (0x260617, True, False), (0x260618, False, False)]:
            with self.subTest(version=version, elmue=elmue):
                info = DeviceInfo("fake", "test", "Candlelight", 0, 0,
                                  firmware=version, channel_count=1,
                                  features=Feature.ELMUE if elmue else Feature(0))
                with patch("canable.device.discover", return_value=[info]), \
                     patch("canable.device.WinUSBTransport") as transport, \
                     patch("canable.device._probe", return_value=info):
                    if accepted:
                        with Device.open():
                            pass
                        transport.assert_called_once_with("fake")
                    else:
                        with self.assertRaises(UnsupportedError):
                            Device.open()
                        transport.assert_not_called()
