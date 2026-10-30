# Verifies FaceDetApp._show_faces keeps each face's landmark slice in sync with
# its own box, including when a degenerate box is skipped in the middle of the
# list. Builds only the box/label/dot pools, no sensor and no model.

import sys

import lvgl as lv

sys.path.insert(0, "/sdcard/factory/gui")
sys.modules.pop("apps.app_face_det", None)
from apps.app_face_det import FaceDetApp, MAX_FACES, POINTS_PER_FACE

HIDDEN = lv.obj.FLAG.HIDDEN
created = []


def make_pool():
    """A FaceDetApp with just the pools _show_faces touches."""
    app = object.__new__(FaceDetApp)
    app.y_off = 160
    app.box_objs = []
    app.box_labels = []
    app.dot_objs = []
    for _ in range(MAX_FACES):
        box = lv.obj(lv.scr_act())
        box.add_flag(HIDDEN)
        app.box_objs.append(box)
        created.append(box)
        lab = lv.label(lv.scr_act())
        lab.add_flag(HIDDEN)
        app.box_labels.append(lab)
        created.append(lab)
    for _ in range(MAX_FACES * POINTS_PER_FACE):
        dot = lv.obj(lv.scr_act())
        dot.add_flag(HIDDEN)
        app.dot_objs.append(dot)
        created.append(dot)
    return app


def show_all(app):
    for dot in app.dot_objs:
        dot.clear_flag(HIDDEN)


def visible(app):
    return [i for i, d in enumerate(app.dot_objs) if not d.has_flag(HIDDEN)]


def pts(cx, cy):
    out = []
    for k in range(POINTS_PER_FACE):
        out += [cx + k * 10, cy]
    return out


def check(name, got, want):
    ok = got == want
    print("[{}] {}: got {} want {}".format(
        "PASS" if ok else "FAIL", name, got, want))
    return ok


ok = True

# 1) face 0 clipped to w=0 at the right edge, face 1 normal
app = make_pool()
show_all(app)          # pretend the previous frame drew every dot
res = ([[800, 200, 0, 120], [100, 150, 80, 90]],
       [[0] * 10, pts(130, 170)])
drawn, more = app._show_faces(res)
ok &= check("degenerate face 0, drawn", drawn, 1)
ok &= check("degenerate face 0, dots", (visible(app), more),
            (list(range(5, 10)), False))
# Position is checked on the host; get_x() is not guaranteed by this binding.
ok &= check("face 1 dot drawn", 5 in visible(app), True)

# 2) two normal faces, then one: the tail slice must go dark
app = make_pool()
app._show_faces(([[10, 10, 20, 20], [300, 120, 40, 50]],
                 [pts(20, 20), pts(320, 140)]))
ok &= check("two faces, dots", visible(app), list(range(2 * POINTS_PER_FACE)))
drawn, _ = app._show_faces(([[10, 10, 20, 20]], [pts(20, 20)]))
ok &= check("shrink to one", (drawn, visible(app)),
            (1, list(range(POINTS_PER_FACE))))

# 3) no detection at all
app = make_pool()
show_all(app)
drawn, more = app._show_faces(None)
ok &= check("no result", (drawn, more, visible(app)), (0, False, []))

print("RESULT:", "PASS" if ok else "FAIL")

for obj in created:
    try:
        obj.delete()
    except BaseException:
        pass
created = []
