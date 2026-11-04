# Build factory/gui/audio/pinyin_map.py: pinyin syllable -> one Han character,
# so a pinyin name typed on the ASCII keyboard can be spoken.
#
# The zh TTS frontend reads Han characters through its own dictionaries and
# produces silence for Latin text, so a name has to be converted first. Only the
# pronunciation matters, so any character with the right syllable works -- but
# the character should be one the frontend knows, hence the small_pinyin phrase
# dictionary is used as a whitelist of common characters.
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# The two inputs are the firmware's own TTS dictionaries, which ship inside the
# image under /sdcard/examples/utils and are not part of this repository. Point
# --src at a directory holding them, or drop copies next to this tool; the old
# behaviour was a hard-coded absolute path, which silently wrote the output to
# the wrong tree on any other checkout.
DEFAULT_SRC = os.path.join(ROOT, "tools", "pinyin_src")

_src = None
for _i, _arg in enumerate(sys.argv):
    if _arg == "--src" and _i + 1 < len(sys.argv):
        _src = sys.argv[_i + 1]
PINYIN_SRC = os.path.join(_src or DEFAULT_SRC, "pinyin.txt")
PHRASE_SRC = os.path.join(_src or DEFAULT_SRC, "small_pinyin.txt")
DST = os.path.join(ROOT, "factory", "gui", "audio", "pinyin_map.py")

# The dictionaries are firmware resources, so they are not in this repository.
# Fail with the invocation instead of a bare FileNotFoundError.
for _p in (PINYIN_SRC, PHRASE_SRC):
    if not os.path.exists(_p):
        sys.exit("[FAIL] missing %s\n"
                 "       Pass --src <dir holding pinyin.txt and small_pinyin.txt>,\n"
                 "       or copy both files into %s" % (_p, DEFAULT_SRC))

# Tone-marked vowels back to their plain form.
TONE = {
    "\u0101": "a", "\u00e1": "a", "\u01ce": "a", "\u00e0": "a",
    "\u0113": "e", "\u00e9": "e", "\u011b": "e", "\u00e8": "e",
    "\u012b": "i", "\u00ed": "i", "\u01d0": "i", "\u00ec": "i",
    "\u014d": "o", "\u00f3": "o", "\u01d2": "o", "\u00f2": "o",
    "\u016b": "u", "\u00fa": "u", "\u01d4": "u", "\u00f9": "u",
    "\u01d6": "u", "\u01d8": "u", "\u01da": "u", "\u01dc": "u",
    "\u00fc": "u", "\u01d5": "u", "\u01d9": "u", "\u01db": "u", "\u01dd": "u",
    "\u0144": "n", "\u0148": "n", "\u1e3f": "m",
}


def toneless(text):
    return "".join(TONE.get(ch, ch) for ch in text)


def common_chars():
    """Character frequency in the phrase dictionary.

    Frequency is the proxy for "common": the dictionary holds ordinary words,
    so a glyph that appears often is one the frontend certainly knows.
    """
    counts = {}
    with io.open(PHRASE_SRC, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or ":" not in line:
                continue
            phrase = line.split(":", 1)[0]
            for ch in phrase:
                if 0x4E00 <= ord(ch) <= 0x9FFF:
                    counts[ch] = counts.get(ch, 0) + 1
    return counts


known = common_chars()
print("common chars in phrase dict:", len(known))

# A character with several readings is pronounced with its common one, not
# necessarily the syllable we want (共 has gong and hong, and would say "gong").
# So single-reading characters are preferred and multi-reading ones are only a
# last resort.
readings = {}
best = {}
with io.open(PINYIN_SRC, "r", encoding="utf-8", errors="replace") as f:
    entries = []
    for line in f:
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        head, rest = line.split(":", 1)
        head = head.strip()
        if not head.upper().startswith("U+"):
            continue
        try:
            cp = int(head[2:], 16)
        except ValueError:
            continue
        if not (0x4E00 <= cp <= 0x9FFF):
            continue
        body = rest.split("#", 1)[0].strip()
        if not body:
            continue
        syls = []
        for item in body.split(","):
            syl = toneless(item.strip())
            if syl and syl.isalpha():
                syls.append(syl)
        if syls:
            entries.append((chr(cp), syls))

    for ch, syls in entries:
        single = len(set(syls)) == 1
        for syl in set(syls):
            score = known.get(ch, 0)
            # Preference order: single reading and common, then any common, then
            # single reading, then anything. Within a tier the most frequent
            # character wins, so the glyph is one the frontend certainly knows.
            if single and score:
                key = (2, score)
            elif score:
                key = (1, score)
            elif single:
                key = (0, 0)
            else:
                key = (-1, 0)
            cur = best.get(syl)
            if cur is None or key > cur[0]:
                best[syl] = (key, ch)

table = {syl: pick[1] for syl, pick in best.items()}
common_syls = sorted(syl for syl, ch in table.items() if ch in known)
print("syllables:", len(table), "| common syllables:", len(common_syls))

lines = [
    "# Pinyin syllable -> one representative Han character.",
    "#",
    "# The zh TTS frontend reads Han characters through its dictionaries and",
    "# emits silence for Latin text, so an ASCII name has to be converted before",
    "# it can be spoken. Only pronunciation matters: any character carrying the",
    "# right syllable produces the right sound. Characters are taken from the",
    "# phrase dictionary first, so the glyph is one the frontend already knows.",
    "#",
    "# Generated by tools/gen_pinyin_map.py. Do not edit by hand.",
    "",
    "PINYIN_CHAR = {",
]
for syl in sorted(table):
    lines.append('    "{}": "{}",'.format(syl, table[syl]))
lines.append("}")
lines.append("")
lines.append("# Syllables whose character is in the frontend's phrase dictionary.")
lines.append("# Segmentation prefers paths built from these, so `nina` becomes ni+na")
lines.append("# (你+那) rather than nin+a (囜+锕).")
lines.append("COMMON = frozenset([")
for syl in common_syls:
    lines.append('    "{}",'.format(syl))
lines.append("])")
lines.append("")

with io.open(DST, "w", encoding="utf-8", newline="\n") as f:
    f.write("\n".join(lines))
print("wrote", DST, os.path.getsize(DST), "bytes")
