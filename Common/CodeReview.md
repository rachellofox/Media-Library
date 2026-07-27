# Repository Review

A working document for a full review of the repo: code correctness, dead code,
structure, and consistency against the standards in `.github/instructions/`.

Split into sections so it can be worked through over several sessions. Nothing
here is a change already made — items are proposals until worked and marked.

## How to use this

Each section states **what is in scope**, **what we check for**, and a **findings**
list. Findings carry a status:

- `[ ]` open, not yet worked
- `[~]` in progress
- `[x]` done (say what changed)
- `[?]` needs your decision — never actioned without you
- `[-]` reviewed, no change needed (say why)

Work one section at a time. At the end of a section, update the statuses here and
record anything user-facing in `Common/CHANGELOG.md`.

Deletions are **always** `[?]`. Nothing gets deleted without your say-so.

### Current state

Updated as work lands. "At review start" is the 2026-07-26 survey, kept so
progress is measurable rather than asserted.

| Measure | At review start | Now |
| --- | --- | --- |
| Python files | 20 (9,359 lines) | 38 (11,607 lines) |
| `app.py` | 5,318 lines, 61 routes, 205 functions | **3,522 lines**, 61 routes, 118 functions |
| Modules in `medialibrary/` | 0 | 16 — the whole codebase bar `app.py` |
| Python files at the repo root | 9 | **1** (`app.py`) |
| Templates | 3 (5,362 lines, 3,304 inline JS) | **2,570 lines, 53 inline JS** (bootstrap only) |
| Front-end JS in files | 0 | 2 (`static/js/`, 3,247 lines) |
| Tests in repo | **0** | 22 files, ~456 assertions |
| CI workflows | **0** | 1 (lint, compile, test, startup) |
| Lint findings (`ruff check .`) | n/a — no linter | **0** |
| Formatting (`ruff format --check`) | n/a — no formatter | **53 files conform** |
| Lines over 100 chars | 80 (of a then-unset limit) | **0** |
| Style violations (dividers, dead code, missing docstrings) | 10 *(scan said 0 — see G0)* | **0** |

The opening claim that the Python already followed the commenting rules closely
was broadly right but rested on a scan that under-counted — see G0. The
conclusion drawn from it still holds: this review was always mostly about
**structure, safety nets and repo hygiene**, and that is where the real defects
turned up.

**Defects found by this review so far**, none of them found by reading code —
every one came from running, probing or linting:

- an arbitrary-file-read over the direct-stream route (I4)
- a maintenance script that overwrote a film with a nested copy of itself, and
  had been crashing on its first log line for so long nobody noticed (G3)
- a leaked file descriptor on every failed playback start (E5)
- the app returning 500 from any entry point other than `python app.py` (C2a)
- a script that reported "no torrents" when it could not reach qBittorrent, in
  the act of deleting torrents that appear to have no files (G4)

---

## Section A — The standards themselves

**Scope:** `.github/copilot-instructions.md` and the four files in
`.github/instructions/`.

**Check for:** whether the rules are sound, whether they are followed, whether
they contradict each other or the code, and what they fail to cover.

**Verdict on the existing rules: they are sound and worth keeping as the
standard.** "Comment WHY not WHAT", no decorative dividers, no commented-out
code, parameterised SQL only, module docstrings on `scripts/` — all of it is
sensible, and the codebase already honours it. No rule needs reversing.

The gaps are things the rules are silent on, which is why the code is consistent
in some ways and not others.

- [x] A1. **`.github/` was excluded by `.gitignore`.** The standards this review
  is built on were not in version control. **Done 2026-07-26:** the `.github/`
  rule is removed and `.vscode/` alone stays ignored, with a comment saying why,
  so it is not "tidied" back in later. Verified with `git check-ignore`.
- [x] A2. **Line length set to 100. Done 2026-07-26.** Chosen from the data, not
  taste: 92% of lines already fit in 79 and **99% in 100**, so this codifies the
  code's existing shape. Only 114 lines exceed it — see A8.
- [x] A3. **`ruff` adopted. Done 2026-07-26.** Config in `pyproject.toml`,
  installed via a new `requirements-dev.txt`. Rule set is E/W/F/I/UP/B/C4/SIM/RUF.
  **101 violations fixed automatically** (unsorted imports, unused imports,
  trailing whitespace, redundant open modes, f-strings without placeholders),
  verified by the full suite and an app start afterwards.
  Three rules are switched off *because this document's rules win over the tool's
  defaults*, each with the reason in `pyproject.toml`: `E401` (the guide permits
  `import os, sys` in short scripts), `RUF001-003` (the code matches en dashes
  deliberately — `[-–]` in a regex is not a typo), and `SIM105` (28 rewrites of
  readable `try/except/pass` for no gain).
- [x] A4. **Test conventions written down. Done 2026-07-26.** Location, naming,
  the no-network rule, the sandbox rule, and why the front-end tests read the
  shipped template. In the style guide, with detail in `tests/README.md`.
- [x] A5. **Type-hint policy set. Done 2026-07-26.** Annotate new and touched
  functions; no retrofit campaign. Mixed annotation is acceptable, a commit that
  churns every file is not.
- [x] A8. **All 141 remaining findings cleared. Done 2026-07-26.**
  `ruff check .` now reports **All checks passed**, with the suite green and the
  app starting after every step.
  The 27 substantive findings were handled individually, and two of ruff's
  suggestions were **wrong** and are suppressed with the reason at the line
  rather than applied:
  - `SIM118` wanted `'genre_1' in item` in place of `in item.keys()`, but `item`
    is a `sqlite3.Row`, which has no `__contains__` — `in` falls back to
    iterating the column *values*, so the check would have silently asked a
    different question. Confirmed empirically before deciding.
  - `SIM102` wanted the upgrade-retirement guard collapsed into one condition,
    but the outer test is what makes `os.path.samefile` safe to call, and that is
    the path that has caused data loss twice.
  The other 114 were line length, rewrapped by hand across 23 files.
- [x] A9. **`ruff format` adopted. Done 2026-07-26, on your decision.** 40 files
  reformatted, 4,123 lines changed, 13 already conforming. Lint stayed clean and
  all 20 test files passed afterwards, with the app answering on 62 routes and
  352 items.
  The style guide now says the formatter *owns* layout — spacing, line breaks and
  argument alignment are not to be hand-tuned — and CI runs
  `ruff format --check` alongside `ruff check`, so consistency is enforced rather
  than remembered. This is what makes A2's line-length rule self-maintaining.
- [x] A6. **Repo map corrected. Done 2026-07-26.** It had named the dead
  `imdb_client.py` as the metadata client, pointed at a deleted `docs/`, omitted
  five modules, and knew nothing of `medialibrary/`. It now lists the seven
  `medialibrary` modules and states the two rules that hold across them — never
  import `app`, take dependencies as getters via `configure()`; and moved names
  are re-imported into `app.py`, which preserves callers but *not*
  monkeypatching. Every path named was checked to exist, which is how the
  dangling `imdb_client.py` reference surfaced.
- [ ] A7. **No commit or branch conventions**, despite `main` being the only
  branch and history being two commits.

---

## Section B — Git hygiene and stray files

**Scope:** `.gitignore`, tracked vs untracked files, files on disk that should
not be there.

**Check for:** secrets or data at risk of being committed, files that should be
tracked but are not, and leftovers.

- [x] B1. **`media.db` was not ignored, and is now.** Done 2026-07-26.
  **CORRECTION — the original finding had this backwards, and it was overstated.**
  It claimed `media.db` was "the app's actual database" and that committing it was
  the most urgent item in the review. Checking properly while working C2:
  `DB_PATH` is `library.db` (app.py:44), which holds 13 tables and 352 media
  items and **was already ignored**. `media.db` is a **zero-byte file with no
  tables** — a stray, not a database. So the library was never at risk; the
  exposure was an empty file.
  The change itself still stands: an unignored `.db` in the repo root is worth
  closing off whatever it contains, and `media.backup*.db` is covered too.
  The lesson is the one this review keeps re-learning — a claim asserted from a
  filename rather than from the file's contents.
- [x] B2. **`media.db` deleted. Done 2026-07-26, on your decision.** Confirmed
  empty first — 0 bytes, no tables — and `DB_PATH` points at `library.db`. The
  only mention anywhere was a line in `tests/README.md` naming it as the database
  tests must not touch; corrected to `library.db`.
  Worth recording how it probably got there: verifying it, I ran
  `sqlite3.connect('media.db')`, which **created the file I was checking for**.
  An earlier probe script of mine almost certainly created the original the same
  way. `sqlite3.connect` is not a read-only operation.
- [x] B3. **`tmp/` was not ignored.** It holds `tmp/hls/`, the live transcode
  cache, which must never be committed. **Done 2026-07-26:** `tmp/` ignored.
  The stray `.cs` file inside it is still a decision — see B7.
- [x] B4. **Untracked source committed. Done 2026-07-26.** 50 files were
  outstanding — not 13 as first counted, since the tracked files also carried
  ~6,400 lines of uncommitted change. Committed to branch
  `commit-outstanding-work` in seven logical commits (ignore rules, standards,
  tests, new modules, application work, scripts, docs) rather than one, so the
  history stays reviewable.
  Verified by cloning the branch to a clean directory: everything compiles and
  **all 19 test files pass from the fresh clone**, with `.github/` present and no
  `.db` file or `tmp/` leaked. The committed state now builds and runs.
  *Branch is unmerged — merging into `main` is your call.*
- [x] B5. **`docs/README.md` deleted.** The pending deletion is committed. Two
  instruction files still point at `docs/` — that remains open as C3.
- [x] B6. **`imdb_client.py` deleted by you. Recorded 2026-07-26.** 88 lines,
  imported by nothing — TMDB had replaced it. Confirmed nothing referenced it
  beyond three stale mentions, now removed: the repo map, a README row listing it
  as "retained", and a docstring in `tmdb_client.py` describing its return shape
  as "the IMDbClient.metadata() contract". Recoverable from history if ever
  needed.
- [x] B7. **`tmp/reference/TranscodeManager.cs` deleted. Done 2026-07-26, on your
  decision.** 28 KB of C# from another project, referenced by nothing in the
  codebase or the docs. It was **not tracked by git** — `tmp/` is ignored — so
  unlike the other deletions this one is not recoverable from history. Flagged
  before removing. `tmp/` now holds only the live HLS cache.

---

## Section C — Repo structure against GitHub practice

**Scope:** root layout, missing standard files, packaging.

**Check for:** what a well-formed public Python repo is expected to carry.

- [ ] C1. **No `LICENSE`.** Without one the code is "all rights reserved" by
  default, which matters if the repo is public. Needs a decision on which licence.
- [x] C2. **CI workflow added. Done 2026-07-26.** `.github/workflows/ci.yml`
  covers exactly what `ci-and-quality-gates.instructions.md` asks for: install
  from `requirements-dev.txt`, lint, compile (the syntax gate), the test suite,
  and a startup check that fetches a page rather than merely importing the
  module. Runs on push to `main` and on every pull request, on Python 3.10 and
  3.13 — the ends of the range `requires-python` claims.
  Node is installed explicitly and then *asserted*, because `run_all.py` skips
  the JavaScript tests with a notice when node is missing, which would silently
  halve the suite while still reporting success.
  **Not yet executed on GitHub** — there is no remote wired up here, so the
  workflow is verified by parsing the YAML and running every step by hand
  against a clean checkout, not by a green tick. The 3.10 leg in particular is
  unproven: this machine has 3.14 only, so if a dependency has no 3.10 wheel
  that leg will fail on first run and the matrix should drop to 3.11+.
- [x] C2a. **Startup was broken outside `python app.py`, and CI found it.**
  Simulating the workflow against a clean checkout returned **500, `no such
  table: settings`**. `store.initialize()` was called only inside
  `if __name__ == '__main__'`, so any import-based entry point — a WSGI server
  such as gunicorn or waitress, the maintenance scripts, the tests — met a
  database with no schema. Moved to module scope beside `store = Storage(...)`;
  the statements are all `CREATE TABLE IF NOT EXISTS`, so repeating them on
  every import costs nothing. A clean checkout now answers 200 with 62 routes.
  This is the first defect CI caught before a human did, which is the argument
  for having it.
- [ ] C3. **`docs/` is referenced by two instruction files but no longer exists.**
  Either recreate it or amend the instructions — currently the docs rule points
  into a void.
- [x] C4. **`pyproject.toml` added. Done 2026-07-26.** Holds project metadata,
  `requires-python = ">=3.10"` (the code uses `X | None` unions), and all ruff
  configuration. Dependencies are read from `requirements.txt` rather than
  duplicated, so there is still one list.
- [ ] C5. **No `.editorconfig`**, so indentation and newline handling depend on
  whatever editor is open.
- [ ] C6. **No `CONTRIBUTING.md`, issue or PR templates.** Lower priority for a
  personal repo; list them so the decision is deliberate rather than accidental.
- [x] C7. **Answered by `scripts/README.md`. Done 2026-07-27.** The leading
  underscore stays, and the README says what it means — internal to the repo, not
  "do not run" — alongside an index of all ten and which six change files. No
  rename, because renaming ten scripts would break every invocation you have in
  muscle memory to fix a naming question a sentence of documentation settles.


- [x] C8. **Root folded into the package. Done 2026-07-27.** The eight remaining
  root modules — `storage`, `tmdb_client`, `trakt_client`, `subtitle_client`,
  `qb_search`, `quality`, `naming`, `episode_match` — moved into `medialibrary/`
  with `git mv`, and every import rewritten across 21 files. `app.py` is now the
  only Python file at the repo root, and the package holds all 16 modules.
  **It surfaced a break that predated it.** `scripts/_plan_tv_naming.py` died on
  `app.DISCOVER_COLLECTION_CACHE_HOURS`. That constant had moved to
  `medialibrary.config` during E1b, ruff removed the then-unused import from
  `app.py`, and nothing noticed — the maintenance scripts act on a real library,
  so no test runs them. The script now imports the constant from its real home.
  `tests/test_module_layout.py` closes that gap: a static check that every
  `app.X` the scripts and tests reach for actually exists, that the root holds
  only `app.py`, and that **no module in `medialibrary` imports the
  application** — which is the rule the whole E1 split rests on, now enforced
  rather than remembered.

---

## Section D — Dependencies

**Scope:** `requirements.txt` against actual imports.

- [x] D1. **`babelfish` declared. Done 2026-07-26.** Imported directly by
  `subtitle_client.py`, but arriving only as a transitive dependency of
  `subliminal` (`babelfish>=0.6.1`). Now declared in its own right.
- [x] D2. **`Werkzeug` declared. Done 2026-07-26.** Imported directly by `app.py`
  for password hashing and by `tests/test_auth.py`, but arriving only via Flask
  (`Werkzeug>=3.0.0`). Now declared in its own right.
- [x] D3. **Pinning policy settled. Done 2026-07-26.** Every entry now carries a
  floor and a ceiling at the next major version — the floor is the oldest release
  known to work, the ceiling stops a future major release breaking a fresh
  install silently, and patch/minor upgrades still flow. This loosened
  `Flask==3.0.3` to `>=3.0.3,<4`, which is the one place the old file was
  stricter. The policy is written at the top of the file so it survives the next
  edit.
  Verified: all 7 requirements satisfied by the current environment, `pip
  install --dry-run` resolves, and the test suite still passes.
- [x] D4. **`requirements-dev.txt` added. Done 2026-07-26**, once A3 settled what
  belongs in it. Pulls in `requirements.txt` and adds `ruff`. Records that the
  test suite needs no Python dependencies at all — only `node` on PATH for the
  JavaScript files.
- [x] D5. **No unused declarations, and no others missing.** Re-checked with an
  AST scan rather than grep, which matters: the first pass missed `send2trash`
  entirely because it is imported inside a `try` block, and would have missed any
  other conditional import. Full set is Flask, Werkzeug, keyring, python-dotenv,
  Send2Trash, subliminal, babelfish — all now declared, none spare.

---

## Section E — `app.py`

**Scope:** 5,318 lines, 61 routes, 205 functions — over half the Python in the repo.

**Check for:** correctness bugs, dead code, duplicated logic, and whether the
module should be split.

This section is large enough that it needs sub-sections. Proposed split, to be
worked one at a time:

- [~] E1. **Split agreed and started 2026-07-26.** Approach settled by measuring
  rather than guessing: an AST pass over app.py showed 191 top-level functions —
  62 route handlers (1,975 lines) and 129 helpers (2,748 lines) — of which **92
  helpers totalling 1,705 lines touch none of the `app`/`store`/`tmdb`/`qb`
  singletons**. Those extract with no circular-import risk, so they are the seam
  to cut along, and the singleton-bound code stays put for now.
  **Migration rule:** every moved name is imported back into app.py by name, so
  `app.X` keeps resolving for the maintenance scripts and tests. No caller
  changes when code moves.
  Done so far — `medialibrary/` package created with:
  - `config.py` — BASE_DIR, DB_PATH, POSTER_DIR, HLS_CACHE_DIR, FFPROBE_EXE,
    FFMPEG_EXE. app.py now consumes these instead of defining its own, so there
    is one source of truth rather than two.
  - `playback.py` (558 lines) — the direct-play / direct-stream / HLS cluster:
    20 functions and 7 module constants including the live transcode job state.
    Chosen first because it is the largest cohesive group that needs nothing but
    three config values and one request helper.
  - `identify.py` (412 lines) — working out what a file or folder holds: title
    and year parsing, pack detection, episode markers, season inference, and
    choosing between search results. 14 functions and 9 constants.
  - `subtitles.py` (332 lines) — sidecar discovery, embedded-track probing,
    SubRip to WebVTT conversion, and the English-detection heuristics. 10
    functions and 3 constants.
  app.py: **5,386 → 4,299 lines, a fifth of the file moved out.** Verified after
  every cluster: ruff clean, 19/19 test files pass, the app answers with all 62
  routes and 352 items, and the full CI sequence passes from a clean checkout.
- [x] E1a. **qBittorrent extracted by injection. Done 2026-07-26.**
  `medialibrary/qbt.py` (276 lines): 13 functions, `QBT_DONE_STATES` and
  `QbtUnavailableError`. It was pure except for two one-line accessors reading
  `store.get_setting`, and moving those as-is would have made `medialibrary.qbt`
  import `app.store` while `app` imports `medialibrary.qbt` — circular.
  Instead the module exposes `configure(get_setting)` and the application hands
  it a getter once the store exists. With no getter it falls back to the
  environment alone, which is what the maintenance scripts get. The module now
  imports nothing from the application.
  **The test suite caught a real flaw in the first attempt.** Passing
  `store.get_setting` directly binds the store object existing at import, so when
  a test swapped in a throwaway database this module carried on reading the real
  settings — `_qbt_webui_enabled()` returned true against an empty test config
  and two download-badge assertions failed. Fixed by injecting
  `lambda key: store.get_setting(key)`, which resolves the current store per
  call. This is the argument for keeping the suite green at every step rather
  than at the end: the bug was silent and behavioural, not a crash.
  This is the pattern for the remaining `store`- and `tmdb`-bound clusters.
- [x] E1b. **Discover extracted. Done 2026-07-26.** `medialibrary/discover.py`
  (302 lines): incomplete collections, the Trakt watchlist, aired-but-missing
  episodes, and the TMDB season/status caches those depend on.
  It needs three things from the application, all injected as **getters** rather
  than objects — `configure(get_store=, get_tmdb=, get_trakt_client=)`. That is
  the E1a lesson applied before it could bite: `tmdb` is rebuilt by
  `_refresh_tmdb_client()` whenever the API key changes, so handing over the
  client itself would have left this module holding a stale one after any key
  change, and `store` is swapped wholesale by the tests.
  The bodies were rewritten mechanically from `store.`/`tmdb.` to `_store()`/
  `_tmdb()`. The module deliberately defines no bare `store` or `tmdb`, so any
  reference the rewrite missed fails as an undefined name rather than silently
  reading nothing — ruff reported none.
  Verified against the real library rather than only by import: 8 incomplete
  collections and 9 shows with missing episodes came back through the moved path,
  and both getters were confirmed to resolve to the live objects.
- [x] E1c. **Download finalisation extracted. Done 2026-07-26.**
  `medialibrary/downloads.py` (486 lines): finalising a completed download,
  filing a TV episode, auto-finalising what qBittorrent has finished, re-adopting
  orphaned downloads, placing a file in the library, and retiring the one it
  replaces — plus the lock that stops concurrent page loads racing into the same
  item. `store` and the logger are injected as getters.
  This is the path that has caused data loss twice, and the extraction was worth
  doing carefully: **five test failures appeared and every one was a real
  problem, not a test being fussy.**
  The cause in all five was the same and is the central hazard of this kind of
  refactor: a test stubs `app.X`, but once the code moves, `app.X` is only a
  re-export and the moved code resolves its own reference. Worse than a broken
  test — `test_finalize` patches `send2trash`, so the real one ran and genuinely
  recycled its temporary files. A refactor that silently disarms a safety stub is
  exactly how the earlier data loss happened.
  Two fixes, both structural rather than papering over:
  - the three tests that stub `send2trash` now patch it in the module that owns
    `_retire_path`, with a comment saying why;
  - `downloads.py` and `app.py` now call qBittorrent through `medialibrary.qbt`
    rather than by from-imported name, because a from-import binds at import time
    and ignores any later stub. The names stay re-exported from `app` so
    `scripts/_qbt_prune_broken.py` keeps working.
  **Lesson worth carrying:** re-exporting a name keeps callers working but does
  *not* keep monkeypatching working. Anything a test stubs has to be reached
  through its module.
- [ ] E2. Auth and session handling — review for correctness and security.
- [ ] E3. Library scan and import (`scan_media_entries`, `import_media_from_paths`).
- [ ] E4. Downloads and qBittorrent integration, including the finalisation path
  that has caused two data-loss bugs already.
- [~] E5. Playback, HLS and cache handling. **One bug already fixed
  (2026-07-26)**, surfaced by adopting ruff: `_start_direct_stream` opened
  `ffmpeg.log` and, if `Popen` or anything after it raised, returned through an
  `except` that removed the cache directory but never closed the handle — leaking
  a file descriptor on every failed playback start. On Windows the open handle
  also kept the log locked, so the `shutil.rmtree(..., ignore_errors=True)`
  cleanup silently left the directory behind. `_start_hls_transcode` had the
  matching close all along; the two paths had simply drifted. The rest of this
  section is still to review.
- [ ] E6. TV and episode logic (`scan_local_episodes`, missing-episode discovery).
- [ ] E7. Discover and TMDB caching.
- [ ] E8. Route layer — consistency of error shapes and status codes across 61 routes.
- [ ] E9. Sweep for dead code across the whole module once the above are done.

---

## Section F — Templates and front end

**Scope:** `templates/` — 5,362 lines, 3,304 of them inline JavaScript.

**Check for:** dead JS, duplicated logic, and whether the JS should move to
`static/`.

- [x] F1. **`index.html` JavaScript extracted. Done 2026-07-27.** 2,584 lines
  moved to `static/js/library.js`; the template drops 4,055 → 1,472 lines.
  The Jinja turned out to be confined to the first 34 lines — a bootstrap
  supplying `ITEMS`, `PREFERRED_QUALITY`, `TRAKT_STATE` and the section id lists.
  That stays inline, because it cannot live in a static file; everything after it
  moved verbatim. The static file is a classic script loaded after the bootstrap,
  so the bootstrap's top-level `const`s are in scope for it and its function
  declarations remain reachable from the inline `onclick` handlers.
  Verified beyond the tests: the rendered page carries exactly two script tags,
  the bootstrap is still populated, `function openHero` is *not* in the page, and
  `GET /static/js/library.js` returns 200 and 102 KB of `text/javascript`.
- [x] F2. **`player.html` JavaScript extracted. Done 2026-07-27.** 665 lines to
  `static/js/player.js`; the template drops 1,212 → 548 lines. Its bootstrap is
  three values — `mediaId`, `episodeParam`, `knownDurationSeconds`. Player page
  verified rendering and serving the file.
  The duplication with `library.js` noted here (playback URL building, subtitle
  handling) is still present — extraction did not address it, and it is now much
  easier to see. Left open as F5.
- [x] F3. **Front-end standard written. Done 2026-07-27.**
  `.github/instructions/javascript-style.instructions.md`: where code lives and
  why logic must not go back into a template, the escaping rule (`escHtml` for
  text, `escAttr` for attributes, attributes always double-quoted), and the same
  comment discipline as Python. CI now runs `node --check` over every shipped and
  test script — cheap, and it catches an edit that would otherwise only fail in
  someone's browser. Four decorative dividers in the extracted JavaScript were
  cleaned, the rule now applying to it.
  Not adopted: a JS linter. `eslint` would be a real dependency and a real config
  decision; the parse gate is the cheap 80%. Worth revisiting on its own.
- [ ] F4. Review for genuinely dead JS — functions no longer called by any
  handler. Now tractable: the code is in two files a tool can read.
- [ ] F5. **`library.js` and `player.js` duplicate playback URL building and
  subtitle handling.** Noted when F2 was written and unchanged by the extraction;
  a shared module is the obvious answer.

---

## Section G — `scripts/`

**Scope:** 10 maintenance scripts, 1,523 lines.

**Check for:** which are one-off and spent, which are ongoing tools, and whether
the destructive ones are safe.

- [-] G1. All 10 carry module docstrings, as the standard requires. Verified by scan.
- [x] G0. **My "0 style violations" baseline was wrong.** The original scan
  required the rule characters immediately after the `#`, so
  `# ── 1. Rename … ──────` did not match. A regex looking for rule characters
  anywhere in a comment finds **10 dividers** across `_cleanup_movies.py` and
  `test_finalize.py`. All replaced with plain comments. Corrected here rather
  than quietly, because the wrong number was used to argue this review was not
  about style.
- [x] G2. **Classified. Done 2026-07-27.** Four are read-only diagnostics
  (`_audit_movies`, `_dbcheck`, `_debug_missing`, `_debug_query`) and six change
  files. None turned out to be a spent one-off: every one addresses a condition
  that recurs whenever new media arrives, so none is proposed for deletion. Git
  dates were no help in judging this — all ten show the same date, being the
  commit that first tracked them.
- [x] G3. **Audited, and `_cleanup_movies.py` was the one that never got fixed.**
  Done 2026-07-27. The other five write-capable scripts are all dry-run by
  default and all use `os.rename`. `_cleanup_movies.py` had four separate
  problems:
  - **It crashed before doing anything.** It logs an arrow (`→`) but never
    reconfigured stdout, so on a cp1252 Windows console it died with
    `UnicodeEncodeError` on its first log line, in dry run. It has been unusable
    as shipped. Every other script has the guard.
  - **`shutil.move`** — the exact call behind the 10 GB duplication. Now
    `os.rename`.
  - **Hard deletes.** `os.remove` and `shutil.rmtree` on leftovers and junk, with
    no recycle. Now routed through `send2trash` like the rest of the codebase.
  - **A silent overwrite.** Promoting a nested video onto a name the folder's own
    video already held: `shutil.move` overwrote it, destroying the root video.
    Swapping to `os.rename` turned that into a loud failure, which is how the bug
    surfaced at all; it now skips and reports, leaving both files.
  Also standardised the flag on `--apply`, with `--execute` still accepted so an
  older invocation applies rather than silently doing nothing.
  Exercised against a sandbox with the recycle bin redirected: dry run changes
  nothing, `--apply` renames, promotes, skips the collision, recycles junk, and
  leaves `Featurettes`, `Subs` and root subtitles untouched.
- [x] G4. **`_qbt_prune_broken.py` failed unreadably, and unsafely.** Done
  2026-07-27. With qBittorrent unreachable it exited with a raw traceback; worse,
  had the call returned `[]` instead of raising, the script would have printed
  "qBittorrent reported no torrents" and exited 0 — a failure indistinguishable
  from an empty result, in a script whose job is to delete torrents that appear
  to have no files. Now reports the failure and exits non-zero without changing
  anything.
- [ ] G4. `_debug_missing.py` (30 lines) and `_debug_query.py` (58 lines) look
  like scratch debugging kept by accident. *Deletion candidates, your call.*
- [x] G5. **`scripts/README.md` added. Done 2026-07-27.** What each script is
  for, which of the ten change files, and the four rules they follow — dry run by
  default, `os.rename` never `shutil.move`, recycle never unlink, refuse rather
  than guess — each with the incident that earned it. Also answers C7: the
  leading underscore marks them internal to the repo, not "do not run".

---

## Section H — Tests

**Scope:** the whole repo. There is no test suite.

**Check for:** what exists, what it should be, and what is at risk today.

- [x] H1. **No tests were in the repo.** Every test written across recent
  sessions lived in a **temporary scratchpad directory**. The survey found this
  was not a hypothetical risk: of seven session scratchpads on disk, **six were
  already empty** — the loss had happened five times over, and only the current
  session's files survived. The count was far larger than first estimated: **19
  files, 2,466 lines, ~424 assertions**, covering episode identification and the
  download-finalisation path, which are precisely where the two data-loss bugs
  occurred.
- [x] H2. **Recovered into `tests/`. Done 2026-07-26.** All 19 files moved and
  made portable — they hardcoded `c:\Users\rache\Documents\...`, so a test that
  only ran on one machine is no safety net. Python files now derive `REPO_ROOT`
  from `__file__`; JS files resolve the template through `__dirname`. Added
  `tests/run_all.py` (runs each as a subprocess, reads their tallies, skips JS
  with a notice when `node` is absent) and `tests/README.md`. **All 19 pass.**
- [ ] H3. Move to `pytest`. Deliberately *not* done as part of the recovery: the
  suite is dependency-free and passing, and converting 424 assertions is a
  separate piece of work with its own risk. Sequence it after C4 (`pyproject.toml`),
  so the config has somewhere to live.
- [x] H4. The TMDB-stubbing and sandbox conventions are now written down in
  `tests/README.md`, including why the JS tests read the shipped template rather
  than a copy.
- [ ] H5. Decide what a "behaviour change needs a test" rule means in practice,
  so `ci-and-quality-gates` becomes enforceable.
- [ ] H6. `test_tv_upgrade_hazard.py` does not print the standard tally line, so
  it reports blank in the runner summary despite passing. Minor inconsistency.
- [ ] H7. No coverage measurement.

---

## Section I — Security

**Scope:** whole repo. `security-and-owasp.instructions.md` exists and should be
read in full when this section is worked.

**Check for:** secret handling, authentication, injection, and the public-access
path specifically, since the app can be exposed to the network.

- [x] I1. **No secrets in tracked files or history. Done 2026-07-26.** Scanned
  every tracked file and the full history for API keys, bearer tokens, JWTs,
  private keys and hardcoded passwords: nothing. `.env` is untracked, and the
  TMDB key, Trakt secret and session signing key are all in the OS keyring, each
  read behind `try/except` so a machine with no keyring backend degrades instead
  of failing.
- [x] I2. **Auth verified by probing, not by reading. Done 2026-07-26.** Every
  route was requested anonymously with auth enabled: **60 of 61 refuse**, and the
  only one that answers is `login`, which is correct. The exempt list is exactly
  `{'login', 'static'}`.
  Mechanics check out: passwords stored with `generate_password_hash` and
  compared with `check_password_hash`, sessions `HttpOnly` and `SameSite=Lax`,
  five failed attempts then a five-minute lockout. `Secure` is deliberately unset
  with a comment explaining why — the app is served over plain HTTP on a LAN, and
  setting it would stop the cookie being sent at all. That is the right call for
  the deployment, and worth revisiting only if it is ever put behind TLS.
- [x] I3. **All SQL parameterised. Done 2026-07-26.** Every `execute` call was
  extracted by AST: 50 use a literal query, 5 build one. All five checked
  individually and all are safe — an `IN` clause whose placeholders are generated
  from `len()` with the values passed as parameters, two `ALTER TABLE` statements
  whose column names come from a hardcoded list, the schema constant, and a
  script whose `WHERE` is assembled from a fixed per-argument template with the
  values bound. No user input reaches SQL text.
- [x] I4. **Directory traversal found and fixed. Done 2026-07-26.**
  `/api/video/<id>/direct-stream/<path:filename>` rejected `..` and a leading
  `/`, which reads as sufficient and is not: **`os.path.join` discards the base
  when the second part is absolute**, so `C:/anywhere/file.mp4` walked straight
  out of the transcode cache. Confirmed by planting a canary file in a temp
  directory and retrieving it over the route — **HTTP 200 with the contents**.
  Any `.mp4`, `.m3u8` or `.m4s` on the machine was readable.
  Fixed with the containment check already trusted in `_resolve_episode_file`:
  resolve with `realpath`, then require the result to sit inside the cache
  directory. The sibling HLS route was never vulnerable — its
  `^segment_(\d+)\.ts$` whitelist rejects everything else, which is why the two
  routes behaved differently despite looking alike.
  Covered permanently by `tests/test_path_traversal.py` (14 assertions), which
  also asserts that ordinary segment names still work — a fix that broke playback
  would be no fix.
- [x] I5. **TMDB image restriction holds. Done 2026-07-26.** Poster downloads are
  refused unless the URL starts with `https://image.tmdb.org/t/p/`, so a crafted
  request cannot make the server fetch an arbitrary host, and a lookalike like
  `image.tmdb.org.evil.example` fails the prefix test. Covered by
  `tests/test_posters.py`.
- [x] I6. **Debug mode cannot be reached remotely. Done 2026-07-26.**
  `debug_enabled = not RUNNING_PUBLIC`, so enabling public access disables the
  Werkzeug debugger — which would otherwise expose an interactive console
  executing arbitrary code. A warning is logged on every public bind.
- [x] I7. **Front-end XSS audited. Done 2026-07-27, unblocked by F1/F2.** With
  the JavaScript in files rather than inline, every interpolation into markup
  could be traced instead of spot-checked. **No XSS found.**
  Of 88 interpolations on markup-building lines: 49 go through `escHtml`/
  `escAttr` directly, 6 are numeric, and the rest resolve to one of three safe
  shapes — nested ternaries whose arms are themselves escaped template literals,
  variables holding markup already built with the helpers (`rows`, `poster`), or
  a value that is a hardcoded literal at every call site (`kind`, always
  `'title'` or `'collection'`). Each was followed to its source.
  Sinks are clean too: no `document.write`, `outerHTML`, `eval`, `new Function`,
  `srcdoc` or `javascript:` URL, and the single `insertAdjacentHTML` takes a
  hardcoded string.
  The load-bearing detail is that **`escAttr` does not escape the single quote**,
  so it is sufficient only while every interpolated attribute is double-quoted.
  That held everywhere, and is now asserted rather than assumed.
  `tests/test_escaping.js` (14 assertions) runs the shipped helpers against real
  attack strings and enforces the invariants the audit rested on, so this stays a
  property of the code rather than a fact about one afternoon.
- [ ] I8. **No security headers.** No CSP, `X-Content-Type-Options`, or
  `Referrer-Policy`. The instructions ask for these "when applicable"; on a LAN
  app the main value is a CSP limiting what injected markup could do, which pairs
  naturally with I7.

---

## Section J — Documentation consistency

**Scope:** `README.md`, `Common/*.md`.

- [ ] J1. `README.md` — verify setup steps still work from a clean clone, given
  B4 means the tracked state is incomplete.
- [ ] J2. `copilot-instructions.md` repo map is stale *(same as A6)*.
- [ ] J3. `Common/Workflow.md` is untracked — decide whether it is part of the
  governance set alongside Roadmap/Runbook/CHANGELOG.
- [ ] J4. Check Runbook against current behaviour, particularly the public-access
  and auth sections which are recent.
- [ ] J5. Confirm CHANGELOG and Roadmap agree on what has shipped.

---

## Suggested order

Ordered by risk and by what unblocks other work.

1. ~~**B1** — `media.db` ignore rule.~~ **Done.**
2. ~~**A1** — un-ignore `.github/`.~~ **Done.**
3. ~~**H1/H2** — rescue the tests out of the scratchpad.~~ **Done** — 19 files,
   424 assertions, all passing.
4. ~~**B4** — get the untracked source committed so the repo builds.~~ **Done** —
   verified by a clean clone.
5. ~~**Section D** — declare the two undeclared dependencies.~~ **Done** (D4
   remains, blocked on the tooling choice below).
6. ~~**A2–A5, C4** — settle the tooling and write it down.~~ **Done** — ruff
   adopted, 101 violations fixed, D4 unblocked and also done.
7. ~~**A8** — clear the 141 remaining lint findings.~~ **Done** — `ruff check .`
   passes clean.
8. ~~**C2** — CI.~~ **Done** — and it immediately found a startup bug (C2a).
9. **Sections E and F** — the two large refactors, only after tests exist to
   catch regressions.
10. **Sections I, G, J** — audit passes.

Items 1–6 are done. Items 9 onward are the substantial work, and are unblocked by
the test suite recovered in item 3.

## Progress log

- **2026-07-26** — A1, B1, B3, H1, H2, H4 done. Two `.gitignore` fixes (the live
  database and the transcode cache were both exposed; the standards directory was
  hidden), and the test suite recovered from temporary storage into `tests/`.
- **2026-07-26** — E1 in progress. `medialibrary/` package created and three
  clusters moved out of app.py — playback/HLS (558), identify (412), subtitles
  (332) — plus shared config. app.py drops 5,386 → 4,299 lines. Every moved name
  is re-imported into app.py, so no caller changed anywhere. Verified against a
  clean checkout through the full CI sequence after each cluster. Stopped at the
  qBittorrent cluster, which needs injection rather than a move (E1a).
- **2026-07-26** — C2 done, plus C2a. CI workflow added and validated by running
  every step by hand against a clean checkout, which found that the app returned
  500 from any entry point other than `python app.py` because the schema was only
  created under `__main__`. Also corrected B1: `library.db` is the live database
  and was always ignored; `media.db` is an empty stray. The original finding was
  stated from a filename rather than the file's contents.
- **2026-07-26** — A8 done. `ruff check .` passes clean across all 20 Python
  files: 27 substantive findings resolved (two of them refused as wrong, with the
  reason recorded at the line) and 114 long lines rewrapped by hand across 23
  files. The formatter was measured as an alternative and reverted — logged as
  A9 for you to decide.
- **2026-07-26** — A2, A3, A4, A5, C4, D4 done, and one real bug fixed in E5.
  `ruff` adopted with config in `pyproject.toml`; 101 violations fixed
  automatically, verified by the suite and an app start. Three rules disabled
  where they contradicted the style guide, on the principle that the guide is the
  standard and the tool serves it. Adopting the linter is what surfaced the
  file-descriptor leak in `_start_direct_stream`. 141 findings remain, logged as
  A8.
- **2026-07-26** — D1, D2, D3, D5 done. Two directly-imported packages were
  relying on someone else's dependency list; both now declared, and a single
  pinning policy applied. D4 left open behind the tooling choice.
- **2026-07-26** — B4, B5 done. 50 outstanding files committed to branch
  `commit-outstanding-work` in seven logical commits. Verified by clean clone:
  compiles, 19/19 test files pass, standards included, no database leaked.
  **Branch is not merged** — that decision is yours.
