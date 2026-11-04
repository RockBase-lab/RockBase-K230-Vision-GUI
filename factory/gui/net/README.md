# net/ — network subsystem (L3)

The resident Wi-Fi service: scanning, association, persistence of the chosen
access point, NTP, and the connection state the status bar and the Network app
both read.

## Contents

| Module | Provides |
| --- | --- |
| `service.py` | `boot_start`, scan, connect, NTP, state queries, the on-disk config wrapper |

## Rules

1. **May import:** `core/`, `hardware/`, and the runtime (`network`, `socket`,
   `_thread`). Never `apps/` or `shell/`.
2. **Never judge `connect()` success by the absence of an exception.** It returns
   while association is still pending; poll `isconnected()` until it flips or a
   deadline expires. A single sample once reported "cannot connect" on every
   boot while the same credentials connected in about 1.5 s (`docs/pitfalls.md`).
3. **The scan cache is the signal source.** `wlan.status('rssi')` reads about
   14 dB optimistic, and `scan()` returns nothing while the radio is locked to
   the current channel.
4. **One attempt has a hard time budget.** `boot_start` uses
   `BOOT_WIFI_TIMEOUT_MS` because `connect()` to an unreachable AP blocks about
   9 s inside the C layer and cannot be interrupted from Python.
5. **This package is the only place that opens the radio.** Apps call
   `net.service`; they never construct `network.WLAN` themselves.

## Status

Populated, final layout.
