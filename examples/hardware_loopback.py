"""Bounded hardware acceptance test; INTERNAL loopback only, no flash/DFU.

Run with Python 3.10+: hardware_loopback.py --serial SERIAL --output result.json
Works from a source checkout without pip install. No automatic send retries.
"""
import argparse
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from canable import Device, Frame, discover
from canable.models import FD_LENGTHS
from canable.protocol import encode_batch


def exercise(channel, frames, *, batch, rounds=1, expected=None):
    total, echoes, raw_times = 0, 0, 0
    kinds = Counter()
    expected = frames if expected is None else expected
    start = time.monotonic()
    for _ in range(rounds):
        receipts = channel.send_many(frames) if batch else [channel.send(frames[0])]
        wanted = expected if batch else expected[:1]
        received, seen_markers = [], []
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            event = channel.receive(min(0.1, max(0, deadline - time.monotonic())))
            if event is None:
                if len(received) == len(wanted) and len(seen_markers) == len(receipts):
                    break
                continue
            kinds[event.kind] += 1
            if event.kind == "error":
                raise AssertionError(f"firmware error event: {event}")
            if event.device_timestamp_us is not None:
                raw_times += 1
            if event.kind == "frame":
                received.append(event.frame)
            elif event.kind == "tx_echo":
                seen_markers.append(event.marker)
            if len(received) >= len(wanted) and len(seen_markers) >= len(receipts):
                break
        if received != wanted:
            raise AssertionError(f"Rx mismatch: expected={wanted!r}, received={received!r}")
        if seen_markers != [r.marker for r in receipts]:
            raise AssertionError(f"Tx marker mismatch: {seen_markers!r}")
        for receipt in receipts:
            receipt.wait(0)
        # No events are thrown away: check late extras/errors before next batch.
        if len(received) != len(wanted):
            raise AssertionError("extra Rx frames")
        total += len(received)
        echoes += len(receipts)
    while (event := channel.receive(0.05)) is not None:
        kinds[event.kind] += 1
        if event.kind in ("frame", "tx_echo", "error"):
            raise AssertionError(f"unexpected trailing event: {event}")
    return dict(sent=len(frames) * rounds if batch else rounds, received=total,
                tx_events=echoes, timestamped_events=raw_times,
                host_dropped=channel.dropped_events, event_kinds=dict(kinds),
                elapsed_seconds=round(time.monotonic() - start, 6),
                usb_write_bytes=len(encode_batch(frames, [1] * len(frames))) if batch else len(encode_batch(frames[:1], [1])))


def run(serial, rounds, output):
    report = dict(started_utc=datetime.now(timezone.utc).isoformat(),
                  mode="internal_loopback", serial=serial, external_can_connected=False,
                  cases=[], passed=False)
    try:
        report["discovery"] = [asdict(info) for info in discover(include_dfu=True)]
        with Device.open(serial=serial) as device:
            channel = device.channel(0)

            def case(name, frames, *, fd=False, batch=False, count=1, bitrate=1_000_000, data_bitrate=5_000_000):
                print(f"RUN {name}", flush=True)
                channel.configure(bitrate=bitrate, data_bitrate=data_bitrate if fd else None)
                if "board" not in report:
                    report["board"] = device.board_info()
                    report["boot_pin_enabled"] = device.boot_pin_enabled()
                channel.start(mode="internal_loopback")
                result = exercise(channel, frames, batch=batch, rounds=count)
                result.update(name=name, nominal_timing=asdict(channel.nominal_timing),
                              data_timing=asdict(channel.data_timing) if channel.data_timing else None,
                              error_state=channel.error_state())
                if result["error_state"]["tx_errors"] or result["error_state"]["rx_errors"]:
                    raise AssertionError(f"nonzero controller error counters: {result}")
                channel.stop()
                result["stopped_state"] = channel.error_state()
                if result["stopped_state"]["state"] != 4:
                    raise AssertionError("channel did not stop")
                report["cases"].append(result)
                print(f"PASS {name}: {result['received']} Rx, {result['tx_events']} Tx events", flush=True)

            for extended in (False, True):
                ident = 0x1ABCDE if extended else 0x123
                for length in (0, 1, 8):
                    case(f"classic_{'ext' if extended else 'std'}_{length}",
                         [Frame(ident, bytes(range(length)), extended=extended)])
                for dlc in (0, 8):
                    case(f"rtr_{'ext' if extended else 'std'}_{dlc}",
                         [Frame(ident, extended=extended, remote=True, remote_dlc=dlc)])

            for extended in (False, True):
                for brs in (False, True):
                    frames = [Frame((0x1ABC00 if extended else 0x200) + length,
                                    bytes(range(length)), extended=extended, fd=True, brs=brs)
                              for length in FD_LENGTHS]
                    case(f"fd_all_lengths_ext{int(extended)}_brs{int(brs)}", frames, fd=True, batch=True)

            packet64 = [Frame(0x300 + n, bytes(range(8))) for n in range(3)] + [Frame(0x303, bytes(range(6)))]
            case("usb_out_exact_64_bytes", packet64, batch=True)
            packet2048 = [Frame(0x400, bytes(8)), Frame(0x401, bytes(6))] + [
                Frame(0x500 + i, bytes((i + j) % 256 for j in range(64)), fd=True, brs=True) for i in range(28)]
            case("usb_out_exact_2048_bytes", packet2048, fd=True, batch=True)
            case("classic_batch63_marker_wrap", [Frame(0x600 + i, bytes([i]) * 8) for i in range(63)],
                 batch=True, count=rounds)
            case("fd_batch28_marker_wrap", packet2048[2:], fd=True, batch=True, count=rounds)
            # Real configured-but-not-started FD->classic normalization path.
            channel.configure(data_bitrate=5_000_000)
            case("configured_fd_to_classic", [Frame(0x321, b"classic")], bitrate=500_000)

        for i in range(5):
            with Device.open(serial=serial) as device:
                channel = device.channel().configure().start(mode="internal_loopback")
                result = exercise(channel, [Frame(0x111, bytes([i]))], batch=False)
                result["name"] = f"reopen_{i + 1}"
                report["cases"].append(result)
            print(f"PASS reopen_{i + 1}", flush=True)
        report["passed"] = True
    except Exception as exc:
        report["failure"] = f"{type(exc).__name__}: {exc}"
        print(report["failure"], file=sys.stderr, flush=True)
    finally:
        report["finished_utc"] = datetime.now(timezone.utc).isoformat()
        report["total_received"] = sum(c["received"] for c in report["cases"])
        report["total_tx_events"] = sum(c["tx_events"] for c in report["cases"])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Report: {output.resolve()}", flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", required=True)
    parser.add_argument("--rounds", type=int, default=10)
    parser.add_argument("--output", type=Path, default=Path("hardware-loopback.json"))
    args = parser.parse_args()
    if not 1 <= args.rounds <= 1000:
        parser.error("rounds must be 1..1000")
    raise SystemExit(run(args.serial, args.rounds, args.output))
