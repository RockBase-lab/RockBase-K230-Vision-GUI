# Runs a probe that needs display initialisation as a subprocess and
# writes the raw output to a file.
# Binary output goes through Python so the host shell cannot re-encode it.

import subprocess
import sys

script = sys.argv[1]
out_path = sys.argv[2]
cmd = [r".\.venv\Scripts\python.exe", "tools/_run_remote.py", "--lvgl",
       "COM9", "921600", script]
p = subprocess.run(cmd, capture_output=True)
data = p.stdout + p.stderr
with open(out_path, "wb") as f:
    f.write(data)
text = data.decode("utf-8", "replace")
for line in text.splitlines():
    if line.startswith("B64 "):
        continue
    print(line)
print("[CAP] rc={} bytes={} -> {}".format(p.returncode, len(data), out_path))
