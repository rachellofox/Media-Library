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

- `app.py`: Flask routes and request handling
- `storage.py`: SQLite schema and data-access helpers
- `imdb_client.py`: external metadata lookups
- `qb_search.py`: qBittorrent search integration
- `quality.py`: quality parsing and comparison
- `templates/`: HTML templates for pages
- `static/`: static assets

### Governance and documentation layer

- `README.md`: onboarding and quick start
- `Common/Runbook.md`: operational runbook
- `Common/Roadmap.md`: approved/open work
- `Common/CHANGELOG.md`: shipped changes
- `docs/`: deeper technical references

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

- Run linting and tests (when present) before merge.
- Validate startup path (`python app.py`) after significant route/storage changes.
- Record shipped behavior changes in `Common/CHANGELOG.md`.
