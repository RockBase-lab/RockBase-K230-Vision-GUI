# Checks which ulab operations are fast enough for the per-frame feature step.
#
# The current app builds a 512-element Python list per frame and normalises it
# with a Python loop, which is the suspected cost. This tests whether ulab can
# do the same with vectorised calls, and how long each approach takes.

import time

import ulab.numpy as np

N = 512
vals = [(i * 37 % 101) - 50.0 for i in range(N)]
arr = np.array(vals, dtype=np.float)
print("B1 array dim={} dtype={}".format(len(arr), arr.dtype))


def timeit(tag, fn, n=20):
    fn()
    t0 = time.ticks_us()
    for _ in range(n):
        r = fn()
    ms = time.ticks_diff(time.ticks_us(), t0) / 1000.0 / n
    print("B2 {:<34s} {:.3f} ms".format(tag, ms))
    return r


# Normalisation, Python loop.
def norm_python():
    total = 0.0
    for i in range(N):
        v = float(arr[i])
        total += v * v
    n = total ** 0.5
    inv = 1.0 / n
    return [float(arr[i]) * inv for i in range(N)]


# Normalisation, vectorised.
def norm_ulab():
    dot = np.dot(arr, arr)
    n = float(dot) ** 0.5
    return arr / n


a = timeit("python loop normalise", norm_python)
try:
    b = timeit("ulab vectorise normalise", norm_ulab)
    print("B3 ulab result dim={} ok={}".format(len(b), True))
    print("B4 python[0]={:.6f} ulab[0]={:.6f}".format(a[0], float(b[0])))
except BaseException as e:
    print("B4 ulab normalise FAILED:", e)

# Dot product between two normalised arrays.
arr2 = norm_ulab() if True else None
try:
    def dot_ulab():
        return np.dot(b, arr2)

    d = timeit("ulab dot", dot_ulab)
    print("B5 ulab dot={:.6f} (expect 1.0)".format(float(d)))
except BaseException as e:
    print("B5 ulab dot FAILED:", e)

# Python dot for comparison.
def dot_python():
    total = 0.0
    for i in range(N):
        total += a[i] * a[i]
    return total


timeit("python dot", dot_python)

# Bytes round trip for saving.
raw = b.tobytes()
print("B6 tobytes len={}".format(len(raw)))
back = np.frombuffer(raw, dtype=np.float)
print("B7 frombuffer dim={} first={:.6f}".format(len(back), float(back[0])))

# Overflow / copy semantics: does `arr / n` modify in place?
probe = np.array([1.0, 2.0, 3.0], dtype=np.float)
out = probe / 2.0
print("B8 divide: out={} src={}".format(
    [round(float(out[i]), 3) for i in range(3)],
    [round(float(probe[i]), 3) for i in range(3)]))

print("RESULT: DONE")
