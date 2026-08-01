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
| Open product work | `Common/Roadmap.md` — one table row, open items only |
| Open repo-health work | `Common/CodeReview.md` |
| What a user would notice | `Common/CHANGELOG.md` |
| Session narrative, dead ends, handoff | `Common/DevLog.md` (not tracked) |

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
