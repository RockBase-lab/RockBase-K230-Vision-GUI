# Dump the OCR dictionary's distinct character set as base64 blocks, so the host can
# compare it against the baked font charset.
#
# The recognition model can only emit classes that exist in dict.txt (CTC argmax over
# the vocabulary), so this file defines exactly what the model can recognise. The
# baked font defines what can be drawn. The difference between the two is the set of
# characters that recognise correctly and then render as a box.

import ubinascii

DICT_PATH = "/sdcard/examples/utils/dict.txt"

words = open(DICT_PATH, "r").read(100000).split("\r\n")

seen = set()
for w in words:
    for ch in w:
        if ch:
            seen.add(ch)

chars = "".join(sorted(seen))
print("D1 entries={} distinct={}".format(len(words), len(chars)))

hanzi = [c for c in chars if 0x4E00 <= ord(c) <= 0x9FFF]
other = [c for c in chars if not (0x4E00 <= ord(c) <= 0x9FFF)]
print("D2 hanzi={} other={}".format(len(hanzi), len(other)))

# GB2312 level 1 is 3755 hanzi; level 2 is 3008. Nothing in this firmware tells us
# which block a character came from, so the host does that comparison.
raw = chars.encode("utf-8")
print("D3 bytes={}".format(len(raw)))
CHUNK = 96
for i in range(0, len(raw), CHUNK):
    b64 = ubinascii.b2a_base64(raw[i:i + CHUNK]).decode().strip()
    print("B64 " + b64)
print("END")
