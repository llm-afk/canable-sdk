"""Read-only list/info by default; bus access is an explicit subcommand."""
import argparse
from dataclasses import asdict
import json
import sys
import time

from . import CanableError, Device, Frame, discover


def _json(value):
    return json.dumps(value, ensure_ascii=False, default=lambda x: x.hex() if isinstance(x, bytes) else str(x))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="canable")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="read-only USB enumeration/capability probe")
    info = commands.add_parser("info", help="read-only firmware information")
    info.add_argument("--serial", required=True)
    for name in ("monitor", "send", "loopback"):
        sub = commands.add_parser(name)
        sub.add_argument("--serial", required=True)
        sub.add_argument("--channel", type=int, default=0)
        sub.add_argument("--bitrate", type=int, default=1_000_000)
        sub.add_argument("--data-bitrate", type=int)
        sub.add_argument("--sample-point", type=float, default=0.75)
        sub.add_argument("--data-sample-point", type=float, default=0.75)
        if name == "monitor":
            sub.add_argument("--seconds", type=float, default=10)
        else:
            sub.add_argument("--id", type=lambda s: int(s, 0), default=0x123)
            sub.add_argument("--data", default="0102030405060708")
            sub.add_argument("--extended", action="store_true")
            sub.add_argument("--fd", action="store_true")
            sub.add_argument("--brs", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command in ("list", "info"):
            entries = discover(include_dfu=True)
            if args.command == "info":
                entries = [i for i in entries if i.serial == args.serial]
                if not entries:
                    raise CanableError("serial not found or inaccessible; use list for interface errors")
            print(_json([asdict(i) for i in entries]))
            return 0
        frame = None
        if args.command != "monitor":
            frame = Frame(args.id, bytes.fromhex(args.data), extended=args.extended, fd=args.fd, brs=args.brs)
        elif args.seconds <= 0:
            raise ValueError("seconds must be positive")
        with Device.open(serial=args.serial) as device:
            channel = device.channel(args.channel)
            channel.configure(bitrate=args.bitrate, data_bitrate=args.data_bitrate,
                              sample_point=args.sample_point, data_sample_point=args.data_sample_point)
            mode = {"monitor": "listen_only", "send": "normal", "loopback": "internal_loopback"}[args.command]
            channel.start(mode=mode)
            if frame is not None:
                receipt = channel.send(frame)
                event = receipt.wait(2.0)
                print(_json(dict(submitted_ns=receipt.submitted_ns, completion=asdict(event), mode=mode)))
                if args.command == "loopback":
                    deadline = time.monotonic() + 2
                    while time.monotonic() < deadline:
                        event = channel.receive(0.1)
                        if event and event.frame == frame:
                            print(_json(dict(loopback="passed", received=asdict(event))))
                            return 0
                    raise CanableError("Tx event seen but matching loopback frame not observed")
            else:
                deadline = time.monotonic() + args.seconds
                while time.monotonic() < deadline:
                    event = channel.receive(min(0.1, max(0, deadline - time.monotonic())))
                    if event:
                        print(_json(asdict(event)))
        return 0
    except (CanableError, ValueError, OSError) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
