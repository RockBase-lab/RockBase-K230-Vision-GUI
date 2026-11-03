# Runs a probe that needs display initialisation as a subprocess and
# writes the raw output to a file.
# Binary output goes through Python so the host shell cannot re-encode it.
#
# Usage:
#   python tools/probes/_cap_run.py <script> <out_path> [port]
#   python tools/probes/_cap_run.py COM56 tools/probes/_cap_x.py tmp/shot.txt
#
# The port is auto-detected when omitted, so a board that enumerates as
# something other than COM9 does not need this file edited.

import subprocess
import sys

if len(sys.argv) < 3:
    print(__doc__ or "usage: _cap_run.py <script> <out_path> [port]")
    sys.exit(2)

script = sys.argv[1]
out_path = sys.argv[2]
port = sys.argv[3] if len(sys.argv) > 3 else ""

cmd = [r".\.venv\Scripts\python.exe", "tools/_run_remote.py", "--lvgl"]
if port:
    cmd += [port, "921600"]
cmd.append(script)
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
