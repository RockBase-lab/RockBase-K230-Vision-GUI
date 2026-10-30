# Generate the face-mesh tables used by apps/app_face_points.py.
#
# Why a hand-placed mesh: the 106-point landmark model puts points on the
# silhouette and on the features and none inside the cheeks, so triangulating
# them directly leaves the cheeks empty. An automatic lattice also degrades
# below about 30 nodes, because the landmark outline is unevenly spaced and the
# lower face has no landmarks at all, so the triangles become long and skewed
# and the eyes vanish into them.
#
# Instead 26 nodes are placed at even fractions of the face box. Feature nodes
# reuse real landmark indices so the mesh tracks expressions; forehead, cheek,
# jaw and chin nodes are synthetic and are bound to landmarks by inverse
# distance weighting. Barycentric binding cannot be used for those: they sit
# outside every landmark triangle, where the weights extrapolate wildly.
#
# Run: python tools/gen_face_mesh.py
# Prints MESH_ORDER / MESH_BIND / MESH_EDGES to paste into the app. Needs numpy
# and scipy on the host; the board does not run this.
#
# Measured on board: 26 nodes and 64 edges, the app settles around 45 ms/frame
# (about 22 fps). Trimming edges further barely moves the frame time, because
# the detector and the preview repaint already cost 36-40 ms.

import math

# One real detection, kept as the template face. Only the relative layout
# matters, so the absolute coordinates do not.
TEMPLATE = [
    (504.39, 286.71), (454.45, 201.29), (466.72, 259.82), (470.25, 265.42),
    (474.49, 270.59), (479.10, 275.27), (484.35, 279.74), (490.08, 283.41),
    (496.75, 285.87), (454.28, 208.13), (454.57, 214.87), (455.37, 221.50),
    (456.45, 228.04), (457.87, 234.56), (459.54, 241.02), (461.46, 247.49),
    (463.80, 253.77), (550.30, 199.02), (541.32, 258.49), (538.07, 264.31),
    (533.98, 269.63), (529.45, 274.44), (524.44, 278.99), (518.70, 282.95),
    (512.17, 285.58), (550.80, 205.91), (550.84, 212.66), (550.51, 219.42),
    (549.76, 226.02), (548.80, 232.67), (547.50, 239.35), (546.00, 245.94),
    (543.98, 252.35), (478.54, 210.29), (479.08, 206.50), (468.95, 207.44),
    (473.28, 209.38), (483.96, 209.62), (479.08, 206.50), (488.92, 208.53),
    (478.87, 203.61), (473.36, 204.66), (484.48, 204.99), (459.87, 195.78),
    (467.72, 194.93), (475.80, 195.20), (492.39, 199.16), (484.13, 196.70),
    (466.89, 190.23), (476.16, 189.65), (493.37, 195.14), (485.32, 191.73),
    (487.17, 255.59), (503.73, 264.02), (495.52, 256.13), (491.37, 259.80),
    (496.64, 262.95), (511.74, 255.59), (515.92, 258.99), (510.80, 262.58),
    (503.66, 256.61), (519.96, 254.50), (503.63, 255.02), (499.25, 249.70),
    (492.81, 252.08), (489.72, 255.63), (495.68, 254.98), (507.74, 249.51),
    (514.23, 251.45), (517.39, 254.69), (511.53, 254.42), (503.55, 250.70),
    (502.18, 205.22), (502.40, 214.75), (502.63, 224.21), (495.20, 208.57),
    (492.75, 228.63), (490.14, 236.62), (493.98, 239.38), (498.24, 240.34),
    (503.07, 241.72), (509.26, 208.26), (512.65, 228.12), (515.71, 235.95),
    (512.06, 238.78), (507.80, 240.12), (502.90, 233.79), (526.17, 209.33),
    (525.29, 205.50), (515.66, 208.00), (520.68, 208.90), (531.28, 208.30),
    (525.29, 205.54), (535.42, 206.22), (525.52, 202.62), (520.00, 204.18),
    (530.99, 203.47), (511.94, 198.81), (520.33, 195.91), (528.88, 194.07),
    (536.96, 193.65), (544.56, 194.59), (510.83, 194.78), (518.92, 190.81),
    (528.26, 188.46), (537.63, 188.96),
]

# (name, landmark index or None, nx, ny). nx/ny are fractions of the face
# bounding box and are only used when the landmark index is None.
#
# ny is negative for the forehead on purpose: the highest landmarks are the brows
# and the bbox top IS the brow line, so a fraction of 0 would put the forehead
# nodes on the eyebrows and the mesh would stop at the brow instead of reaching
# the hairline.
NODES = [
    ("forehead_top", None, 0.50, -0.34),
    ("forehead_l", None, 0.24, -0.22),
    ("forehead_r", None, 0.76, -0.22),
    ("temple_l", None, 0.02, 0.02),
    ("temple_r", None, 0.98, 0.02),
    ("brow_l_out", 43, None, None),
    ("brow_l_in", 48, None, None),
    ("brow_r_in", 102, None, None),
    ("brow_r_out", 97, None, None),
    ("eye_l_out", 33, None, None),
    ("eye_l_in", 41, None, None),
    ("eye_r_in", 95, None, None),
    ("eye_r_out", 87, None, None),
    ("bridge", 74, None, None),
    ("cheek_l", None, 0.12, 0.62),
    ("cheek_r", None, 0.88, 0.62),
    ("nose_tip", None, 0.50, 0.62),
    ("nose_l", 77, None, None),
    ("nose_r", 84, None, None),
    ("mouth_l", 52, None, None),
    ("mouth_r", 67, None, None),
    ("jaw_l", None, 0.26, 0.90),
    ("jaw_r", None, 0.74, 0.90),
    ("chin", None, 0.50, 0.99),
    ("cheek_low_l", None, 0.16, 0.80),
    ("cheek_low_r", None, 0.84, 0.80),
]

BIND_K = 3


def bbox(pts):
    xs = [v[0] for v in pts]
    ys = [v[1] for v in pts]
    return min(xs), min(ys), max(xs), max(ys)


def place(pts):
    x0, y0, x1, y1 = bbox(pts)
    w, h = x1 - x0, y1 - y0
    out = []
    for name, lm, nx, ny in NODES:
        if lm is not None:
            out.append((name, pts[lm][0], pts[lm][1]))
        else:
            out.append((name, x0 + nx * w, y0 + ny * h))
    return out


def idw(pts, p, k=BIND_K):
    """k nearest landmarks by inverse squared distance, weights normalized."""
    dl = sorted(((pts[i][0] - p[0]) ** 2 + (pts[i][1] - p[1]) ** 2, i)
                for i in range(len(pts)))
    sel = dl[:k]
    ws = [(i, 1.0 / (d2 + 1e-6)) for d2, i in sel]
    s = sum(w for _, w in ws)
    return [(i, w / s) for i, w in ws]


def main():
    import numpy as np
    from scipy.spatial import Delaunay

    nodes = place(TEMPLATE)
    xy = np.array([[n[1], n[2]] for n in nodes])
    tri = Delaunay(xy)
    tris = [tuple(sorted(int(i) for i in s)) for s in tri.simplices]

    edges = set()
    for t in tris:
        for a in range(3):
            for b in range(a + 1, 3):
                edges.add((min(t[a], t[b]), max(t[a], t[b])))
    edges = sorted(edges)

    used = {i for e in edges for i in e}
    orphan = [nodes[i][0] for i in range(len(nodes)) if i not in used]
    print("nodes=%d triangles=%d edges=%d orphan=%s"
          % (len(nodes), len(tris), len(edges), orphan or "none"))
    print("estimated draw: %.1f ms at 800x480"
          % (12.0 + max(0, len(edges) - 23) * (19.3 - 11.9) / 23.0))

    def emit_block(name, lines):
        print(name + " = [")
        for ln in lines:
            print("    " + ln)
        print("]")

    order = []
    line = ""
    for name, _, _ in nodes:
        piece = "%r, " % name
        if len(line) + len(piece) > 72:
            order.append(line.rstrip())
            line = ""
        line += piece
    order.append(line.rstrip())

    bind = []
    for i, n in enumerate(nodes):
        ws = idw(TEMPLATE, (n[1], n[2]))
        bind.append("(%s)," % ", ".join("(%d, %.4f)" % (k, w) for k, w in ws))

    elines = []
    line = ""
    for a, b in edges:
        piece = "(%d, %d), " % (a, b)
        if len(line) + len(piece) > 72:
            elines.append(line.rstrip())
            line = ""
        line += piece
    elines.append(line.rstrip())

    emit_block("MESH_ORDER", order)
    print()
    emit_block("MESH_BIND", bind)
    print()
    emit_block("MESH_EDGES", elines)


if __name__ == "__main__":
    main()
