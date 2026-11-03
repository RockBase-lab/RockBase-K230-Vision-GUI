# Per-batch ADC trace for the Battery app, for runs where serial is gone: the ripple
# only shows while the board runs on the pack, and unplugging the cable also removes
# the console. One B line per batch, plus an S timeline for the batches worth reading.
# Each batch is written and closed immediately, so a power cut costs one second.
#
# Off unless /data/battery/trace_on exists: a batch is one second apart, so leaving it
# on would write to the eMMC 86400 times a day for no product benefit.
#
# B: seq,t_ms,dur_ms,n_try,n_ok,n_rej,tap_uv,raw_med,med_pack,min_pack,max_pack,
#    span,min_i,max_i,min_rel,max_rel,span_r,over20,mean_pack,status,pct,paint_us,
#    heap,sb,quiet
# S: seq,i,rel_ms,gap_ms,since_paint_ms,uv,ok
#
# span is peak-to-peak in pack mV; span_r drops the outer 10% of each tail, which
# separates a broad ripple from one bad sample. quiet=1 batches skip the repaint, so
# comparing quiet against normal spans isolates this app's own redraw from the rest.

import gc
import os
import time

import batt

DIR = "/data/battery"
ENABLE_FILE = DIR + "/trace_on"
LIMIT_BYTES = 4 * 1024 * 1024
DUMP_SPAN_MV = 40           # dump the timeline above this span, or on any reject
DUMP_EVERY = 10             # ...and one baseline timeline every N batches
DUMP_MAX = 900              # timelines per session, about 15 minutes at 1 per second

HEADER = ("# B: seq,t_ms,dur_ms,n_try,n_ok,n_rej,tap_uv,raw_med,med_pack,"
          "min_pack,max_pack,span,min_i,max_i,min_rel,max_rel,span_r,over20,"
          "mean_pack,status,pct,paint_us,heap,sb,quiet\n"
          "# S: seq,i,rel_ms,gap_ms,since_paint_ms,uv,ok\n")


def _ensure_dir(path):
    """Create a directory level by level (CanMV has no os.makedirs)."""
    current = ""
    for part in path.strip("/").split("/"):
        current += "/" + part
        try:
            os.mkdir(current)
        except OSError:
            pass


def _exists(path):
    try:
        os.stat(path)
        return True
    except OSError:
        return False


def _append(path, text):
    with open(path, "a") as f:
        f.write(text)


class Tracer(object):
    """One CSV file per app session."""

    def __init__(self, path):
        self.path = path
        self.rows = 0
        self.dumps = 0
        self.written = 0

    def open(self):
        if not _exists(self.path):
            _append(self.path, "# battery adc trace\n")
            _append(self.path, HEADER)
        print("[Battery] trace -> {}".format(self.path))

    def close(self):
        path, rows = self.path, self.rows
        self.path = None
        if path is not None:
            try:
                _append(path, "# end rows={}\n".format(rows))
            except BaseException:
                pass

    def batch(self, rel, gap, sp, uvs, raw_med, tap_uv, status, pct, paint_us,
              quiet):
        """Write one batch. `rel`, `gap` and `sp` are microsecond ticks."""
        path = self.path
        if path is None:
            return
        if self.written >= LIMIT_BYTES:
            print("[Battery] trace file full, stopping")
            self.path = None
            return

        n = len(uvs)
        pv = []             # pack mV of the valid samples, in sample order
        pi = []             # their position in the batch, for min/max placement
        for i in range(n):
            uv = uvs[i]
            if 0 < uv <= batt.MAX_INPUT_UV:
                pv.append(uv * batt.DIV_NUM // batt.DIV_DEN // 1000)
                pi.append(i)
        n_ok = len(pv)

        med = mn = mx = span = span_r = mean = 0
        over20 = 0
        mn_i = mx_i = -1
        if n_ok:
            ordered = sorted(pv)
            med = ordered[n_ok // 2]
            mn = ordered[0]
            mx = ordered[-1]
            span = mx - mn
            k = n_ok // 10
            span_r = ordered[n_ok - 1 - k] - ordered[k] if n_ok > 2 * k else span
            total = 0
            for v in pv:
                total += v
                if v - med > 20 or v - med < -20:
                    over20 += 1
            mean = total // n_ok
            for j in range(n_ok):
                if pv[j] == mn and mn_i < 0:
                    mn_i = pi[j]
                if pv[j] == mx and mx_i < 0:
                    mx_i = pi[j]

        dur = rel[-1] // 1000 if rel else 0
        fields = [
            "B", self.rows, time.ticks_ms(), dur, n, n_ok, n - n_ok,
            tap_uv, raw_med, med, mn, mx, span, mn_i, mx_i,
            rel[mn_i] // 1000 if mn_i >= 0 else -1,
            rel[mx_i] // 1000 if mx_i >= 0 else -1,
            span_r, over20, mean, status.replace(" ", "_"),
            pct if pct is not None else -1, paint_us, gc.mem_free(),
            batt.STATUS_SAMPLES, 1 if quiet else 0,
        ]
        out = ",".join([str(v) for v in fields]) + "\n"

        if (span > DUMP_SPAN_MV or n > n_ok or self.rows < 3
                or (self.rows % DUMP_EVERY == 0)) and self.dumps < DUMP_MAX:
            self.dumps += 1
            for i in range(n):
                uv = uvs[i]
                ok = 1 if 0 < uv <= batt.MAX_INPUT_UV else 0
                r = rel[i] // 1000 if i < len(rel) else -1
                g = gap[i] // 1000 if i < len(gap) else -1
                p = sp[i] // 1000 if i < len(sp) else -1
                out += "S,{},{},{},{},{},{},{}\n".format(
                    self.rows, i, r, g, p, uv, ok)

        _append(path, out)
        self.rows += 1
        self.written += len(out)


def open_session():
    """Open the trace file for this app session, or None when tracing is off."""
    try:
        if not _exists(ENABLE_FILE):
            return None
        _ensure_dir(DIR)
        n = 1
        while n < 1000 and _exists("{}/trace_{:03d}.csv".format(DIR, n)):
            n += 1
        tracer = Tracer("{}/trace_{:03d}.csv".format(DIR, n))
        tracer.open()
        return tracer
    except BaseException as error:
        print("[Battery] trace off: {}".format(error))
        return None
