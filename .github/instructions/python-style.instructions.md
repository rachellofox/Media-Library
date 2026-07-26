---
applyTo: '**/*.py'
description: 'Python code style and commenting discipline for Media-Library.'
---

# Python Style and Commenting Rules

## Core Principle

Write code that speaks for itself. Comment only when necessary to explain **WHY**, not **WHAT**. The best comment is the one you do not need to write.

## Comments

### Write comments only when they add genuine value

Good reasons to comment:
- Explain a non-obvious choice or constraint (`# qBittorrent reports 'checkingup' as a done state`)
- Document external API behaviour that isn't obvious from the code
- Annotate a workaround or known limitation (`# TMDB omits release_date for unannounced titles`)
- Mark intentional no-ops (`pass  # subtitle — intentionally kept`)

### Do not write comments that restate the code

```python
# Bad: repeats the variable name
misnamed_videos = []  # list of misnamed videos

# Bad: restates the condition  
if not folder_videos:  # no videos found
```

### Do not use decorative dividers or section banners

```python
# Bad
# ── Report ──────────────────────────────────────────────
```

Use a blank line to separate logical sections instead.

### Do not comment out dead code — delete it

Use version control to recover removed code if needed.

### Allowed annotations

Use these prefixes for structured notes:

```python
# TODO: description
# FIXME: description
# NOTE: non-obvious context future maintainers need
# WARNING: side effect or footgun
```

## Docstrings

- Module-level docstrings: required on every `scripts/` file. Include purpose and usage line.
- Function docstrings: only for non-trivial public functions. One line is enough if the signature is self-explanatory.
- Skip docstrings on trivial helpers and one-liners.

## Naming

Prefer names that make comments unnecessary:

```python
# Bad
x = price * 0.08  # apply tax

# Good
tax = price * TAX_RATE
```

## Line length

Maximum 100 characters. This codifies what the code already does — 99% of
existing lines fit — rather than imposing a new shape on it.

## Linting

`ruff` is the linter and the single source of tool configuration, which lives in
`pyproject.toml`. Install it with `pip install -r requirements-dev.txt`.

```bash
python -m ruff check .          # report
python -m ruff check . --fix    # apply safe fixes
python -m ruff format .         # apply layout
```

`ruff format` owns layout. Do not hand-tune spacing, line breaks or argument
alignment — run the formatter and let it decide, so layout stops being something
to have an opinion about. CI checks both.

Rules that contradict a rule in this document are switched off in
`pyproject.toml`, with a comment saying why — this document wins, not the tool's
defaults. Where a rule is right in general but wrong in one place, suppress it at
that line with `# noqa: RULE` and a comment explaining the exception.

## Type hints

Annotate new functions and functions you are already changing. Do not open a
retrofit campaign across untouched code — mixed annotation is acceptable, a churn
commit that touches everything is not.

## Imports

- One logical group per section (stdlib, then third-party, then local), separated by blank lines.
- Prefer explicit imports over `import os, sys` on the same line when clarity benefits from it.
- `import os, sys` on one line is acceptable in short utility scripts.
  (`E401` is disabled for this reason.)

## Constants

- Use `UPPER_SNAKE_CASE` for module-level constants.
- A short trailing comment is acceptable only when the value itself doesn't explain the constraint:

```python
DISCOVER_COLLECTION_CACHE_HOURS = 24  # acceptable: arbitrary TTL, not self-evident
VIDEO_EXTS = {'.mkv', '.mp4', '.avi'}  # no comment needed — name is clear
```

## SQL

- Use parameterized queries only (`?` placeholders). Never concatenate user input into SQL strings.

## Tests

Live in `tests/`, one file per area, named `test_<area>.py` or `.js`. Run the
suite with `python tests/run_all.py`.

- Never touch the network. Stub TMDB with a fake client exposing only the methods
  under test.
- Never touch the real library or database. Use `tempfile.TemporaryDirectory()`
  and a throwaway SQLite file.
- Front-end tests read the shipped template, extract its `<script>` block and
  evaluate it, so they test what actually ships rather than a copy.

`tests/README.md` has the detail and the current coverage map.
