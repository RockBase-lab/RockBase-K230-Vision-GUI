# What memory does this board actually expose, and what does gc.mem_free() count?
#
# Read-only. Creates no widgets, so it runs without --lvgl.
import gc
import os
import sys

print("=== gc ===")
try:
    print("MEM_FREE", gc.mem_free())
except Exception as e:
    print("MEM_FREE_ERR", repr(e))
try:
    print("MEM_ALLOC", gc.mem_alloc())
except Exception as e:
    print("MEM_ALLOC_ERR", repr(e))
print("MEM_TOTAL", gc.mem_alloc() + gc.mem_free())
for name in ("threshold", "collect", "enable", "disable", "isenabled"):
    print("GC_HAS", name, hasattr(gc, name))

print("=== sys ===")
print("IMPL", getattr(sys, "implementation", None))
try:
    print("SYS_ATTRS", [a for a in dir(sys) if not a.startswith("_")])
except Exception as e:
    print("SYS_ATTRS_ERR", repr(e))

print("=== micropython ===")
try:
    import micropython
    print("MP_ATTRS", [a for a in dir(micropython) if not a.startswith("_")])
    for name in ("mem_info", "heap_locked", "heap_unlocked", "opt_level"):
        print("MP_HAS", name, hasattr(micropython, name))
    try:
        micropython.mem_info()
    except Exception as e:
        print("MP_MEM_INFO_ERR", repr(e))
except Exception as e:
    print("MP_IMPORT_ERR", repr(e))

print("=== /proc ===")
for p in ("/proc/meminfo", "/proc/self/status", "/proc"):
    try:
        if p == "/proc":
            print("PROC_LIST", os.listdir(p))
        else:
            with open(p) as f:
                head = f.read(1200)
            print("PROC_OK", p, len(head))
            print(head)
    except Exception as e:
        print("PROC_ERR", p, repr(e))

print("=== os ===")
try:
    print("OS_ATTRS", [a for a in dir(os) if not a.startswith("_")])
except Exception as e:
    print("OS_ATTRS_ERR", repr(e))
print("HAS_SYSCONF", hasattr(os, "sysconf"))

print("=== media / image ===")
try:
    import image
    print("IMAGE_ATTRS", [a for a in dir(image) if a.isupper()])
    for a in ("ALLOC_HEAP", "ALLOC_MMZ", "ALLOC_SRAM"):
        if hasattr(image, a):
            print("IMAGE_CONST", a, getattr(image, a))
except Exception as e:
    print("IMAGE_ERR", repr(e))
try:
    from media.media import MediaManager
    print("MEDIA_ATTRS", [a for a in dir(MediaManager) if not a.startswith("_")])
except Exception as e:
    print("MEDIA_ERR", repr(e))

print("PROBE_DONE")
