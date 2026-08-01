# Contributing

A personal project, so this is mostly a note to my future self about the
conventions already in use rather than a process for a team.

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate           # Windows
pip install -r requirements-dev.txt
python app.py
```

`requirements-dev.txt` pulls in the runtime dependencies and adds `ruff`. The
test suite needs no Python packages at all — only `node` on PATH, for the
front-end tests.

## Before committing

```bash
python -m ruff check .          # must pass
python -m ruff format .         # owns layout; do not hand-tune spacing
python tests/run_all.py         # must pass
```

CI runs exactly these, plus a compile pass and a request to `/` to prove the app
still answers. Import alone proves little — it is the request path that breaks
when a route or template is wrong.

**Start the app after any change to routes, templates or `url_for`.** The suite
is good at catching logic regressions and blind to wiring ones: moving routes
into blueprints broke every `url_for('index')` in the codebase while all 22 test
files still passed.

## Commits

`type: summary in the imperative`, lower case, no trailing full stop.

Types in use: `feat`, `fix`, `refactor`, `test`, `docs`, `style`, `build`, `ci`,
`chore`, `security`.

The body is where the value is. Say **why**, and say what was tried and rejected
— a commit that records "shutil.move was replaced with os.rename because it
silently degrades to copy-then-delete" is worth more than the diff. If a change
was verified, say how it was verified.

## Branches

Work on a branch; `main` is the default and stays mergeable. Name it after the
work (`commit-outstanding-work`, `tv-episode-ordering`), not after a ticket
number, since there are no tickets.

## Where things go

- `app.py` — application setup and wiring only. **No routes.**
- `medialibrary/web/` — route blueprints, one module per area
- `medialibrary/` — everything else. **These modules never import `app`**; they
  take what they need through `configure()` at startup, always as *getters*,
  because `tmdb` is rebuilt when the API key changes and the tests replace the
  store wholesale.
- `static/js/` — front-end behaviour. Templates hold markup and the inline
  bootstrap of server-injected values, nothing more.
- `scripts/` — operator tools, dry-run by default. See `scripts/README.md`.
- `tests/` — see `tests/README.md`.

One rule worth knowing before moving code: **re-exporting a name keeps callers
working but does not keep monkeypatching working.** Anything a test stubs has to
be reached through its module, not through a re-export.

## Where notes go

This eroded once already — the Roadmap was a table with Priority and Status in the
first commit, drifted into prose bullets, and grew to 383 lines of finished work.
The rule is written down here so the next session follows it rather than
following whatever the file currently looks like.

| Kind of note | Where it goes |
| --- | --- |
| Why *this line* is written this way | A comment on the line |
| Why *this change* was made, what was rejected | The commit message |
| Something broken | `Common/Roadmap.md` → **Bugs** table |
| Something not built yet | `Common/Roadmap.md` → **Features** table |
| Open repo-health work | `Common/CodeReview.md` |
| What a user would notice | `Common/CHANGELOG.md` |
| Session narrative, dead ends, handoff | `Common/DevLog.md` (not tracked) |

**Bugs and features are tracked separately.** A bug is something that does not
do what it already promises; a feature is something the app does not do yet.
When an item is arguably both, file it as a bug — "is this broken?" is the
question that decides how soon it gets looked at.

### Item IDs

`F-DDMM.NN` for a feature, `B-DDMM.NN` for a bug: the kind, the day it was
raised, and a counter within that day. `F-0108.01` is the first feature raised
on 1 August. The counter restarts daily, so it only has to be unique within its
own date — no need to scan the whole file for the next free number.

An item keeps its ID for life, date included. The date says when it was raised,
not when it was last touched, so an ID that has been sitting around a while
looks like it. One that turns out to be the other kind keeps its number and
swaps its letter — `B-0108.04` becomes `F-0108.04`.

Quote the ID in the commit that closes it.

These replaced a plain `ML-n` sequence on 1 August 2026, which carried no
information and had to be tracked by hand. Commits before that date cite the old
ids; this is what they map to:

| Old | New | | Old | New |
| --- | --- | - | --- | --- |
| ML-8 | F-0108.01 | | ML-10 | B-0108.01 |
| ML-9 | F-0108.02 | | ML-11 | B-0108.02 |
| ML-16 | F-0108.03 | | ML-17 | B-0108.03 |
| ML-12 | F-0108.04 | | ML-19 | B-0108.04 |
| ML-13 | F-0108.05 | | ML-18 | B-0108.05 |
| ML-15 | F-0108.06 | | | |

Everything above carries 1 August because that is when the scheme started, not
because it was all raised that day. ML-14 is absent because it never appears
anywhere in this repo's history — a gap in the old numbering, not a lost item.

Both land in the same CHANGELOG, which is written for a user and so groups by
what changed for them, not by which table the work came from. A fixed bug reads
"Fixed …"; a shipped feature just says what it now does.

**A shipped item is deleted from the Roadmap, not marked done** — the CHANGELOG
is the record of what shipped. Never delete a Roadmap row until the CHANGELOG
covers it, or the item vanishes from the record entirely.

`Common/DevLog.md` is deliberately gitignored, so it is not backed up. Nothing
that matters long-term should live only there.

## The standards

`.github/instructions/` holds the coding standards, and they win over a tool's
defaults — where `ruff` disagrees with them, the rule is switched off in
`pyproject.toml` with the reason next to it.

`Common/CodeReview.md` is the standing review of the repo, worked section by
section. `Common/CHANGELOG.md` records anything a user would notice.
