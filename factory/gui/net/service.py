# Network service singleton: WiFi connect/persist, timezone, NTP sync.
#
# Responsibility boundary: this module belongs to no app. launcher.py calls start() at
# boot and poll() once per frame in the main loop. app_network is only its
# configuration entry point; leaving the app does not affect the link (WLAN is a
# firmware-global interface: once up it stays active until someone calls
# disconnect -- confirmed on board).
#
# On-board constraints that shaped the implementation (all of them were hit):
# - time.tzset does not exist; machine.RTC has timezone(-12..12 whole hours) and
#   datetime, and time.localtime() follows both. rtc.datetime() reads and writes
#   *local* time (the tz is already baked in), so after NTP yields a UTC epoch we
#   must localtime() it first and only then write it.
# - **wlan.connect() to an unreachable AP blocks ~9s before raising** (measured).
#   Calling connect on the GUI main thread freezes the UI ~9s per failure, and
#   touching WLAN from both the main thread and a probe concurrently crashes the
#   driver (measured: after the crash even constructing WLAN(STA_IF) raises
#   TypeError and only a power cycle recovers).
#   Decision: connect / scan / NTP all run in _thread, and only one owner touches
#   WLAN at a time -- while connecting, poll() never calls isconnected(); the
#   worker polls it and fills in the result itself.
# - wlan.connect() returns immediately on success (a reachable AP does not
#   block); only failure blocks until the driver timeout.
# - scan() returns a list of dicts: {ssid,bssid,channel,rssi,security,band,hidden};
#   multiple BSSIDs of the same SSID are de-duplicated by signal.
# - Threads never touch LVGL: LVGL is not thread-safe here, so any cross-thread
#   call corrupts its state.
#
# Attempt-budget semantics (user requirement, reworked 2026-10-28): each power-on
# gets MAX_TRIES attempts, spaced RETRY_DELAY_MS apart. When the budget is used
# up the module disconnects and stops all automatic retries (state GAVE_UP, no
# further attempts until the next power-on). The counter lives only in memory, so
# a reboot restores the budget (booting in a different environment or after the
# router recovers still auto-connects). Only a manual connect in the Network app
# resets the counter and revives the service immediately.
# Why it was tightened: the wlan.connect() failure path holds the GIL for a long
# time (~9s of driver blocking), so 5 back-to-back retries meant over a minute of
# continuous stutter with the status bar stuck on "connecting". 3 attempts with a
# 10s gap split that into three short pulses with a responsive UI in between.
#
# Unconfigured semantics (second revision 2026-10-30, user requirement): no
# net.json on the SD card means the user never configured WiFi in the Network
# app, so **no connection is attempted at all** -- start() reads the config,
# applies the timezone and returns without touching the radio or starting a
# worker. Rationale: wlan.connect() to an unreachable AP blocks ~9s (C layer,
# cannot be shortened from Python), and a freshly powered-on unit usually has no
# network configured yet, so waiting is pure startup latency. After the user
# connects once, net.json holds an ssid and every later power-on retries within
# the MAX_TRIES budget.
# Corollary: while unconfigured, no persistence path may **create** net.json
# (see save(create)). An earlier version wrote the ntp_idx cache to disk right
# after a successful boot-time NTP sync, so "delete net.json, reboot, it grows
# back"; that file also had an empty ssid, so the next boot treated it as
# "configured" -> start() saw an empty ssid and went idle, and WiFi never
# connected again. Both holes are closed in load/save.
#
# On-disk format v2 (2026-10-30): the password is no longer stored in plain text;
# seal() wraps it into "pwd":{"r","c"} with a fresh nonce on every write.
# ssid/tz/ntp_idx stay plain -- scanning and the UI need them, and encrypting them
# would only cost debugging time without adding protection. A v1 file (top-level
# "password") is used as-is in memory and immediately rewritten as v2, invisible
# to the user. Undecryptable ciphertext means "unconfigured" (no connect, the
# user just types the password again).

import hashlib
import json
import time


CFG_PATH = "/sdcard/factory/net.json"
CFG_VER = 2                    # on-disk format version (v1 = plain-text password)

MAX_TRIES = 3                  # connection-failure budget per power-on; exhausted = service stops
CONNECT_WAIT_MS = 5000         # upper bound for waiting on isconnected after connect().
                               # Note: wlan.connect() itself blocks ~9s on an
                               # unreachable AP (C layer, cannot be shortened from
                               # Python); this timeout only covers the wait after
                               # connect returns.
RETRY_DELAY_MS = 10000         # gap between one failure and the next attempt
NTP_INTERVAL_MS = 6 * 3600 * 1000   # 6h automatic re-sync
NTP_FIRST_DELAY_MS = 4000      # delay before the first NTP after connect (DHCP/router settling)
NTP_RETRY_MS = 10 * 60 * 1000  # retry gap after the whole server pool fails

# Deep scan: scan() always returns 0 entries while connected (on-board A/B: 3/3
# runs returned 0 entries in 0ms; disconnected, 3/3 runs took ~1.5s and returned
# 9~13 entries). The only way to get a real AP list is to drop the link, scan,
# then reconnect with the stored credentials (measured: disconnect -> scan ->
# reconnect takes about 6s).
SCAN_PAUSE_MS = 600            # let the radio settle after disconnecting
SCAN_CACHE_TTL_MS = 5 * 60 * 1000   # no automatic deep scan within this window

# Timezone pool: one NTP server per continent. The starting point rotates by
# ntp_idx, and one success makes that server the next starting point (nearest
# first).
NTP_SERVERS = [
    ("Asia", "ntp.aliyun.com"),
    ("Europe", "europe.pool.ntp.org"),
    ("North America", "north-america.pool.ntp.org"),
    ("South America", "south-america.pool.ntp.org"),
    ("Africa", "africa.pool.ntp.org"),
    ("Oceania", "oceania.pool.ntp.org"),
]

# State machine (visible to the UI)
IDLE = "idle"                  # unconfigured / manually disconnected
CONNECTING = "connecting"      # worker is connecting (main loop stays off WLAN)
CONNECTED = "connected"
GAVE_UP = "gave_up"            # budget exhausted, service stopped

_wlan = None
state = IDLE
cfg = {}
# Whether the user ever configured WiFi (net.json has a usable ssid plus a
# decryptable password). False = no connect this power-on (see "unconfigured
# semantics" at the top).
_configured = False
# Whether cfg's ssid/password came from user input or a stored file (rather than
# factory defaults). Only when True will save() write credentials to disk.
_creds_user_set = False
_last_ntp_ms = 0
last_error = ""
last_ntp_text = "-"
# UTC epoch and server continent of the last successful sync: used to
# re-render last_ntp_text locally after a timezone change (it shows the
# "last sync instant", and changing timezone needs no network).
_last_ntp_epoch = None
_last_ntp_region = ""

# Sole WLAN owner: while _worker_busy=True the main loop never touches _wlan.
_worker_busy = False
_connect_result = None         # filled by the worker: True=up / False=failed
_ntp_pending = None            # filled by the worker: None=none / False=failed / (idx,epoch)
_scan_busy = False
_scan_result = None            # filled by the worker: [(ssid, rssi, security), ...]
_scan_cache = None             # last non-empty scan result (fallback while connected, when scan is always empty)
_scan_cache_ms = 0             # cache write time (TTL check, avoids dropping the radio repeatedly)
_scan_force = False            # this scan was user-initiated (deep scan allowed)
_ip_hold = ""                  # IP kept during a deep-scan disconnect so the UI does not flash "-"
# Attempt budget: memory-only -- the count restarts on every power-on (a reboot
# restores the budget, so booting in another environment or after the router
# recovers still auto-connects); when spent it goes GAVE_UP until the user
# connects manually.
_tries = 0                     # failed connect attempts this power-on
_retry_at = 0                  # time of the next automatic retry (0 = nothing pending)


# ---------------- Config persistence (SD card) ----------------

def load():
    global cfg, _configured, _creds_user_set
    _configured = False
    _creds_user_set = False
    try:
        with open(CFG_PATH) as f:
            loaded = json.loads(f.read())
        if not isinstance(loaded, dict):
            loaded = {}
    except Exception:
        loaded = {}

    pwd = None
    legacy = False
    if loaded.get("v") == CFG_VER:
        # v2: rebuild the password from pwd.r / pwd.c. Undecryptable means the
        # file was damaged or the algorithm changed; treat it as unconfigured
        # (no connect, the user retypes the password in the app).
        pwd = unseal(loaded.pop("pwd", None))
        # Ciphertext alone is not enough: a file with an empty ssid can only be a
        # settings file left by a timezone change or a sync, holding no WiFi
        # credentials, so it must not count as configured.
        if pwd is not None and loaded.get("ssid"):
            _configured = True
    elif "password" in loaded:
        # v1 plain-text file: use it as-is in memory, then rewrite it as v2
        # right below (costs one extra disk write).
        pwd = loaded.pop("password") or ""
        legacy = True
        _configured = bool(loaded.get("ssid"))

    if _configured:
        cfg = loaded
        cfg["password"] = pwd
    else:
        # Never configured: do not connect (see "unconfigured semantics" at the
        # top). Empty ssid/password make start() go idle; tz/ntp_idx and other
        # settings in the file are still honoured.
        cfg = loaded
        cfg["ssid"] = ""
        cfg["password"] = ""
    cfg.setdefault("tz", 8)
    # Older versions persisted tries, which made "budget exhausted last power-on"
    # last forever. The new semantics restart the count each power-on, so the
    # leftover key is dropped here.
    cfg.pop("tries", None)
    cfg.pop("v", None)
    cfg.setdefault("ntp_idx", 0)
    _creds_user_set = _configured
    if legacy and _configured:
        # Plain text -> ciphertext; the migration completes on one boot. A failed
        # write does not affect this session's networking (the password is
        # already in memory); the next boot tries again.
        save()
    return cfg


def saved_ssid():
    """SSID the user saved; "" when never configured (no AP is marked saved)."""
    if not _configured:
        return ""
    return cfg.get("ssid", "")


def _cfg_exists():
    try:
        import os
        os.stat(CFG_PATH)
        return True
    except Exception:
        return False


def save(create=True):
    """Write the config back to the SD card.

    create=False means "update an existing file only, never conjure one". The
    boot-time NTP path must use it when caching ntp_idx: if the user never
    configured WiFi, net.json must not appear on the SD card (otherwise deleting
    it and rebooting grows it back, and the next boot sees "a file exists" and
    wrongly assumes a configured unit).

    Only explicit user actions (connecting WiFi or changing the timezone in the
    Network app) may create the file.
    """
    global _configured
    ssid = cfg.get("ssid", "")
    pwd = cfg.get("password", "")
    if not _creds_user_set:
        # WiFi never configured: save settings such as tz only, leaving the
        # credential fields empty. Otherwise the next boot reads the bogus ssid
        # from the file and connects to a network the user never picked.
        ssid = ""
        pwd = ""
        if not create and not _cfg_exists():
            return False
    sealed = seal(pwd)
    if sealed is None:
        # Sealing failed, so skip the write: losing this change beats falling
        # back to plain text.
        print("[net] password seal failed, config not saved")
        return False
    out = {"v": CFG_VER,
           "ssid": ssid,
           "tz": cfg.get("tz", 8),
           "ntp_idx": cfg.get("ntp_idx", 0),
           "pwd": sealed}
    data = json.dumps(out)
    # On-board measurement: on fatfs, if some other code left a raw file handle
    # open (open(p).read() without close), open("w") on the same file raises
    # EPERM until that handle is collected. So on failure we run gc.collect() to
    # reclaim stray handles and retry once, making persistence immune to that
    # ordering.
    for attempt in range(2):
        try:
            with open(CFG_PATH, "w") as f:
                f.write(data)
            # Only real credentials on disk count as "user configured"; a
            # settings-only file holding tz/ntp_idx does not (otherwise the UI
            # would show an SSID the user never picked as a saved network).
            _configured = _creds_user_set
            return True
        except Exception as e:
            if attempt == 0:
                import gc
                gc.collect()
                continue
            print("[net] config save failed:", e)
            return False


# ---------------- WLAN ----------------

def _get_wlan():
    global _wlan
    if _wlan is None:
        import network
        _wlan = network.WLAN(network.STA_IF)
        _wlan.active(True)
    return _wlan


def _format_ntp_text():
    """Re-render the last sync's UTC epoch as display text for the current
    timezone (local only, no network).

    On-board measurement: time.localtime(epoch) applies no timezone and returns
    UTC fields, so tz*3600 is added by hand. Without a sync yet (epoch None) the
    text stays "-".
    """
    global last_ntp_text
    if _last_ntp_epoch is None:
        return
    tz = int(cfg.get("tz", 0))
    tm = time.localtime(_last_ntp_epoch + tz * 3600)
    last_ntp_text = "{:04}-{:02}-{:02} {:02}:{:02}".format(
        tm[0], tm[1], tm[2], tm[3], tm[4])


def apply_tz(tz=None):
    """Write the timezone into the RTC. CanMV's timezone accepts whole hours
    within -12..12 only.

    On success last_ntp_text is re-rendered as well: changing the timezone only
    changes the display offset, the last sync instant is still the same UTC
    moment, so the new timezone must show immediately without going online
    (previously the ntp row waited for the next sync).
    """
    if tz is None:
        tz = cfg.get("tz", 0)
    try:
        import machine
        machine.RTC().timezone(int(tz))
        _format_ntp_text()
        return True
    except Exception as e:
        print("[net] timezone failed:", e)
        return False


def _start_connect(ssid, password):
    """Start a connect worker (never blocks the main loop). Bumps this power-on's
    counter (memory only, not persisted)."""
    global state, _worker_busy, _connect_result, _tries, _retry_at
    _tries += 1
    _retry_at = 0
    state = CONNECTING
    _worker_busy = True
    _connect_result = None
    print("[net] connect '{}' attempt {}/{}".format(ssid, _tries, MAX_TRIES))
    import _thread
    _thread.start_new_thread(_connect_worker, (ssid, password))


def _connect_worker(ssid, password):
    """Connect in a _thread and poll isconnected until success or timeout.

    connect() to an unreachable AP blocks ~9s (measured), so this must never run
    on the main thread. While it runs, poll() sees _worker_busy=True and stays
    off WLAN, so concurrent access cannot crash the driver.

    Scan once first: on board, only the disconnected state produces a list (once
    associated the radio locks a channel and scan always returns 0). The result
    is cached in _scan_cache so the UI can fall back to it when scan is pressed
    while connected instead of showing an empty list forever.
    """
    global _worker_busy, _connect_result, last_error, _scan_cache
    w = _get_wlan()
    try:
        parsed = _parse_scan(w.scan())
        if parsed:
            _scan_cache = parsed
    except Exception:
        pass
    ok = False
    err = ""
    try:
        w.connect(ssid, password)
    except Exception as e:
        err = str(e)
    deadline = time.ticks_add(time.ticks_ms(), CONNECT_WAIT_MS)
    while time.ticks_diff(deadline, time.ticks_ms()) > 0:
        try:
            if w.isconnected():
                ok = True
                break
        except Exception:
            pass
        time.sleep_ms(300)
    # The driver often does not raise on an association failure (it only logs
    # "run connect failed."), leaving last_error empty or stale so the UI has no
    # reason to show. Provide a readable failure message here.
    if not ok and not err:
        err = "assoc failed (AP seen? check password/router)"
    if not ok:
        last_error = err
    _connect_result = ok
    _worker_busy = False


def connect(ssid, password):
    """Manual connect: reset the budget, save the config, start trying (also
    revives a stopped service)."""
    if _worker_busy:
        return
    global _tries, _retry_at, _creds_user_set
    cfg["ssid"] = ssid
    cfg["password"] = password
    _creds_user_set = True
    _tries = 0
    _retry_at = 0
    save()
    _start_connect(ssid, password)


def disconnect():
    global state, _ip_hold, _tries, _retry_at
    if _worker_busy:
        return
    _ip_hold = ""
    try:
        _get_wlan().disconnect()
    except Exception as e:
        print("[net] disconnect error:", e)
    state = IDLE
    _tries = 0
    _retry_at = 0


def ip():
    if _ip_hold:
        return _ip_hold
    if state == CONNECTED:
        try:
            return _get_wlan().ifconfig()[0]
        except Exception:
            pass
    return ""


def connected_rssi():
    """Signal strength (dBm) of the current network, for the status-bar WiFi
    icon bucket.

    Prefer the cached rssi for that SSID: _connect_worker scans just before
    connecting, so the cache holds a value fresh at connect time (measured to
    track the real bucket better than status). When adopting an existing link on
    a cold start the cache is empty, so fall back to live `wlan.status("rssi")`
    (measured on board: in the connected state it returns the current AP's real
    rssi and follows the link reliably). Only when neither is available assume
    "connected means good signal" and return -50, rather than misleadingly
    showing a weak link. Returns None when not connected.
    """
    if state != CONNECTED:
        return None
    ssid = cfg.get("ssid", "")
    for row in (_scan_cache or []):
        if row[0] == ssid and row[1] is not None:
            return row[1]
    # The SSID is not in the cache (usually adopted at boot, never scanned):
    # try a live reading.
    try:
        r = _get_wlan().status("rssi")
        # A valid rssi is roughly -30..-95; anything outside (including
        # True/0/None) is untrusted.
        if isinstance(r, int) and -120 <= r <= 0:
            return r
    except Exception:
        pass
    return -50


# rssi bucket thresholds (dBm): the status-bar WiFi icon and the Network list's
# signal colour share this single place so the two cannot drift apart. A
# compromise between common industry figures -- Android's -55/-65/-75 and Cisco's
# -67 design floor for data networks -- collapsed to -55 / -70. This unit's WiFi
# antenna gain is modest, so wider thresholds would paint a genuinely weak link
# as full bars.
RSSI_STRONG_MIN = -55
RSSI_FAIR_MIN = -70


def rssi_bucket(rssi):
    """rssi -> bucket: 2=strong / 1=fair / 0=weak. None counts as strong (an
    unknown level must not read as weak)."""
    if rssi is None or rssi >= RSSI_STRONG_MIN:
        return 2
    if rssi >= RSSI_FAIR_MIN:
        return 1
    return 0


def wifi_icon_key():
    """Status-bar WiFi icon variant name: not connected -> "off"; connected maps
    the rssi bucket to "1"/"2"/"3" (the number is the lit arc count). The icons
    are pre-baked offline and shipped with the assets."""
    if state != CONNECTED:
        return "off"
    return str(rssi_bucket(connected_rssi()) + 1)


# ---------------- Lifecycle: start / poll ----------------

def start():
    """Called once when launcher.py boots: load the config, apply the timezone, and
    auto-reconnect only when stored credentials exist.

    With no net.json (never configured) it returns immediately without touching
    the radio or starting a worker -- wlan.connect() blocks ~9s on an unreachable
    AP and waiting during the very first boot is pointless. The budget is a
    memory counter (_tries starts at 0), so boot needs no history lookup.
    """
    global state, _last_ntp_ms
    load()
    apply_tz()
    if not cfg["ssid"]:
        state = IDLE
        print("[net] no saved wifi, network off (radio untouched)")
        return
    # After a soft reset (Ctrl-D) the firmware-level link survives while module
    # state resets. Measured: isconnected() is still True then, so adopt it as
    # CONNECTED, avoiding a duplicate connect and a "connected but shown as
    # disconnected" UI. On a cold boot isconnected() is False and the normal
    # reconnect path runs.
    try:
        if _get_wlan().isconnected():
            state = CONNECTED
            _last_ntp_ms = time.ticks_add(
                time.ticks_ms(), NTP_FIRST_DELAY_MS - NTP_INTERVAL_MS)
            print("[net] boot: adopting existing link, ip={}".format(ip()))
            return
    except Exception:
        pass
    _start_connect(cfg["ssid"], cfg["password"])


def poll():
    """Called once per main-loop frame. Only ticks comparisons and state
    transitions, so the cost is negligible.

    Key discipline: while _worker_busy is set (a connect/scan/NTP worker is
    running) WLAN is never touched; just wait for the worker to fill in results.
    """
    global state, _last_ntp_ms, _ntp_pending, _connect_result, _tries, _retry_at
    now = time.ticks_ms()

    # Consume the connect result (one-shot, when the worker finishes)
    if state == CONNECTING and not _worker_busy and _connect_result is not None:
        ok = _connect_result
        _connect_result = None
        if ok:
            state = CONNECTED
            _tries = 0
            _retry_at = 0
            # First sync NTP_FIRST_DELAY_MS after the link comes up
            _last_ntp_ms = time.ticks_add(
                now, NTP_FIRST_DELAY_MS - NTP_INTERVAL_MS)
            print("[net] connected, ip={}".format(ip()))
        elif _tries >= MAX_TRIES:
            state = GAVE_UP
            try:
                _get_wlan().disconnect()
            except Exception:
                pass
            print("[net] {} attempts failed, service stopped "
                  "(until manual connect)".format(_tries))
        else:
            # No back-to-back retry after a failure: the wlan.connect() failure
            # path holds the GIL for a long time, and consecutive attempts chain
            # the stutter into one long block. Schedule the retry RETRY_DELAY_MS
            # out; the worker idles in the gap and the UI stays smooth.
            _retry_at = time.ticks_add(now, RETRY_DELAY_MS)
            print("[net] attempt {} failed, retry in {}s".format(
                _tries, RETRY_DELAY_MS // 1000))
        return

    # Automatic retry is due (timer scheduled on failure; the budget check lives
    # in the branch above)
    if state == CONNECTING and not _worker_busy and _retry_at \
            and time.ticks_diff(now, _retry_at) >= 0:
        _start_connect(cfg["ssid"], cfg["password"])
        return

    # IDLE / GAVE_UP / connecting (worker busy): do not query WLAN
    if state != CONNECTED or _worker_busy:
        return

    # CONNECTED: link-loss detection (no worker here, so the main loop may safely
    # call isconnected)
    try:
        alive = _get_wlan().isconnected()
    except Exception:
        alive = False
    if not alive:
        print("[net] link lost, reconnecting")
        _start_connect(cfg["ssid"], cfg["password"])
        return

    # NTP: consume the worker result first, then schedule the next one (reversed
    # order double-fires).
    if _ntp_pending is not None:
        result = _ntp_pending
        _ntp_pending = None
        if result is False:
            _last_ntp_ms = time.ticks_add(now, NTP_RETRY_MS - NTP_INTERVAL_MS)
        else:
            _apply_ntp(result[0], result[1])
    if not _worker_busy and time.ticks_diff(now, _last_ntp_ms) >= NTP_INTERVAL_MS:
        _start_ntp()


# ---------------- NTP (runs inside _thread, never touches LVGL) ----------------

def _start_ntp():
    global _worker_busy
    _worker_busy = True
    import _thread
    _thread.start_new_thread(_ntp_worker, (cfg["ntp_idx"],))


def _ntp_worker(start_idx):
    """Hand-rolled SNTP client: a 48-byte packet, taking the server transmit
    seconds minus the 1900 epoch offset.

    rtc.ntp_sync is not used: it takes a single server and cannot do "one server
    per continent, rotating, with timeout control". The pool rotates from
    start_idx and stops at the first success.
    """
    global _worker_busy, _ntp_pending, last_error
    import socket
    import struct
    n = len(NTP_SERVERS)
    result = None
    for k in range(n):
        idx = (start_idx + k) % n
        host = NTP_SERVERS[idx][1]
        s = None
        try:
            addr = socket.getaddrinfo(host, 123)[0][-1]
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(3)
            s.sendto(b"\x1b" + b"\x00" * 47, addr)
            data = s.recv(48)
            if data and len(data) >= 48:
                epoch = struct.unpack("!I", data[40:44])[0] - 2208988800
                if epoch > 1000000000:      # an obviously implausible time counts as failure
                    result = (idx, epoch)
                    break
        except Exception as e:
            last_error = "ntp {}: {}".format(host, e)
        finally:
            if s is not None:
                try:
                    s.close()
                except Exception:
                    pass
    # Tri-state fill: success=(idx,epoch), whole pool failed=False (distinct from
    # None=still running).
    _ntp_pending = result if result is not None else False
    _worker_busy = False


def _apply_ntp(idx, epoch):
    global _last_ntp_ms, last_ntp_text, _last_ntp_epoch, _last_ntp_region
    _last_ntp_ms = time.ticks_ms()
    _last_ntp_epoch = epoch
    _last_ntp_region = NTP_SERVERS[idx][0]
    cfg["ntp_idx"] = idx
    # Update an existing file only: a successful sync must not conjure net.json on
    # a unit that never configured WiFi.
    save(create=False)
    try:
        import machine
        tz = int(cfg.get("tz", 0))
        # Measured RTC semantics on board (which force adding the timezone
        # here):
        # - time.localtime(epoch) applies **no timezone** and returns UTC fields;
        # - RTC().datetime(v) treats v as **local time**, stores v-tz internally
        #   and adds tz back on read.
        # So writing UTC directly makes the internal value UTC-tz, and the UI and
        # status bar stay a full tz behind (measured: 8 hours behind at tz=8).
        # Shift the epoch to local time before writing.
        local_tm = time.localtime(epoch + tz * 3600)
        # Align the hardware timezone first: the RTC stores v-tz, so a mismatch
        # with the configured tz still displays correctly but makes time.time()
        # return a skewed epoch, which breaks TLS/certificate validation.
        apply_tz(tz)
        machine.RTC().datetime(
            (local_tm[0], local_tm[1], local_tm[2], local_tm[6],
             local_tm[3], local_tm[4], local_tm[5], 0))
        _format_ntp_text()
        print("[net] ntp synced: {} | utc={:04}-{:02}-{:02} {:02}:{:02} tz={:+d}".format(
            last_ntp_text, time.gmtime(epoch)[0], time.gmtime(epoch)[1],
            time.gmtime(epoch)[2], time.gmtime(epoch)[3],
            time.gmtime(epoch)[4], tz))
    except Exception as e:
        print("[net] ntp apply failed:", e)


# ---------------- WiFi scan (runs inside _thread) ----------------

def scan_start(force=False):
    """Start one scan. Returns True=started / False=held by a worker and not
    started (the caller should retry).

    _scan_busy is set synchronously before the thread starts, so the caller can
    treat that as "started" instead of catching the true->false window of
    scanning() -- a connected-state scan can return empty instantly, so the edge
    would be missed.

    force=True means the user pressed scan: it allows the deep scan (drop the
    radio -> scan -> reconnect) that yields a real AP list. force=False (the
    automatic scan when entering the app) deep-scans only once the cache has
    expired, so opening the app does not cut WiFi for 6s every time.
    """
    global _scan_busy, _worker_busy, _scan_result, _scan_force
    if _worker_busy or _scan_busy:
        print("[net] scan_start: skipped (worker_busy={} scan_busy={} state={})".format(
            _worker_busy, _scan_busy, state))
        return False
    _scan_busy = True
    _worker_busy = True
    _scan_result = None
    _scan_force = bool(force)
    print("[net] scan_start: thread started (state={} force={})".format(
        state, force))
    import _thread
    _thread.start_new_thread(_scan_worker, ())
    return True


# security is a firmware bit flag (measured WPA2_AES_PSK=4194308; WPA_WPA2_MIXED
# carries the WPA bit). Normalised to the strings "OPEN" / "WPA" so the UI can
# decide whether to draw a lock (it matches the uppercase "OPEN").
def _sec_text(sec):
    if isinstance(sec, int):
        return "OPEN" if sec == 0 else "WPA"
    s = str(sec).upper()
    return "OPEN" if ("OPEN" in s or not s or s == "NONE") else "WPA"


def _parse_scan(nets):
    """De-duplicate and sort scan()'s raw return into
    [(ssid, rssi, sec_text), ...]."""
    best = {}
    for ent in nets:
        # Measured: scan returns rt_wlan_info objects (attribute access, ssid as
        # bytes), not dicts. Accept dict / tuple / object shapes.
        if isinstance(ent, dict):
            ssid = ent.get("ssid", b"")
            rssi = ent.get("rssi", -999)
            sec = ent.get("security", "")
        elif isinstance(ent, (tuple, list)):
            ssid = ent[0]
            rssi = ent[3] if len(ent) > 3 else -999
            sec = ent[4] if len(ent) > 4 else ""
        else:
            ssid = getattr(ent, "ssid", b"")
            rssi = getattr(ent, "rssi", -999)
            sec = getattr(ent, "security", "")
        if isinstance(ssid, (bytes, bytearray)):
            try:
                ssid = ssid.decode()
            except Exception:
                ssid = str(ssid)
        ssid = str(ssid)
        if not ssid:
            continue
        if ssid not in best or rssi > best[ssid][0]:
            best[ssid] = (rssi, _sec_text(sec))
    return sorted(
        [(s, v[0], v[1]) for s, v in best.items()],
        key=lambda t: -t[1])


def _scan_worker():
    global _scan_busy, _worker_busy, _scan_result, _scan_cache, _scan_cache_ms
    global _scan_force, _ip_hold, state, _last_ntp_ms, last_error
    t0 = time.ticks_ms()
    try:
        w = _get_wlan()
        was_connected = False
        try:
            was_connected = bool(w.isconnected())
        except Exception:
            pass
        print("[net] scan: calling wlan.scan() (connected={})".format(
            was_connected))
        raw = w.scan()
        print("[net] scan: raw {} entry(s) in {}ms".format(
            len(raw) if raw is not None else 0,
            time.ticks_diff(time.ticks_ms(), t0)))
        _scan_result = _parse_scan(raw or [])

        # A connected-state scan always returns 0 entries (the radio is locked to
        # the current channel). A real list requires briefly dropping the link and
        # reconnecting with the stored credentials afterwards -- on-board A/B
        # confirmed that is the only workable path.
        # Note that the deep scan must not require "stored credentials": after a
        # soft reset the firmware may still hold the previous link while this
        # module has an empty cfg["ssid"] (the user deleted net.json or the
        # credentials will not decrypt). Skipping the deep scan then leaves the
        # Network app permanently empty and the user cannot configure anything.
        # Without credentials, dropping the link is fine and it stays down after
        # the scan (that is the correct state anyway).
        need_deep = (not _scan_result and was_connected
                     and (_scan_force or not _scan_cache
                          or time.ticks_diff(time.ticks_ms(), _scan_cache_ms)
                          > SCAN_CACHE_TTL_MS))
        if need_deep:
            print("[net] scan: empty while connected -> deep scan "
                  "(pause link, rescan, reconnect if configured)")
            _ip_hold = ip()          # keep showing the old IP during the drop instead of flashing "-"
            try:
                w.disconnect()
            except Exception as e:
                print("[net] scan: disconnect for deep scan failed:", e)
            time.sleep_ms(SCAN_PAUSE_MS)
            t_scan = time.ticks_ms()
            raw = w.scan()
            print("[net] scan: deep raw {} entry(s) in {}ms (link paused)".format(
                len(raw) if raw is not None else 0,
                time.ticks_diff(time.ticks_ms(), t_scan)))
            _scan_result = _parse_scan(raw or [])

            if not cfg["ssid"]:
                # No usable credentials: stay disconnected after the scan instead
                # of joining a network the user never picked.
                _ip_hold = ""
                state = IDLE
                print("[net] scan: no saved credentials, radio left off")
            else:
                t_link = time.ticks_ms()
                restored = False
                try:
                    w.connect(cfg["ssid"], cfg["password"])
                    deadline = time.ticks_add(time.ticks_ms(), CONNECT_WAIT_MS)
                    while time.ticks_diff(deadline, time.ticks_ms()) > 0:
                        if w.isconnected():
                            restored = True
                            break
                        time.sleep_ms(300)
                except Exception as e:
                    print("[net] scan: reconnect after deep scan failed:", e)
                _ip_hold = ""
                if restored:
                    state = CONNECTED
                    _tries = 0
                    _last_ntp_ms = time.ticks_add(
                        time.ticks_ms(), NTP_FIRST_DELAY_MS - NTP_INTERVAL_MS)
                    print("[net] scan: link restored in {}ms ip={}".format(
                        time.ticks_diff(time.ticks_ms(), t_link), ip()))
                else:
                    state = IDLE
                    last_error = "scan: link restore failed"
                    print("[net] scan: link restore FAILED, radio left "
                          "disconnected")

        print("[net] scan: parsed {} unique AP(s) in {}ms".format(
            len(_scan_result), time.ticks_diff(time.ticks_ms(), t0)))
        for ssid, rssi, sec in _scan_result[:8]:
            print("[net]   - {:<20} {:>5}dBm {}".format(ssid, rssi, sec))
        if len(_scan_result) > 8:
            print("[net]   ... {} more".format(len(_scan_result) - 8))
        # Cache the last non-empty result: while the TTL blocks further deep
        # scans the UI still has a list to show.
        if _scan_result:
            _scan_cache = _scan_result
            _scan_cache_ms = time.ticks_ms()
        else:
            print("[net] scan: empty result")
    except Exception as e:
        _scan_result = None
        last_error = "scan: {}".format(e)
        print("[net] scan failed:", e)
    finally:
        _scan_force = False
    _scan_busy = False
    _worker_busy = False


def scan_get():
    """Return the scan result (None=in progress or failed)."""
    return _scan_result


def scanning():
    return _scan_busy


def busy():
    """Whether a connect/scan/NTP worker is running (usually stuck in a wlan C
    call holding the GIL).

    beep uses this to decide whether to make sound: while the worker holds the
    lock a click tone cannot be muted by the stop thread and inevitably becomes a
    long beep, so staying silent is better. See beep.set_busy_check.
    """
    return _worker_busy

# ---------------- password at rest ----------------
# Scope: this is obfuscation, not cryptographic protection. The key material has
# to travel with the card (a replaced card must still join the network), so it
# lives in the same file. The goal is only that the password is not readable when
# the card is pulled and mounted on a PC, when a backup is stolen, or during a
# full-text search -- not that it resists someone reading the sources under
# /sdcard/factory/gui/.
#
# Algorithm: SHA256 counter-mode keystream XOR.
#   keystream_i = SHA256(_TAG + nonce + byte(i)),  c = plaintext ^ keystream
# The 16-byte nonce is regenerated on every seal() and stored with the file, so
# the same password yields different ciphertext on two cards.
#
# Why SHA256 rather than ucryptolib: hashlib already has proven on-board use in
# this project, while ucryptolib has no on-board evidence. A password is at most
# 63 bytes, so performance is no reason to adopt an unverified dependency.
#
# Envelope (verified after decryption, so corruption takes the "not configured"
# path instead of feeding garbage to connect()):
#   b"RB\x02" + length (2 bytes little endian) + password text
#
# Changing _TAG or the algorithm invalidates every password stored on every
# deployed device; users would have to re-enter it once.

_TAG = b"RockBase/net/v2"
_HDR = b"RB\x02"
R_LEN = 16                     # nonce length, regenerated on every seal


def _rand(n):
    """Get n random bytes. Three sources degrade in turn; the last fallback only
    guarantees "different every time"."""
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
        import time as _t
        seed = "{}-{}-{}".format(_t.ticks_us(), _t.ticks_ms(), id(_TAG))
    except Exception:
        seed = str(id(_TAG))
    return hashlib.sha256(seed.encode()).digest()[:n]


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


def _keystream(nonce, need):
    """Generate need bytes of keystream: SHA256(_TAG + nonce + counter) per block."""
    out = b""
    i = 0
    while len(out) < need:
        h = hashlib.sha256()
        h.update(_TAG)
        h.update(nonce)
        h.update(bytes([i & 0xFF]))
        out += h.digest()
        i += 1
    return out


def _xor(nonce, data):
    n = len(data)
    ks = _keystream(nonce, n)
    out = bytearray(n)
    for i in range(n):
        out[i] = data[i] ^ ks[i]
    return bytes(out)


def seal(pwd):
    """Wrap the plaintext password into {"r": nonce hex, "c": ciphertext hex} and
    return None on failure.

    On failure the caller must abandon the write: losing this change beats
    falling back to plaintext.
    """
    try:
        if isinstance(pwd, bytes):
            raw = pwd
        else:
            raw = (pwd or "").encode()
        n = len(raw)
        body = _HDR + bytes([n & 0xFF, (n >> 8) & 0xFF]) + raw
        nonce = _rand(R_LEN)
        return {"r": _hex(nonce), "c": _hex(_xor(nonce, body))}
    except Exception as e:
        print("[net] seal failed:", e)
        return None


def unseal(d):
    """Restore the plaintext password from {"r","c"}; returns None when the format
    is wrong or the data was corrupted, which callers treat as not configured."""
    try:
        if not isinstance(d, dict):
            return None
        nonce = _unhex(d.get("r") or "")
        c = _unhex(d.get("c") or "")
    except Exception:
        return None
    if len(nonce) != R_LEN or not c:
        return None
    try:
        body = _xor(nonce, c)
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