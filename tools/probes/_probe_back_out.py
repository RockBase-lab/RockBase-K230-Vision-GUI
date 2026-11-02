# Verifies the layered CANCEL back-out on the real launcher.
#
# Walks the whole ladder without physical button events by calling back_out():
#   app -> the folder flyout it came from -> the desktop page with that folder -> page 0
#
# A release_home app is used on purpose: it deletes the home screen, so the pager is
# rebuilt from scratch and the page number has to be restored explicitly. Run with
# --lvgl. The app is closed again before the probe ends.

import gc
import sys
import time

sys.path.insert(0, "/sdcard/factory/gui")
for _name in ("apps.app_face_det", "apps", "apps.error", "ui.beep",
              "ui.paging", "launcher"):
    if _name in sys.modules:
        del sys.modules[_name]

import lvgl as lv

_f = open("/sdcard/factory/gui/launcher.py")
gui_src = _f.read()
_f.close()
del _f

G = {"__name__": "gui_probe"}
exec(compile(gui_src, "launcher.py", "exec"), G)
G["_ensure_cache_dir"]()

launcher = G["Launcher"]()
launcher.build_home()
lv.task_handler()

ok = True


def check(tag, got, want):
    global ok
    good = got == want
    if not good:
        ok = False
    print("[{}] {}: got {} want {}".format(
        "PASS" if good else "FAIL", tag, got, want))


pager = launcher.pager
if pager is None:
    print("B1 no pager: only one page, cannot test")
else:
    print("B1 pages={} page={}".format(pager.page_count, pager.page))
    check("starts on page 0", pager.page, 0)

    # Simulate the user swiping to the page that holds the Face folder.
    pager.go(1, animate=False)
    lv.task_handler()
    check("moved to page 1", pager.page, 1)

    face_meta = None
    for meta in G["APPS"]:
        if meta["id"] == "face":
            face_meta = meta
            break
    print("B2 face tile: {}".format(face_meta["name"]))

    # Open the folder, then act like a member was tapped.
    launcher._open_folder(face_meta)
    lv.task_handler()
    check("folder open", launcher.folder is not None, True)

    member = None
    for m in G["FOLDER_MEMBERS"]["face"]:
        if m["id"] == "face_det":
            member = m
            break
    print("B3 member: {} module={} release_home={}".format(
        member["name"], member["module"], member.get("release_home")))

    # Exactly what the member tile's click handler does.
    launcher.return_folder = launcher.folder["meta"]
    launcher._close_folder()
    launcher.open_app(member)
    lv.task_handler()
    check("app running", launcher.active_app is not None, True)
    print("B4 app up, home_scr deleted={}".format(launcher.home_scr is None))

    # CANCEL inside the app: back to the flyout, on the same desktop page.
    consumed = launcher.back_out()
    # back_out() only requests the exit; the main loop performs it on the next pass.
    # Reproduce that step so the probe follows the real path.
    if launcher.ctx is not None and launcher.ctx.want_exit:
        launcher.close_app()
    time.sleep_ms(200)
    lv.task_handler()
    check("app CANCEL consumed", consumed, True)
    check("app closed", launcher.active_app is None, True)
    check("folder restored", launcher.folder is not None, True)
    check("page preserved after release_home", launcher.pager.page, 1)

    # CANCEL with the flyout open: close it, stay on this page.
    consumed = launcher.back_out()
    lv.task_handler()
    check("flyout CANCEL consumed", consumed, True)
    check("folder closed", launcher.folder is None, True)
    check("still on page 1", launcher.pager.page, 1)

    # CANCEL on the desktop: back to page 0.
    consumed = launcher.back_out()
    lv.task_handler()
    check("desktop CANCEL consumed", consumed, True)
    check("back to page 0", launcher.pager.page, 0)

    # Nothing left: the key is not consumed and stays harmless.
    consumed = launcher.back_out()
    check("no more levels", consumed, False)

    # The desktop step above started a snap animation. Let LVGL finish it before
    # moving the pager again, or the second go() fights the first scroll animation
    # and reports the old page.
    for _ in range(30):
        lv.task_handler()
        time.sleep_ms(20)
    print("B4d settled: page={}".format(launcher.pager.page))

    # A top-level app must not inherit a folder level: it returns to the plain
    # desktop, on whichever page it was opened from. app_placeholder touches no
    # hardware, so this part cannot fail for an unrelated reason.
    #
    # Always read launcher.pager: a release_home app rebuilt the home screen, so the
    # pager is a NEW object and a cached reference would act on the dead one.
    launcher.pager.go(1, animate=False)
    for _ in range(10):
        lv.task_handler()
        time.sleep_ms(20)
    print("B4e positioned on page={}".format(launcher.pager.page))
    top = None
    for meta in G["APPS"]:
        if meta.get("id") == "settings":
            top = meta
            break
    print("B5 top-level tile: {} module={} release_home={}".format(
        top["name"], top["module"], top.get("release_home")))
    print("B5a before open: pager={} page={} home_scr={}".format(
        id(launcher.pager), launcher.pager.page, launcher.home_scr is None))
    launcher.return_folder = None
    launcher.open_app(top)
    lv.task_handler()
    print("B5b after open: pager={} page={} home_scr={} content_y={}".format(
        id(launcher.pager), launcher.pager.page, launcher.home_scr is None,
        launcher.ctx.content_y if launcher.ctx else -1))
    check("top-level app running", launcher.active_app is not None, True)
    launcher.ctx.request_exit()
    launcher.close_app()
    lv.task_handler()
    print("B5c after close: pager={} page={} home_page={} home_scr={}".format(
        id(launcher.pager), launcher.pager.page, launcher.home_page,
        launcher.home_scr is None))
    check("no flyout for a top-level app", launcher.folder, None)
    check("page kept for a top-level app", launcher.pager.page, 1)

print("B6 mem_free={}".format(gc.mem_free()))
print("RESULT:", "PASS" if ok else "FAIL")
print("ALL DONE")
