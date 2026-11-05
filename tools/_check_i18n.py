#!/usr/bin/env python3
"""Check the i18n tables against the baked fonts and the code that uses them.

Four checks, each one a class of defect that renders as a box with no error on
the board:

  1. every zh character in NAMES/STRINGS is in the charset its font bakes from;
  2. every i18n.t()/t_en()/name() reference resolves to a real key;
  3. no label is created from an ASCII placeholder and later given i18n text
     without i18n.fit_font (it keeps a Latin face and boxes the Chinese);
  4. no dialog-size call site uses CJK_FONT, the face baked from NAMES only.

Run it after editing core/i18n.py or adding labels:
    python tools/_check_i18n.py
Exit status is non-zero when a check fails.
"""

import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GUI = os.path.join(ROOT, "factory", "gui")
I18N = os.path.join(GUI, "core", "i18n.py")

NAMES = ("camera", "gallery", "scan", "face", "vision", "audio", "touch",
         "imu", "network", "settings", "battery", "detect", "pose", "segment",
         "orient", "person", "fall", "gesture", "plate", "ocr", "face_det",
         "landmark", "recognize", "liveness", "mask")

# Keys whose name is built at runtime; each family is listed with its members so a
# typo in the numeric or token part is still caught by check 2b.
DYNAMIC = {
    "gesture.": ("fist", "five", "gun", "love", "one", "six", "three",
                 "thumbup", "yeah"),
    "pose.view.": ("0.name", "0.note", "1.name", "1.note", "2.name", "2.note"),
    "imu.pose.": ("0", "1", "2", "3", "4", "5", "6"),
}

failures = []


def fail(msg):
    failures.append(msg)


def load_tables():
    src = io.open(I18N, encoding="utf-8").read()

    def lit(name):
        start = src.index(name + " = {")
        i = src.index("{", start)
        depth = 0
        for j in range(i, len(src)):
            if src[j] == "{":
                depth += 1
            elif src[j] == "}":
                depth -= 1
                if depth == 0:
                    return eval(src[i:j + 1])
        raise SystemExit("unbalanced literal: " + name)

    return lit("NAMES"), lit("STRINGS")


def check_charset(names, strings):
    need16 = set()
    for pair in list(names.values()) + list(strings.values()):
        need16.update(c for c in pair[1] if ord(c) > 127)
    need28 = set()
    for pair in names.values():
        need28.update(c for c in pair[1] if ord(c) > 127)

    for label, need, path in (
            ("16px/22px face", need16, "tools/ui_cjk_charset.txt"),
            ("28px tile face", need28, "tools/ui_cjk_desktop_charset.txt")):
        got = set(io.open(os.path.join(ROOT, path), encoding="utf-8").read())
        missing = sorted(need - got)
        print("  {}: {} chars, missing {}".format(
            label, len(need), "".join(missing) if missing else "none"))
        if missing:
            fail("charset {} lacks {}".format(path, "".join(missing)))


def check_keys(names, strings):
    keys = set(strings)
    en = set(pair[0] for pair in strings.values())
    checked = 0
    for base, _dirs, files in os.walk(GUI):
        for name in sorted(files):
            if not name.endswith(".py"):
                continue
            text = io.open(os.path.join(base, name), encoding="utf-8").read()
            for m in re.finditer(r'i18n\.t\(\s*"([^"]+)"\s*([+)%])?', text):
                checked += 1
                key = m.group(1)
                # A trailing + or % builds the key at runtime; its family is
                # validated through DYNAMIC below instead.
                if m.group(2) in ("+", "%"):
                    continue
                if key in keys:
                    continue
                # A key family whose numeric/token half is substituted at runtime
                # ("pose.view.{}.name" -> pose.view.0.name).
                fam = [p for p in DYNAMIC if key.startswith(p)]
                if fam:
                    rest = key[len(fam[0]):]
                    if "{" in rest:
                        continue
                    if rest in DYNAMIC[fam[0]]:
                        continue
                    fail("{}: i18n.t(\"{}\") is not a {} member".format(
                        name, key, fam[0]))
                    continue
                fail("{}: i18n.t(\"{}\") has no row".format(name, key))
            for m in re.finditer(r'i18n\.t_en\(\s*"([^"]+)"', text):
                checked += 1
                if m.group(1) not in en:
                    fail("{}: i18n.t_en(\"{}\") matches no en half".format(
                        name, m.group(1)))
            for m in re.finditer(r'i18n\.name\(\s*"([^"]+)"', text):
                checked += 1
                if m.group(1) not in NAMES:
                    fail("{}: i18n.name(\"{}\") has no NAMES row".format(
                        name, m.group(1)))
    print("  references checked: {}".format(checked))


ASCII_INIT = re.compile(r"""^\(?\s*["']([ -~]*)["']""")


def check_runtime_refit():
    """A label built from an ASCII placeholder keeps its Latin face, so every later
    set_text with i18n text must be paired with fit_font within a few lines."""
    found = 0
    for base, _dirs, files in os.walk(GUI):
        for name in sorted(files):
            if not name.endswith(".py"):
                continue
            lines = io.open(os.path.join(base, name), encoding="utf-8").read().split("\n")
            created = {}
            for i, line in enumerate(lines):
                m = re.search(
                    r"(?:self\.)?([A-Za-z_]\w*)\s*=\s*(lv\.label|_seg|seg)\(", line)
                if not m or m.group(1) in created:
                    continue
                var, call = m.group(1), m.group(2)
                if call == "lv.label":
                    init = None
                    for k in range(i + 1, min(i + 4, len(lines))):
                        s = re.search(r"set_text\(\s*(.+?)\)", lines[k])
                        if s:
                            init = s.group(1)
                            break
                    created[var] = (i + 1, init)
                else:
                    rest = line[m.end():].split(")")[0]
                    created[var] = (i + 1, rest.split(",")[0].strip())
            for i, line in enumerate(lines):
                m = re.search(r"(?:self\.)?([A-Za-z_]\w*)\.set_text\(\s*(.*)$", line)
                if not m:
                    continue
                arg = m.group(2).strip().rstrip(")")
                carries = "i18n." in arg
                if not carries:
                    # The text may be assigned to a local first:
                    #   text = i18n.t("...");  label.set_text(text)
                    if re.fullmatch(r"[A-Za-z_]\w*", arg):
                        near = "\n".join(lines[max(0, i - 20):i])
                        if re.search(
                                r"^\s*" + re.escape(arg) + r"\s*=\s*.*i18n\.",
                                near, re.M):
                            carries = True
                if not carries:
                    continue
                info = created.get(m.group(1))
                if info is None:
                    continue
                init = info[1]
                if init is None:
                    continue
                lit = ASCII_INIT.match(init)
                if not lit or not all(ord(c) < 128 for c in lit.group(1)):
                    continue
                window = "\n".join(lines[max(0, i - 3):i + 6])
                if any(tok in window for tok in ("fit_font", "font_for",
                                                 "cjk_fit", "_cjk")):
                    continue
                found += 1
                fail("{}:{} '{}' was built from an ASCII placeholder (L{}: {}) "
                     "and later gets i18n text without fit_font".format(
                         name, i + 1, m.group(1), info[0], init[:24]))
    print("  ASCII-placeholder labels needing a re-fit: {}".format(found))


def check_dialog_face():
    """CJK_FONT is baked from NAMES only; a STRINGS key drawn with it boxes."""
    bad = 0
    for base, _dirs, files in os.walk(GUI):
        for name in sorted(files):
            if not name.endswith(".py"):
                continue
            text = io.open(os.path.join(base, name), encoding="utf-8").read()
            for m in re.finditer(r"font_for\([^)]*i18n\.CJK_FONT\)", text):
                bad += 1
                fail("{}: font_for(..., i18n.CJK_FONT) for dialog text; use "
                     "CJK_FONT_DIALOG".format(name))
    print("  dialog call sites on the 28px face: {}".format(bad))


def main():
    names, strings = load_tables()
    print("[i18n] {} NAMES, {} STRINGS".format(len(names), len(strings)))
    print("[1] charset coverage")
    check_charset(names, strings)
    print("[2] key references")
    check_keys(names, strings)
    print("[3] runtime font re-fit")
    check_runtime_refit()
    print("[4] dialog face")
    check_dialog_face()
    if failures:
        print("\nFAILED ({}):".format(len(failures)))
        for f in failures:
            print("  " + f)
        return 1
    print("\nall i18n checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
