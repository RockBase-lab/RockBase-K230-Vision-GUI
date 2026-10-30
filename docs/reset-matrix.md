# Reset and recovery matrix

The board has several resources whose native state survives a MicroPython soft
reset. Choosing the wrong recovery action wastes minutes, so this table maps a
failure to the smallest action that actually clears it.

## Recovery levels

| Level | Action | Trigger |
|---|---|---|
| L0 | Delete the module from `sys.modules` | stale bytecode after deploying a `.py` |
| L1 | Ctrl-D soft reboot (`machine.soft_reset()`, or `_run_remote.py --reset`) | stale module cache, some leaked Python state |
| L2 | `machine.reset()` | state that L1 does not clear |
| L3 | Power cycle / physical reset button | anything native that L1 and L2 cannot clear |

## The matrix

| Failure | Symptom | Cleared by | Notes |
|---|---|---|---|
| Audio input (PDM) handle leak | `kd_mpi_ai_set_pdm_clk failed:-1` on the next `open()` | **L3 only** | `mpp.ai.kd_mpi_ai_disable(dev)` rescues one leak; calling it again locks the channel permanently |
| Audio output (AO) handle leak | `kd_mpi_ao_enable_chn failed:-1` | **L3 only** | Applies to a stream opened without `stop_stream` then `close` |
| Video decoder pool leak | `kd_mpi_vdec_attach_vb_pool failed`, pool id reaching -1, camera also failing | **L3 only** | `Decoder.destroy()` raises before `vb_destory_pool`; destroy failure must be repaired manually |
| Camera sensor singleton | `OSError: sensor(N) is already inited` | **L2 only** | One `Sensor()` per VM lifetime; `stop()` + `deinit()` does not free it and L1 does not clear it |
| WiFi supplicant wedge | repeated association failures, then no recovery | **L3 only** | Radio cannot be disabled; also restart the access point, which may have blacklisted the client |
| SD file still locked | `OSError: 16` (EBUSY) on remove, `EPERM` on reopen | **L3 only** | An unclosed handle survives a soft reboot |
| Stale Python module | code changes have no effect, or new attributes are missing | **L0** | `del sys.modules[name]` inside the probe; do not rely on L1 |
| LVGL active screen deleted | touch completely dead, or a crash | **L3** | Never delete the active screen; load another screen first |
| mpp resource residue after Vision | `kd_mpi_ao_enable_chn failed:-1` in Camera afterwards | **L3 only** | Vision leaves resources that block AO enablement |
| Wrong video resolution bound to `LAYER_VIDEO1` | whole board unresponsive, no serial echo | **L3 only** | Validate `w == 800 and h == 480` before creating the decoder |
| Serial link dropped after a native crash | `ClearCommError` 22, `OSError` 995, `PermissionError` 22 | **L3 only** | Reach out and press reset |
| Native crash from a bad ai2d declaration | deadlock with no exception and no REPL response | **L3 only** | See `docs/board-ai-runtime.md` |
| Audio stream closed without `stop_stream` | hard hang, programmatic attempts produce nothing | **L3 only** | See `docs/performance-budget.md` |

## Things that specifically do not work

- Confirming the port is free proves nothing: `serial.Serial(port).open()`
  succeeds in 4 ms even while the GUI loop is running. The failure mode is not
  port contention but a blocking read.
- Ctrl-C does not interrupt a running GUI loop: four attempts produced zero
  bytes of echo.
- Ctrl-D does interrupt it: it returns `MPY: soft reboot` immediately.
- A soft reset does not clear user module bytecode in every case. Measured:
  newly added attributes were still missing after `--reset`, so probes must
  delete the module explicitly.
- Disconnecting the host does not stop the on-board script. It keeps running
  and the port will open but return no bytes. Confirm the board is alive before
  reconnecting.

## Recovery procedure for a wedged board

1. Try Ctrl-D. If the board answers `MPY: soft reboot`, continue with L0/L1
   work.
2. If nothing echoes, power cycle. Do not keep sending Ctrl-C; it cannot be
   delivered while a C call holds the GIL.
3. After a power cycle, verify the board is alive before deploying:
   a short probe that prints the firmware version and free space is enough.
4. If serial still cannot be opened after a power cycle, check the host side:
   an IDE serial session or an orphaned probe process may hold the port.

## Leak-free probe discipline

The three leak classes above are all caused by probe or app teardown that skips
a release step. Follow these rules and the leaks never occur:

- Audio: open in `try`, `stop_stream()` then `close()` in `finally`, and never
  call `terminate()` on the shared `PyAudio`.
- Camera: `s.stop()` then `MediaManager.deinit()` in `finally`, and only after
  switching away from any active screen.
- Video: repair the decoder pool when channel destruction fails, before
  returning.
- LVGL: never delete the active screen; delete the old screen only after
  `scr_load` on the new one.
- Files: close every handle explicitly, including on error paths, because a
  leaked handle locks the file across soft reboots.
