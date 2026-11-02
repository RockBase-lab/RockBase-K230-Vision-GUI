# Print, as base64, the dictionary characters outside the GB2312 level-1 hanzi
# block, so the host can extend the font charset with exactly the punctuation and
# symbols the recognition model can emit. Base64 keeps the transfer pure ASCII:
# the host shell cannot round-trip the CJK and symbol characters.

import ubinascii

DICT_PATH = "/sdcard/examples/utils/dict.txt"

words = open(DICT_PATH, "r").read(100000).split("\r\n")

extra = []
seen = set()
for w in words:
    for ch in w:
        if not ch or ch in seen:
            continue
        seen.add(ch)
        o = ord(ch)
        if 0x4E00 <= o <= 0x9FFF:
            continue
        if o > 0xFFFF:          # non-BMP: a surrogate pair in the font tool
            continue
        extra.append(ch)

raw = "".join(extra).encode("utf-8")
# Wrap so each serial line stays short enough for the guard.
chunk = 48
for i in range(0, len(raw), chunk):
    print("B64 {}".format(ubinascii.b2a_base64(raw[i:i + chunk]).decode().strip()))
print("END")
