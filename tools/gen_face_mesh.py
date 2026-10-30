# Generate the face-mesh tables used by apps/app_face_points.py.
#
# Why a generated mesh and not a triangulation of the landmarks: the 106-point
# model puts points on the silhouette and on the features and none inside the
# cheeks, so a Delaunay on them leaves the cheeks empty and packs triangles onto
# the eyes and mouth. Instead an even lattice is laid inside the face outline,
# triangulated once, and every vertex is bound to three landmarks by barycentric
# weights. The topology then never changes while the vertices follow any face.
#
# Run: python tools/gen_face_mesh.py
# Prints the MESH_BARY / MESH_EDGES tables to paste into the app. Needs numpy
# and scipy on the host; the board does not run this.
#
# Measured on board: the emitted mesh is 36 vertices and 94 edges, and the app
# runs at about 50-57 ms/frame (18-20 fps). Trimming the mesh from 104 to 94
# edges did not change the frame rate measurably: repainting the preview and
# running the detector already cost 36-40 ms.

import math

# One real detection, kept as the template face. The mesh only depends on the
# relative layout, so the absolute coordinates do not matter.
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

BASIN = [1, 9, 10, 11, 12, 13, 14, 15, 16, 2, 3, 4, 5, 6, 7, 8, 0, 24,
         23, 22, 21, 20, 19, 18, 32, 31, 30, 29, 28, 27, 26, 25, 17]

# Feature points that must land on a mesh node, so eyes and nose stay readable
# after the deformation. Brows 2, eyes 4, nose bridge 1, nose wings 2. The lips
# are left to the lattice: adding lip anchors cost 6 nodes for little visible
# gain at this scale, and the lattice already puts nodes along the mouth.
FEATURES = [
    43, 48,
    97, 102,
    35, 33, 39, 41,
    89, 87, 93, 95,
    74,
    77, 84,
]

# Every Nth outline point becomes a mesh node. Without outline anchors the
# triangulation spans the forehead and the silhouette stops looking like a face;
# stride 5 was the sparsest that still followed the jaw.
OUTLINE_STRIDE = 5

# The template face is only 97x98 px, so the lattice step decides how many
# interior nodes fit. 22 px gives 36 nodes in total with the forehead covered;
# 34 px fits only five interior nodes and leaves the forehead empty.
LATTICE_STEP = 22.0


def point_in_poly(p, poly):
    """Even-odd containment test, enough for a star-shaped face outline."""
    x, y = p
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if ((yi > y) != (yj > y)) and \
                (x < (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi):
            inside = not inside
        j = i
    return inside


def build_lattice(poly, step):
    """Hexagonal lattice inside the outline: even spacing, equilateral cells."""
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    out = []
    row = 0
    y = y0
    dy = step * math.sqrt(3.0) / 2.0
    while y <= y1:
        x = x0 + (step * 0.5 if row % 2 else 0.0)
        while x <= x1:
            if point_in_poly((x, y), poly):
                out.append((x, y))
            x += step
        y += dy
        row += 1
    return out


def main():
    import numpy as np
    from scipy.spatial import Delaunay

    poly = [TEMPLATE[i] for i in BASIN]
    outline = BASIN[::OUTLINE_STRIDE]
    verts = list(build_lattice(poly, LATTICE_STEP))
    lattice_n = len(verts)
    for i in outline:
        verts.append(TEMPLATE[i])
    for i in FEATURES:
        verts.append(TEMPLATE[i])
    # Anchors: outline and feature nodes must survive, otherwise a landmark a few
    # px outside the basin ends up as a dot with no edges.
    anchor_from = lattice_n
    print("lattice %d + outline %d + features %d = %d vertices"
          % (lattice_n, len(outline), len(FEATURES), len(verts)))

    xy = np.array(verts)
    tri = Delaunay(xy)
    tris = []
    for s in tri.simplices:
        if any(int(i) >= anchor_from for i in s):
            tris.append(tuple(sorted(s)))
        elif point_in_poly((xy[s, 0].mean(), xy[s, 1].mean()), poly):
            tris.append(tuple(sorted(s)))
    edges = set()
    for t in tris:
        for a in range(3):
            for b in range(a + 1, 3):
                edges.add((t[a], t[b]))
    edges = sorted(edges)
    print("%d triangles, %d edges" % (len(tris), len(edges)))
    print("estimated draw: %.1f ms at 800x480"
          % (12.0 + max(0, len(edges) - 23) * (19.3 - 11.9) / 23.0))

    lm = np.array(TEMPLATE)
    lm_tri = Delaunay(lm)
    bary = []
    for v in verts:
        p = np.array(v)
        s = lm_tri.find_simplex(p)
        if s < 0:
            raise SystemExit("vertex %r outside every landmark triangle" % (v,))
        T = lm_tri.simplices[s]
        A = lm_tri.transform[s]
        b = A[:2].dot(p - A[2])
        w0, w1 = float(b[0]), float(b[1])
        bary.append((int(T[0]), int(T[1]), int(T[2]), w0, w1, 1.0 - w0 - w1))

    def emit(name, items, fmt):
        print(name + " = [")
        line = "    "
        for it in items:
            piece = (fmt % it) + ", "
            if len(line) + len(piece) > 76:
                print(line.rstrip())
                line = "    "
            line += piece
        print(line.rstrip())
        print("]")

    emit("MESH_BARY", bary, "(%.0f, %.0f, %.0f, %.4f, %.4f, %.4f)")
    print()
    emit("MESH_EDGES", edges, "(%d, %d)")


if __name__ == "__main__":
    main()
