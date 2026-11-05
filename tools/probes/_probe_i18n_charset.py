# Render every character in tools/ui_cjk_charset.txt with the 16px UI face and
# capture the grid. A missing glyph draws LVGL's fallback box, which is visible
# here, so this is the end-to-end proof that no i18n string can box.
# Run with --lvgl.
import sys

sys.path.insert(0, "/sdcard/factory/gui")
for _name in list(sys.modules):
    if _name.split(".")[0] in ("core", "ui", "shell", "apps", "launcher"):
        del sys.modules[_name]

import lvgl as lv
import ubinascii
import uctypes

from core import font_pool
from core import i18n


def shot(tag):
    for _ in range(3):
        lv.task_handler()
    dsc = lv.snapshot_take(lv.scr_act(), lv.COLOR_FORMAT.ARGB8888)
    w, h = dsc.header.w, dsc.header.h
    ptr = int.from_bytes(bytes(memoryview(dsc.data)), "little")
    raw = uctypes.bytearray_at(ptr, w * h * 4)
    out = bytearray()
    for y in range(h):
        base = y * w * 4
        run = 1
        b0, g0, r0 = raw[base], raw[base + 1], raw[base + 2]
        for x in range(1, w):
            o = base + x * 4
            b, g, r = raw[o], raw[o + 1], raw[o + 2]
            if b == b0 and g == g0 and r == r0 and run < 256:
                run += 1
            else:
                out.append(run - 1)
                out.append(b0)
                out.append(g0)
                out.append(r0)
                b0, g0, r0 = b, g, r
                run = 1
        out.append(run - 1)
        out.append(b0)
        out.append(g0)
        out.append(r0)
    dsc.buf_free()
    print("SHOT {} {} {} {} {}".format(tag, w, h, len(out), w * h * 4))
    b64 = ubinascii.b2a_base64(bytes(out)).decode().strip()
    for i in range(0, len(b64), 800):
        print("B64 " + b64[i:i + 800])
    print("SHOTEND " + tag)


font = font_pool.get("ui_cjk_16.bin")
print("[FONT] ui_cjk_16 ok={}".format(font is not None))

# Derive the set from the tables themselves, so the probe renders exactly what the
# UI can ask for.
chars = set()
for table in (i18n.NAMES, i18n.STRINGS):
    for _en, zh in table.values():
        chars.update(c for c in zh if ord(c) > 127)
charset = "".join(sorted(chars))
print("[SET] {} chars from {} entries".format(
    len(charset), len(i18n.NAMES) + len(i18n.STRINGS)))

scr = lv.obj()
scr.set_style_bg_color(lv.color_hex(0x12141A), 0)
scr.set_style_bg_opa(lv.OPA.COVER, 0)
lv.scr_load(scr)

PER_ROW = 34
rows = (len(charset) + PER_ROW - 1) // PER_ROW
y = 4
for r in range(rows):
    chunk = charset[r * PER_ROW:(r + 1) * PER_ROW]
    lab = lv.label(scr)
    lab.set_text(chunk)
    lab.set_style_text_color(lv.color_hex(0xFFFFFF), 0)
    if font is not None:
        lab.set_style_text_font(font, 0)
    lab.set_pos(6, y)
    y += 21
print("[SET] rows={} bottom={}".format(rows, y))

# Also render the strings that are drawn by a CJK face chosen at runtime, so a
# font_for miss would show up here rather than in one app.
samples = [
    "识别 无人脸", "人脸 0.85", "目标 3", "已冻结", "点击扫描",
    "正在加载模型 3/8", "已录入人脸 (2)", "语音 启动名字",
]
y += 8
for text in samples:
    nxt = lv.label(scr)
    nxt.set_text(text)
    nxt.set_style_text_color(lv.color_hex(0x7FB6D9), 0)
    if font is not None:
        nxt.set_style_text_font(font, 0)
    nxt.set_pos(6, y)
    y += 22

shot("charset_16")
print("PROBE_DONE")
