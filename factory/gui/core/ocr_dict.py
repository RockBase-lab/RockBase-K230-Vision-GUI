# OCR recognition dictionary, loaded once per process.
#
# 6549 entries separated by CRLF; the last entry is the CTC blank. Loading it costs
# 239 ms, so the boot sequence fills this cache and app_ocr reads it as a lookup.

import time

DICT_PATH = "/sdcard/examples/utils/dict.txt"

_words = None
_error = ""


def words():
    """Return the dictionary as a list of strings, or None when it cannot be read."""
    global _words, _error
    if _words is not None:
        return _words
    if _error:
        return None
    t0 = time.ticks_ms()
    try:
        with open(DICT_PATH, "r") as f:
            _words = f.read(100000).split("\r\n")
    except BaseException as error:
        _error = "{} ({})".format(DICT_PATH, error)
        print("[DICT] load failed: {}".format(_error))
        return None
    print("[DICT] {} entries {}ms".format(
        len(_words), time.ticks_diff(time.ticks_ms(), t0)))
    return _words


def error():
    return _error


def is_loaded():
    return _words is not None
