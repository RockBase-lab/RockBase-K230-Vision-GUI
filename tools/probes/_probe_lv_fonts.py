# Which built-in montserrat sizes does this firmware actually expose?
import lvgl as lv

print("--- montserrat built-ins ---")
found = []
for size in range(8, 60):
    name = "font_montserrat_{}".format(size)
    f = getattr(lv, name, None)
    if f is not None:
        found.append((size, f))
        print("FOUND", name, "line_height", getattr(f, "line_height", "?"))
print("SIZES", ",".join(str(s) for s, _ in found))

print("--- other font families ---")
for name in dir(lv):
    if name.startswith("font_") and "montserrat" not in name:
        f = getattr(lv, name, None)
        print("FAMILY", name, "line_height", getattr(f, "line_height", "?"))
print("PROBE_DONE")
