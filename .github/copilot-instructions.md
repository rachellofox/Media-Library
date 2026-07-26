# Media-Library - AI Coding Agent Instructions

## Project Intent

This repository is a local-first media library web app built with Flask and SQLite.

Primary goals:

- keep the app simple and maintainable
- preserve local-first behavior
- avoid breaking existing UI workflows
- keep metadata and quality checks reliable

## Repository Shape

### Application layer

- `app.py`: Flask routes, request handling, and the `store`/`tmdb`/`qb` singletons
- `medialibrary/`: logic split out of `app.py`, none of it importing the app
  - `config.py`: paths, tool locations and cache TTLs — the one source of truth
  - `identify.py`: reading a file or folder — titles, years, packs, episode markers
  - `playback.py`: direct play, direct stream and HLS transcoding
  - `subtitles.py`: sidecar and embedded subtitles, SubRip to WebVTT
  - `qbt.py`: qBittorrent WebUI client
  - `downloads.py`: finalising a completed download into the library
  - `discover.py`: incomplete collections, watchlist, missing episodes
- `storage.py`: SQLite schema and data-access helpers
- `tmdb_client.py`: metadata lookups (TMDB is the metadata source)
- `trakt_client.py`: Trakt collection and watchlist sync
- `subtitle_client.py`: subtitle fetching via subliminal
- `qb_search.py`: qBittorrent search integration
- `quality.py`: quality parsing and comparison
- `naming.py` / `episode_match.py`: canonical filenames, and identifying episodes
  that carry no `SxxExx` marker
- `templates/`, `static/`: pages and assets
- `scripts/`: operator tools, dry-run by default
- `tests/`: the suite — `python tests/run_all.py`

Two rules hold across `medialibrary/`:

- **Modules never import `app`.** Anything they need from the application is
  handed over by `configure()` at startup, and always as a *getter* — `tmdb` is
  rebuilt when the API key changes and `store` is swapped by the tests, so
  holding either directly goes stale.
- **Moved names are re-imported into `app.py`**, so `app.X` keeps resolving for
  the scripts and tests. Note that this keeps *callers* working but not
  *monkeypatching*: anything a test stubs has to be reached through its module,
  not through a re-export.

### Governance and documentation layer

- `README.md`: onboarding and quick start
- `Common/Runbook.md`: operational runbook
- `Common/Roadmap.md`: approved/open work
- `Common/CHANGELOG.md`: shipped changes
- `Common/CodeReview.md`: the standing repo review, worked section by section

## General Rules

- Keep changes minimal and focused.
- Preserve existing behavior unless a change explicitly requires otherwise.
- Avoid hardcoded secrets; use environment variables.
- Update docs when user-facing behavior changes.
- Add or update tests when behavior changes.


## Python/Flask Conventions

- Prefer clear function boundaries and small helpers.
- Use parameterized SQL queries only.
- Keep Flask route handlers thin and move logic to helpers when practical.
- Keep template rendering safe; avoid unsafe HTML injection patterns.
- Follow commenting and style rules in `.github/instructions/python-style.instructions.md`.

## Quality Expectations

- `python -m ruff check .` must pass; config is in `pyproject.toml`.
- `python tests/run_all.py` must pass. Keep it green at every step rather than
  only at the end — the failures worth catching in this codebase have been silent
  and behavioural, not crashes.
- Validate the startup path after route or storage changes. Import alone proves
  little; fetch a page, as `.github/workflows/ci.yml` does.
- Record shipped behavior changes in `Common/CHANGELOG.md`.
