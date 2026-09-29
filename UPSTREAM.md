# Upstream protocol baseline

This is a standalone Python SDK repository. It does not require a local firmware checkout.

- Upstream: https://github.com/Elmue/CANable-2.5-firmware-Slcan-and-Candlelight
- Inspected commit: `e862f6a6b609ddee22d071e439ebaee1a52010ff`
- Firmware version: `0x260803`

Paths such as `Firmware/Candlelight/candlelight_def.h` and `SampleApplication C++/Source/Candlelight/`
mentioned in source comments or THIRD_PARTY_NOTICES.md refer to that upstream repository.
The SDK does not compile or import upstream firmware files.

The project includes source, offline tests, CLI, examples, hardware validation scripts and reports.
Run `./sdk.ps1 test` for offline tests, `./sdk.ps1 list` for read-only discovery,
and `./sdk.ps1 build` to regenerate the wheel under `dist/`.
Generated wheels, build products, bytecode and local virtual environments are not committed.

Hardware evidence is scoped to the exact adapter and configuration in VALIDATION.md.
External CAN interoperability and long-running performance remain unverified.
## Elmue compatibility floor

Minimum firmware: `0x260618`. Compared upstream `1f201d7` (Source/) with
`5852f8a` (Firmware/), accounting for the directory rename. Candlelight wire
definitions, control requests, USB class, CAN implementation and settings are
identical. The buffer change adds `GLB_ProtoElmue` to the TxBlob detection
condition; it fixes legacy-mode parsing and leaves the SDK Elmue path unchanged.
The SDK explicitly activates Elmue mode and still requires its capability bit.
Versions below 0x260618 and non-Elmue adapters remain unsupported.
