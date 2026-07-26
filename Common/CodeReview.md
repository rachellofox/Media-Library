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

### Survey baseline

Taken 2026-07-26, so later sections can be sized against it.

| Measure | Value |
| --- | --- |
| Python files | 20 (9,359 lines) |
| Largest module | `app.py` — 5,318 lines, 61 routes, 205 functions |
| Templates | 3 (5,362 lines, of which 3,304 are inline JS) |
| Tests in repo | 19 files, ~424 assertions *(recovered — was **0**)* |
| CI workflows | **0** |
| Style violations (dividers, dead code, missing docstrings) | **0** |
| Lines over 100 chars | 80 |

The style scan result is worth stating plainly: the Python already follows the
commenting and docstring rules closely. This review is therefore mostly about
**structure, safety nets and repo hygiene**, not a comment-cleaning exercise.

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
- [?] A9. **Adopt `ruff format` as well?** Measured rather than guessed: running
  it would fix 88 of the 114 long lines automatically and the suite still passed
  afterwards — but it rewrites **3,794 lines across 34 files**, converting `'''`
  to `"""` and exploding deliberately grouped argument lists to one per line.
  That is a formatter adoption, which is a bigger decision than clearing lint,
  and it overrides layout you chose on purpose. Reverted, and the 114 lines were
  wrapped by hand instead. Worth revisiting as its own decision: a formatter
  makes consistency permanent and ends line-length debate, at the cost of one
  large diff and some hand-tuned layout. *Your call.*
- [ ] A6. **`copilot-instructions.md` describes a repo that no longer exists.**
  It lists `imdb_client.py` as the metadata client (it is dead — see B6, and
  TMDB is the real client) and points at `docs/` (deleted — see C3). It omits
  `naming.py`, `episode_match.py`, `subtitle_client.py`, `trakt_client.py` and
  `tmdb_client.py` entirely.
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
- [?] B2. **`media.db` is an empty stray** (0 bytes, no tables). Nothing reads or
  writes it — `DB_PATH` points at `library.db`. Propose deletion. *Your call.*
  `library.db` is the live database and must stay.
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
- [?] B6. **`imdb_client.py` (88 lines) is imported by nothing.** TMDB replaced
  it. Propose deletion. *Your call.*
- [?] B7. **`tmp/reference/TranscodeManager.cs`** — propose deletion or move out
  of the repo, unless you still want it as a reference. *Your call.*

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
- [ ] C7. **`scripts/` naming.** Every script is `_`-prefixed, which in Python
  signals "private". They are in fact operator tools. Propose either dropping the
  prefix or documenting what it means — and adding `scripts/README.md`, since
  there are now 10 of them with no index.
- [ ] C8. **Root is crowded** — 9 Python modules at top level. A `medialibrary/`
  package would be conventional, but this is a real refactor with import churn.
  Raised for a decision, not assumed. *Related to E1.*

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

- [ ] E1. **Decide the split first.** Everything else in this section is easier
  once the file is divided. Natural seams, by inspection: auth/session, settings,
  library scan and import, downloads and qBittorrent, playback and HLS, TV and
  episodes, Discover, and the routes themselves. Proposal is a `medialibrary/`
  package with `app.py` reduced to app setup and route registration. **Big
  change — needs your agreement before any code moves.**
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

- [ ] F1. **`index.html` is 4,055 lines with 2,619 lines of inline `<script>`.**
  It cannot be linted, tested, or cached by the browser separately. This is the
  front-end equivalent of E1 and the largest single maintainability problem after
  `app.py`.
- [ ] F2. **`player.html` holds a further 685 lines of inline JS**, some of which
  duplicates `index.html` (playback URL building, subtitle handling).
- [ ] F3. No JS linting or formatting standard exists at all — there is no
  instructions file covering `**/*.js` or `**/*.html`.
- [ ] F4. Review for genuinely dead JS — functions no longer called by any handler.

---

## Section G — `scripts/`

**Scope:** 10 maintenance scripts, 1,523 lines.

**Check for:** which are one-off and spent, which are ongoing tools, and whether
the destructive ones are safe.

- [-] G1. All 10 carry module docstrings, as the standard requires. Verified by scan.
- [ ] G2. **Classify each as ongoing tool or spent one-off.** Several were written
  for a single migration that has now been applied. Candidates for deletion once
  classified — `[?]` each, since a spent script is still a record of what was done.
- [ ] G3. **Audit the destructive ones** (`_consolidate_duplicates`, `_split_packs`,
  `_qbt_prune_broken`, `_plan_tv_naming --apply`) for dry-run-by-default and for
  `os.rename` rather than `shutil.move` — the latter caused a 10 GB duplication
  incident and the fix must not regress.
- [ ] G4. `_debug_missing.py` (30 lines) and `_debug_query.py` (58 lines) look
  like scratch debugging kept by accident. *Deletion candidates, your call.*
- [ ] G5. Add `scripts/README.md` — what each script is for, and which are safe
  to run. *(Also C7.)*

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

- [ ] I1. Verify no secrets in tracked files or git history (`.env` is ignored;
  `keyring` is used for the Trakt secret — confirm nothing else is stored plainly).
- [ ] I2. Review the auth added for public access: session handling, lockout,
  password storage, and that every route and stream is actually covered.
- [ ] I3. Confirm all SQL is parameterised, per the style rule. `storage.py` is
  the file to audit.
- [ ] I4. Review path handling on every route that takes a file path — directory
  traversal is the obvious risk in a media server.
- [ ] I5. Confirm the TMDB image-prefix restriction still holds and cannot be
  bypassed by a lookalike host.
- [ ] I6. Check debug mode cannot be enabled while public access is on.

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
