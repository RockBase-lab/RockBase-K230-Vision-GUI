# Asserts the on-board launcher.py text for the Face group entry name.

p = "/sdcard/factory/gui/launcher.py"
f = open(p, "r")
src = f.read()
f.close()
del f

lines = src.split("\n")
for i, ln in enumerate(lines):
    if '"id": "face_det"' in ln or '"id": "detect"' in ln:
        print("L%d: %s" % (i + 1, ln.strip()))

ok_face = '"id": "face_det", "name": "Faces"' in src
ok_vision = '"id": "detect", "name": "Detect"' in src

old = '"id": "face_det", "name": "Detect"'

print("CHECK face_det -> Faces :", "PASS" if ok_face else "FAIL")
print("CHECK detect  -> Detect :", "PASS" if ok_vision else "FAIL")
print("CHECK old name gone     :", "PASS" if old not in src else "FAIL")
print("RESULT:", "PASS" if (ok_face and ok_vision and old not in src) else "FAIL")
