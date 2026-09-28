"""Explicitly sends ONE frame. Run: python examples/send_once.py SERIAL"""
import sys
from canable25 import Device, Frame

with Device.open(serial=sys.argv[1]) as device:
    channel = device.channel(0)
    channel.configure(bitrate=1_000_000, data_bitrate=5_000_000)
    channel.start()
    receipt = channel.send(Frame(0x123, bytes(range(12)), fd=True, brs=True))
    print("USB write completed at:", receipt.submitted_ns)
    print("Firmware Tx event:", receipt.wait(1.0))
    # Tx event proves no application-level response from the target device.
    while (event := channel.receive(0)) is not None:
        print(event)
