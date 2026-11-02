import os

print("=== uname ===")
try:
    print(os.uname())
except Exception as e:
    print("uname failed:", e)

print("=== plate kmodels on board ===")
MODEL_DIR = "/sdcard/examples/kmodel"
try:
    names = os.listdir(MODEL_DIR)
except Exception as e:
    names = []
    print("listdir failed:", e)
for n in sorted(names):
    low = n.lower()
    if ("licen" in low or "plate" in low or "ocr" in low):
        p = MODEL_DIR + "/" + n
        try:
            print(p, os.stat(p)[6])
        except Exception as e:
            print(p, "stat failed:", e)
print("total entries:", len(names))

print("=== aidemo plate symbols ===")
try:
    import aidemo
    for n in dir(aidemo):
        low = n.lower()
        if ("licen" in low or "plate" in low or "ocr" in low):
            print(n)
except Exception as e:
    print("aidemo import failed:", e)

print("=== done ===")
