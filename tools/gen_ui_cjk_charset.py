# Regenerate tools/ui_cjk_charset.txt from the zh half of i18n.NAMES, and check
# that every character is also covered by the preloaded ocr_cjk_16 font (used for
# 16px member labels). Run after editing i18n.py, then re-run gen_font.py ui28cjk
# and gen_tile_cache.py.
import io
import re

src = io.open("factory/gui/i18n.py", encoding="utf-8").read()
pairs = re.findall(r'\("([^"]*)", "([^"]*)"\)', src)
zh = set()
for _en, name in pairs:
    zh.update(c for c in name if ord(c) > 127)
charset = sorted(zh)
io.open("tools/ui_cjk_charset.txt", "w", encoding="utf-8").write("".join(charset))
print("ui_cjk charset: {} chars".format(len(charset)))

ocr = set(io.open("tools/ocr_charset.txt", encoding="utf-8").read())
missing = [c for c in charset if c not in ocr]
print("not covered by ocr_cjk_16:", "".join(missing) if missing else "NONE")
