# Regenerate the two CJK charset files the UI fonts bake from:
#
#   tools/ui_cjk_charset.txt          NAMES + STRINGS (the 16px app face)
#   tools/ui_cjk_desktop_charset.txt  NAMES only       (the 28px tile-name face)
#
# Coverage is guaranteed by construction: gen_font.py takes the characters from
# these exact files, and refresh() below is the only writer. Keeping the 28px
# face on NAMES alone matters: folding the app strings in grew it from 30 KB to
# 138 KB for glyphs that face never renders.
#
# The tables are read by brace-matching and eval, not by a per-line regex: an
# entry written as an implicit multi-line concatenation ("first half "
# "second half") is part of the dict value, and a line pattern misses it
# (measured: four characters were absent from the charset that way).
import io
import os

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "factory", "gui", "core", "i18n.py")
ALL_OUT = os.path.join(HERE, "ui_cjk_charset.txt")
DESKTOP_OUT = os.path.join(HERE, "ui_cjk_desktop_charset.txt")


def _table(src, name):
    """Eval the dict literal assigned to `name` in src."""
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


def _chars(pairs):
    found = set()
    for pair in pairs:
        found.update(ch for ch in pair[1] if ord(ch) > 127)
    return "".join(sorted(found))


def refresh():
    """Rewrite both charset files; returns (all_chars, desktop_chars)."""
    src = io.open(SRC, encoding="utf-8").read()
    names = _table(src, "NAMES")
    strings = _table(src, "STRINGS")
    all_chars = _chars(list(names.values()) + list(strings.values()))
    desktop = _chars(names.values())
    io.open(ALL_OUT, "w", encoding="utf-8").write(all_chars)
    io.open(DESKTOP_OUT, "w", encoding="utf-8").write(desktop)
    print("ui_cjk charset: {} chars ({} strings); desktop: {} chars".format(
        len(all_chars), len(names) + len(strings), len(desktop)))
    return all_chars, desktop


if __name__ == "__main__":
    refresh()
