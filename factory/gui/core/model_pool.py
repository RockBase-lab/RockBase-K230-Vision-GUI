# Resident KPU model pool: one nn.kpu per kmodel path, loaded once per process.
#
# The GUI runs exactly one app at a time, so a single kpu per model can be shared
# by every app that needs it. Taking models from here instead of constructing them
# in each app is what makes a boot-time preload possible: the app's open path
# becomes a dict lookup.
#
# Ownership: the pool owns every kpu it hands out. Apps must never call release();
# only release_all() does, and it exists for shutdown. An app's destroy() only
# deinits its own ai2d, which is per-app because the input geometry differs.
#
# Measured on board: sixteen resident models cost about 1 KB of Python heap in
# total, because load_kmodel places the weights and KPU arenas outside the GC
# heap. All sixteen load and each still infers (27-109 ms). An earlier report
# of a wedge at nine instances did not reproduce with registry-order loads.

import gc
import time

import nncase_runtime as nn

from core.errors import AppUnavailable

MISSING = ("Model missing on the SD card.\n"
           "Expected:\n  {}")

_pool = {}      # path -> nn.kpu()
_failed = {}    # path -> reason, so a broken model is not retried on every open


def get(path):
    """Return the resident kpu for path, loading it on first use.

    Raises AppUnavailable when the file is missing or the load fails, which the
    launcher already turns into a plain-language panel.
    """
    kpu = _pool.get(path)
    if kpu is not None:
        return kpu
    reason = _failed.get(path)
    if reason is not None:
        raise AppUnavailable(MISSING.format(reason))

    t0 = time.ticks_ms()
    try:
        kpu = nn.kpu()
        kpu.load_kmodel(path)
    except BaseException as error:
        _failed[path] = "{} ({})".format(path, error)
        print("[POOL] load failed: {} ({})".format(path, error))
        raise AppUnavailable(MISSING.format(path))
    _pool[path] = kpu
    print("[POOL] {} {}ms resident={} free={}KB".format(
        path.rsplit("/", 1)[-1], time.ticks_diff(time.ticks_ms(), t0),
        len(_pool), gc.mem_free() // 1024))
    return kpu


def is_loaded(path):
    return path in _pool


def loaded_count():
    return len(_pool)


def preload(items, on_progress=None):
    """Load every (path, label) in items once, reporting progress after each.

    Never raises: one bad model must not stop the boot. Returns (ok, failed).
    """
    total = len(items)
    ok = 0
    failed = 0
    for i in range(total):
        path, label = items[i]
        try:
            get(path)
            ok += 1
        except AppUnavailable as error:
            failed += 1
            print("[POOL] preload skipped {}: {}".format(label, error.message))
        if on_progress is not None:
            try:
                on_progress(i + 1, total, label)
            except BaseException as error:
                print("[POOL] progress callback failed: {}".format(error))
    return ok, failed


def release_all():
    """Release every pooled kpu. Only for shutdown, never from an app."""
    for path in list(_pool.keys()):
        kpu = _pool.pop(path)
        try:
            kpu.release()
        except BaseException as error:
            print("[POOL] release {} failed: {}".format(path, error))
    _failed.clear()
    gc.collect()
