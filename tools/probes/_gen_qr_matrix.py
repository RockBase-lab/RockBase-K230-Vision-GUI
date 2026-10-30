# Generates a QR module matrix on the PC for injection into the scan
# probe. True means a dark module.

import qrcode

DATA = "HELLO-ROCKBASE-SCAN-123"
q = qrcode.QRCode(border=4)
q.add_data(DATA)
q.make(fit=True)
m = q.get_matrix()
n = len(m)
lines = [str(n)]
for row in m:
    parts = []
    x = 0
    while x < n:
        v = row[x]
        r = 1
        while x + r < n and row[x + r] == v:
            r += 1
        parts.append("{} {}".format(r, 1 if v else 0))
        x += r
    lines.append(" ".join(parts))
open("tmp/_qr_matrix.txt", "w").write("\n".join(lines))
print("modules:", n, "bytes:", sum(len(l) + 1 for l in lines))
