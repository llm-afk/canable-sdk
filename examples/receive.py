"""Passive CAN monitor. Run: python examples/receive.py SERIAL"""
import sys

from canable import Device

with Device.open(serial=sys.argv[1]) as device:
    channel = device.channel(0)
    channel.configure(bitrate=1_000_000, data_bitrate=5_000_000)
    channel.start(mode="listen_only")
    try:
        while True:
            event = channel.receive(1.0)
            if event:
                print(event)
    except KeyboardInterrupt:
        pass
