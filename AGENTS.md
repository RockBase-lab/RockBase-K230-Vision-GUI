# AGENTS.md

Working agreement for AI agents and human contributors in this repository.

## Scope

This repository ships **product content**: the board-side MicroPython GUI,
the asset baking tools, the on-board API stubs, and the product
documentation. Material that only records how a particular person or
machine prefers to work stays local and out of version control.

## Language policy

**The rule is decided by whether git tracks the file. Check with
`git ls-files <path>` before you write.**

- **Tracked by git -> write in English.** Anyone who clones the repository
  reads it, so it must be understandable to every contributor.
- **Ignored by git (local only) -> write in Chinese.** It is read only on
  the machine that produced it, so Chinese keeps drafting fast and keeps
  private conventions out of the public history.

This applies to *everything*, not just the files listed below: code
comments, docstrings, commit messages, tracked documentation, and agent
instructions all follow the same test.

| Content | Language | Tracked in git |
| --- | --- | --- |
| Code comments, docstrings | English | yes |
| Commit messages | English | yes |
| `README.md` | English | yes |
| `README_zh.md` | Chinese (explicit translation) | yes |
| `AGENTS.md` | English | yes |
| `.github/skills/**` | Chinese | no |
| `.github/copilot-instructions.md` | Chinese | no |
| Design notes (`*_design.md`, `*设计*.md`) | Chinese | no |

`README_zh.md` is the one deliberate exception on the tracked side: it is
the Chinese counterpart of `README.md` and is expected to stay in
Chinese. Do not treat it as a precedent for other tracked files.

When you are unsure, run the check instead of guessing:

```powershell
git ls-files --error-unmatch <path>   # tracked   -> English
git check-ignore -v <path>            # ignored    -> Chinese
```

## Code comments

- Write comments in **English**.
- A comment exists for one reason: to let the next reader (human or agent)
  understand the **design intent**, the **meaning of the code**, and facts
  the code itself cannot express. Nothing else belongs in it.
- Keep it **short**. State the constraint in one line and stop.

### What earns a comment

| Keep | Drop |
| --- | --- |
| Why this shape, not the obvious one | History: `was changed to`, `in an earlier version`, `fixed twice`, `at first` |
| Limits the code cannot show: hardware, timing, memory, protocol | Debug narrative: how a bug was found, what was tried first |
| A decision-critical value, with unit and condition | The same fact restated, or an example after an example |
| A non-obvious contract between modules | Restating what the next line already says |

Record a measurement in one line:
`Measured on board: 31 ms/frame at 320x320 input`.

### Length budget

| Where | Limit |
| --- | --- |
| Module header | 12 lines |
| Function or class docstring | 3 lines (5 for a non-obvious algorithm) |
| Inline comment | 1 line |

If a block cannot fit, the surplus is history or narrative: cut it.
Long-form explanation belongs in `docs/`, which has the same standard:
state the measured value, the condition, and the consequence, never the
path taken to find it.

### Self-check

Delete any comment that contains these words; they mark history or
provenance rather than intent:

```
was / were / earlier / previously / at first / later found /
we tried / mistake / lesson / ported / based on / inspired by
```

Do not describe provenance at all: no upstream repository names, branch
names, or file paths. The only exception is attribution required by a
licence, which lives in `factory/gui/assets/LICENSE.txt`.

### Transition

Do not rewrite old comments in bulk. When you are already editing the
code a comment explains, tighten **that** comment to the budget above, so
the cleanup ships next to a behaviour change and stays reviewable.

## Adding an app

An app is a module under `factory/gui/apps/` plus registry entries. Do
all of it in one change:

1. Create `apps/app_<name>.py` exporting `create_app(content, ctx)` and
   returning an object with `step()` and `destroy()`.
2. Add its entry to `APPS` in `launcher.py`, or to `FOLDER_MEMBERS` when it
   belongs to an existing folder tile.
3. If the app gets its own icon, add the `id` to `ICONS` in
   `tools/gen_app_icons.py` **and** to `APP_ACCENTS`. The accent is the
   icon's ink colour, and an id missing from `APP_ACCENTS` renders white.
   Members that reuse another entry's PNG (`detect` -> `icon_vision.png`)
   skip this step.
4. Generate the icons: `python tools/gen_app_icons.py`. It needs
   `pip install svglib reportlab`.
5. Increment `BAKE_VER` in `launcher.py`, then re-bake:
   `python tools/gen_tile_cache.py`. Top-level tiles only; folder members
   are drawn at runtime.
6. Deploy and verify on the board:
   `python tools/deploy_board.py --gui-only`, soft reset, open the app.

Replacing an existing placeholder needs steps 1 and 6 only when the `id`,
icon, and accent already exist.

## Consistency check

Before an acceptance commit, re-read what you changed and confirm that
everything that describes it still tells the truth. Code and comment
drift is the most common defect in this repository because so much
knowledge lives in prose.

Check, in this order:

1. **Comments and docstrings you touched.** Does each still describe the
   new behaviour? Does it state anything the code no longer does? Stale
   numbers (timings, sizes, thresholds) count as wrong.
2. **Code you did not touch but that depends on the change.**
3. **`docs/*.md`.** Any measured value, threshold, or API contract that
   the change moved must be updated in the same commit.
4. **Scripts and generated assets.** See the coupling table below.
5. **Skills and `AGENTS.md`.** If a command line, file path, or rule
   changed, update the text that documents it.

### Coupling table

| Changed | Must also verify |
| --- | --- |
| `launcher.py` colour/size constants (`COL_*`, `TILE_*`, `ICON_SIZE`, `NAME_COLOR`, `*_W`, `*_H`) | Re-run the matching `tools/gen_*.py`, increment `BAKE_VER`, refresh `assets/cache/*.bin` |
| `launcher.py` `APPS` / `FOLDER_MEMBERS` | Each `module` exists in `apps/`, each `icon` exists in `assets/`, each `id` has an `APP_ACCENTS` entry |
| App contract (`create_app` / `step` / `destroy`) | Every `apps/app_*.py` still matches it |
| A board API call | `stubs/*.pyi` covers the symbol |
| `README.md` | `README_zh.md` carries the same content |
| A `tools/` command-line flag | The usage line in the script and the skill that documents it |
| `tools/deploy_board.py` `MANIFEST` | The directory layout on the board |
| A constant parsed by a generator | The generator's own default and any `docs/` value |
| Any rule, path, or command name | The four places rules live: `AGENTS.md`, `.github/copilot-instructions.md`, `.github/skills/**`, `docs/*.md` |
| A board fact learned the hard way | See `## Capturing what you learned` |

Checker script and git hook are deliberately absent: the rules above are
cheap to apply by hand and a hook would block urgent deploy commits. When
a check is repeatedly missed, add it to the local
`.github/skills/acceptance-check/SKILL.md` walkthrough instead.

## Commit messages

All commit messages are English: subject, body, and footer. The type and
scope tables plus the ASCII self-check live in
`.github/skills/english-commit/SKILL.md` when that local skill is present;
the important part is that a message contains no non-ASCII characters.

## Board operations

**Before debugging why a change does not show on the board, run the doctor:**

```powershell
python tools/_doctor.py              # or: python tools/_doctor.py COM56
```

It checks, in the order that isolates the cause, every trap that has silently
hidden a deployed change on this card: `/sdcard/main.py` being a stale launcher
copy, empty `ui/` / `net/` / `audio/` package directories left from the
pre-split layout, missing APPS modules or icons, a `BAKE_VER` mismatch, stale
tile caches, and unresolvable imports. One line per check, non-zero exit on
failure.

**Never pipe a deploy through a line filter.** `deploy_board.py` redraws its
progress with `\r`, which `Select-String`, `grep` and `Select-Object` buffer
until the process exits, so the run looks frozen at `[PLAN]`. Run it bare.

Deploying to the K230 SD card, verifying files on board, and running
one-off scripts over USB serial are documented in
`.github/skills/rockbase-board-deploy/SKILL.md` when that local skill is
present. The entry point is `tools/deploy_board.py`; run it without
arguments for an incremental deploy over an auto-detected port.

## Board constraints

These are the rules where breaking them hangs the board or silently
corrupts behaviour. Each one links to the measured explanation.

1. **Never delete the LVGL active screen.** Load another screen first.
   Deleting a live screen leaves a dangling pointer and kills touch
   input. See `docs/reset-matrix.md`.
2. **The AI camera channel must use `RGBP888` when ai2d declares
   `NCHW_FMT`.** A packed format makes ai2d read HWC memory as CHW; the
   scores collapse to noise while per-channel statistics still look
   correct. Declaring the input as packed with `[1, 3, H, W]` deadlocks
   the board. See `docs/board-ai-runtime.md`.
3. **Release every KPU runtime tensor right after `to_numpy()`.** A
   missed release leaks native memory, crashes the board and drops the
   serial link. Only a power cycle recovers it. See
   `docs/board-ai-runtime.md`.
4. **Changing a colour or size constant that `tools/gen_*.py` parses
   requires re-running the matching script and incrementing
   `BAKE_VER`.** Otherwise the board loads stale bitmaps.
5. **Assets larger than 256 KB must be deployed with `--force`.** The
   incremental deploy compares size only above that threshold, so a
   re-baked file with an unchanged byte count is skipped as "already
   identical".
6. **Never skip `stop_stream()` before `close()` on an audio stream.**
   The order is mandatory and skipping it hard-hangs the board. See
   `docs/performance-budget.md`.
7. **Construct `Sensor()` at most once per VM lifetime.** Stopping and
   deinitialising it does not free the singleton, and a soft reset does
   not clear it either — only `machine.reset()` does. See
   `docs/reset-matrix.md`.
8. **Validate a video stream is exactly 800x480 before binding
   `LAYER_VIDEO1`.** Any other resolution leaves the whole board
   unresponsive with no exception and no serial echo. See
   `docs/board-hardware.md`.
9. **Do not use a variable TTF instantiated by `fontTools`, and do not
   allocate a large RGB565 canvas.** Both wedge the board: the first
   loops forever inside `stb_truetype` on the first glyph render, the
   second runs out of memory. See `docs/lvgl-binding.md`.
10. **After deploying a `.py`, reset the board or delete the module from
    `sys.modules` before believing a result.** `/sdcard/main.py` must
    stay the 112-byte three-line launcher stub; copying `launcher.py` over it
    boots a stale snapshot that no probe will reveal. See
    `docs/pitfalls.md`.
11. **Every runtime data path belongs under `/data/<app>/`, one directory
    per app.** `/sdcard` is the ~76 MB deploy partition that holds product
    content (`factory/gui`, `examples/kmodel`, `examples/utils`) and must
    only ever be written by the deploy tools. Any file the product creates
    at runtime — recordings, photos, TTS audio, caches, settings,
    databases — goes to `/data/<app>/...` so it survives a re-flash of the
    deploy partition and cannot fill it. `/data` is the ~28.6 GB eMMC
    partition. See `docs/board-hardware.md`.
12. **Never open ADC channel 4.** It wedges the board and drops the serial
    link with no exception. Channels 0-3 and 5 are safe. See
    `docs/board-hardware.md`.
13. **Diagnose an unstable ADC reading on an idle board.** Sampling ADC5
    while another app owns the camera or KPU produced an alternating two-value
    pattern and a false 2.4 V span that did not reproduce once the board was
    idle. See `docs/pitfalls.md`.

The complete list of traps, including the ones that fail silently instead
of hanging the board, is `docs/pitfalls.md`. Read it before writing a new
app or a new probe.

## Board knowledge

Measured facts about the platform live in `docs/` and are the reference
for design decisions:

| Document | Contents |
| --- | --- |
| `docs/board-ai-runtime.md` | Ai2d and KPU contracts, post-processing signatures, memory discipline |
| `docs/lvgl-binding.md` | What this LVGL binding exposes, rendering model, theme traps, touch semantics |
| `docs/board-hardware.md` | IMU, ADC, microphone, amplifier, camera, video decoding |
| `docs/performance-budget.md` | Per-frame costs and the 20 ms loop budget |
| `docs/reset-matrix.md` | Which failure a soft reset clears and which needs a power cycle |
| `docs/pitfalls.md` | Cross-cutting trap index, grouped by failure mode |

Probe methodology and reproducible probe scripts are local material:
`.github/skills/k230-board-probe/SKILL.md` for the workflow and
`tools/probes/` for the scripts that are worth keeping.

> **`docs/*.md` written for the reader, not for the author.** State the
> measured value, the condition it was measured under, and the
> consequence for a design decision. Do not record the debugging path.

## Capturing what you learned

**A pitfall you hit twice is a pitfall the next person will hit.** Stop and
write it down before moving on, in the same change that fixes it, while
the conditions are still known.

The trigger is any of these:

- A symptom took several attempts to explain, or you re-derived the same
  fact you already measured once.
- A probe or app wedged the board, dropped the serial link, or failed
  silently.
- You changed code because of something the source does not show.

Write it to exactly one place, chosen by what the knowledge is:

| The knowledge is | Goes to |
| --- | --- |
| A hard rule that hangs or corrupts the board if broken | Numbered entry in `## Board constraints`, plus a `docs/pitfalls.md` entry |
| A measured value, limit, or API contract | The matching `docs/*.md`, with value, condition, consequence |
| Why this code has the shape it has | The comment next to the code, within the length budget |
| A repeatable measurement | `tools/probes/` script plus one line in `tools/probes/README.md` |
| A repeatable procedure or command sequence | `.github/skills/` (local, Chinese) |

Rules for the entry itself:

- **Read `docs/pitfalls.md` first.** Extend the existing entry instead of
  adding a near-duplicate; a trap index that repeats itself stops being
  read.
- **State the failure, the condition, and the fix.** Not the order in
  which you tried things, and not how long it took.
- **Keep the reproduction.** A fix whose trigger cannot be reproduced is
  a guess; the probe or the exact steps belong in the entry.
- **Delete the entry when the trap disappears** (dependency upgrade,
  firmware change). A stale trap costs more than a missing one.

## Not tracked

`.github/` is listed in `.gitignore`. The instruction file and the skills
under it are local conventions for one workstation; other contributors
are not expected to adopt them.
