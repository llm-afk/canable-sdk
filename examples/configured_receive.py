"""Listen for selected CAN IDs; configure the adapter without transmitting."""
from canable import CanFilter, open_can


def main():
    with open_can(
        bitrate=1_000_000,
        mode="listen_only",
        queue_size=8192,
        filters=[CanFilter(0x180, 0x7F0)],
        termination=None,
        bus_load_interval_ms=1000,
    ) as can:
        print(can.info)
        while True:
            frame = can.recv(timeout=0.1)
            if frame is not None:
                print(hex(frame.arbitration_id), frame.data.hex())


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
