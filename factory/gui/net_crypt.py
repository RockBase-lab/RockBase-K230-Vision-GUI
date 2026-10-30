# WiFi password at-rest wrapper: keeps the plaintext password out of net.json.
#
# Scope (do not treat this as cryptographic security): the key material has to
# travel with the card (the requirement is that a replaced card or a replaced
# unit can still join the network), so it sits in the file. The goal is only that
# the password is not visible in three situations - pulling the card and mounting
# it on a PC, a stolen backup, and a full-text search - not that it resists
# someone reverse engineering the sources under /sdcard/factory/gui/. Any claim
# that it does the latter is self-deception.
#
# Algorithm: SHA256 counter-mode keystream XOR (not AES, and not "encryption" in
# the security sense)
#   key_stream_i = SHA256(_TAG + r + byte(i))
#   c = plaintext ^ key_stream
# - r is a fresh 16-byte random value regenerated on every seal(), stored with
#   the file and never reused -> the same password produces different ciphertext
#   on two cards, and you cannot tell it is the same network.
# - Why SHA256 rather than ucryptolib: hashlib already has proven on-board use in
#   this project, while ucryptolib has only been seen in examples that ship their
#   own ImportError fallback and has no on-board evidence; a password is at most
#   63 bytes, which is two or three SHA256 blocks, so performance is no reason to
#   pull in an unverified dependency.
#
# Plaintext envelope (verified after decryption, so a corrupted ciphertext is
# detected and takes the "not configured" path instead of feeding garbage to the
# connect call):
#   b"RB\x02" + len (2 bytes little endian) + password text
#
# **Changing _TAG or the algorithm invalidates every password stored on every
# deployed device** (users would have to re-enter it once), so think it through
# before changing anything here.

import hashlib

_TAG = b"RockBase/net/v2"
_HDR = b"RB\x02"
R_LEN = 16                     # nonce length (regenerated on every seal)


# ---------------- randomness ----------------

def _rand(n):
    """Get n random bytes. Three sources degrade in turn on board; the last
    fallback only guarantees "different every time"."""
    try:
        import os
        b = os.urandom(n)
        if b and len(b) == n:
            return bytes(b)
    except Exception:
        pass
    try:
        import urandom
        return bytes([urandom.getrandbits(8) for _ in range(n)])
    except Exception:
        pass
    try:
        import time
        seed = "{}-{}-{}".format(time.ticks_us(), time.ticks_ms(), id(_TAG))
    except Exception:
        seed = str(id(_TAG))
    return hashlib.sha256(seed.encode()).digest()[:n]


# ---------------- hex ----------------

def _hex(b):
    try:
        return b.hex()
    except AttributeError:
        return "".join("{:02x}".format(x) for x in b)


def _unhex(s):
    n = len(s) // 2
    out = bytearray(n)
    for i in range(n):
        out[i] = int(s[i * 2:i * 2 + 2], 16)
    return bytes(out)


# ---------------- keystream ----------------

def _ks(r, need):
    """Generate need bytes of keystream: SHA256(_TAG + r + counter) per block."""
    out = b""
    i = 0
    while len(out) < need:
        h = hashlib.sha256()
        h.update(_TAG)
        h.update(r)
        h.update(bytes([i & 0xFF]))
        out += h.digest()
        i += 1
    return out


def _xor(r, data):
    n = len(data)
    ks = _ks(r, n)
    out = bytearray(n)
    for i in range(n):
        out[i] = data[i] ^ ks[i]
    return bytes(out)


# ---------------- public API ----------------

def seal(pwd):
    """Wrap the plaintext password into {"r": nonce hex, "c": ciphertext hex};
    returns None on failure.

    On failure the caller must abandon the write (better to save nothing than to
    fall back to plaintext)."""
    try:
        if isinstance(pwd, bytes):
            raw = pwd
        else:
            raw = (pwd or "").encode()
        n = len(raw)
        body = _HDR + bytes([n & 0xFF, (n >> 8) & 0xFF]) + raw
        r = _rand(R_LEN)
        return {"r": _hex(r), "c": _hex(_xor(r, body))}
    except Exception as e:
        print("[net-crypt] seal failed:", e)
        return None


def unseal(d):
    """Restore the plaintext password from {"r","c"}; returns None when the
    format is wrong or the data was corrupted (= treat as not configured)."""
    try:
        if not isinstance(d, dict):
            return None
        r = _unhex(d.get("r") or "")
        c = _unhex(d.get("c") or "")
    except Exception:
        return None
    if len(r) != R_LEN or not c:
        return None
    try:
        body = _xor(r, c)
    except Exception:
        return None
    if len(body) < 5 or body[0:3] != _HDR:
        return None
    n = body[3] | (body[4] << 8)
    raw = body[5:]
    if len(raw) != n:
        return None
    try:
        return raw.decode()
    except Exception:
        return None
