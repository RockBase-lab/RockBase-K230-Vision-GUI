# Does the MicroPython GC heap grow on demand, or is 4 MB a hard ceiling?
#
# Read-only in the sense that it allocates and frees bytearrays only; it touches
# no hardware and creates no widgets.
import gc

MB = 1048576.0


def snap(tag):
    gc.collect()
    alloc = gc.mem_alloc()
    free = gc.mem_free()
    print("%-18s alloc=%7.2f free=%7.2f sum=%7.2f MB" % (
        tag, alloc / MB, free / MB, (alloc + free) / MB))
    return alloc + free


def try_block(kb):
    """Try one contiguous allocation of kb KB. Returns (ok, sum_while_held)."""
    try:
        buf = bytearray(kb * 1024)
    except MemoryError:
        print("BLOCK %6d KB -> MemoryError" % kb)
        return False, None
    except Exception as e:
        print("BLOCK %6d KB -> %r" % (kb, e))
        return False, None
    held = gc.mem_alloc() + gc.mem_free()
    print("BLOCK %6d KB -> ok, sum while held %.2f MB, free %.2f MB" % (
        kb, held / MB, gc.mem_free() / MB))
    del buf
    return True, held


snap("baseline")

# Small steps: does the reported total move with demand?
for kb in (64, 128, 256, 512):
    try_block(kb)
    snap("after %dKB" % kb)

print("--- contiguous ceiling ---")
for kb in (768, 1024, 1536, 2048, 3072):
    ok, _held = try_block(kb)
    snap("after %dKB" % kb)
    if not ok:
        # Stop climbing once one size fails: this is the safe way to find the
        # ceiling without risking a wedge.
        print("CEILING_AT_OR_BELOW %d KB" % kb)
        break

snap("final")
print("PROBE_DONE")
