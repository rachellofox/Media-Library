# Roadmap

Single source of truth for approved and open work.

## How this file is maintained

- **Open work only.** An item that has shipped is deleted from here, not marked
  done. The record of what shipped is `CHANGELOG.md`.
- **Three kinds of work, three tables.** A **bug** is the app not doing what it
  already promises. A **feature** is something the app does not do yet. A
  **library** item is the collection on disk being wrong, where no code changes
  at all. If an item is arguably both a bug and a feature, file it as a bug —
  the question "is this broken?" is the one that decides how soon it is looked
  at.
- **One row per item**: ID, Title, Source, Priority, Status.
- **IDs are `F-DDMM.NN`, `B-DDMM.NN` or `L-DDMM.NN`** — the kind, the day it was
  raised, and a counter within that day. `F-0108.01` is the first feature raised
  on 1 August. The counter restarts each day, so it only has to be unique within
  its own date.
- **An item keeps its ID for life**, including the date, which records when it
  was raised rather than when it was last touched. One that turns out to be the
  other kind keeps its number and swaps its letter, so `B-0108.04` becomes
  `F-0108.04` and the history still leads to it.
- **Nothing is removed until the CHANGELOG covers it.** The Roadmap says what is
  coming; the CHANGELOG says what arrived. An item deleted without a CHANGELOG
  line disappears from the record entirely. **Library items are the exception**
  — nothing about the app changed, so there is nothing to tell a user. They are
  deleted when the files are right, and the commit that fixed them is the
  record.
- **Rationale does not live here.** Why something was built the way it was
  belongs in the commit message, or in a comment on the line it explains. Long
  `[DONE]` write-ups are how this file grew to 383 lines of finished work.
- Repo-health work is tracked separately in `CodeReview.md`, so the two lists do
  not compete. This file is for product work.

Priorities: **P1** wanted next · **P2** wanted · **P3** would be nice.

## Status Model

- Proposed — noted, not committed to
- Approved — agreed, not started
- In Progress — being worked on now
- Blocked — waiting on a decision or something external
- Done — shipped; the row is deleted and a CHANGELOG line takes its place

## Bugs

Something that does not do what it already promises.

| ID | Title | Source | Priority | Status |
| -- | ----- | ------ | -------- | ------ |
| B-0108.02 | Transcoded playback: poor quality, stream freezes, high CPU — full diagnosis needed | User need | P1 | Proposed |
| B-0108.07 | Discover's "add new movie" offers local file selection and a quality choice instead of showing in the hero card and pulling a torrent like everywhere else | User need | P1 | Blocked |
| B-0108.09 | ffprobe subprocess calls decode as cp1252 with no explicit encoding, so a file whose metadata isn't valid cp1252 throws an unraisable exception on Windows | Review finding | P3 | Proposed |

## Features

Something the app does not do yet.

| ID | Title | Source | Priority | Status |
| -- | ----- | ------ | -------- | ------ |
| F-0108.04 | Tidy the settings page into sections (network, preferences, storage — categories to confirm) | User need | P3 | Proposed |
| F-0108.05 | Move the favourites icon | User need | P3 | Proposed |
| F-0108.08 | Choose between torrent mirrors and the underlying trackers — mirrors usually carry fewer results and fewer seeds | User need | P3 | Proposed |

## Library

The collection on disk being wrong. No code changes; the fix is to the files.

| ID | Title | Source | Priority | Status |
| -- | ----- | ------ | -------- | ------ |

These rows are deleted once the files are right, and the commit that corrected
them is the record — there is no CHANGELOG line, because nothing about the app
changed.

Every item above carries today's date because that is when this scheme started,
not because they were all raised today. Dates are meaningful from here on.

Priorities for B-0108.09 and F-0108.08 were assumed, not given — adjust if
wrong. F-0108.08's ID and Status were also completed from a raw line that had
neither.

## Detail

Only where a row needs more than its title. Anything much longer than a paragraph
is a sign it should be worked on rather than described.

### B-0108.07 — Discover's "add new movie" flow

Investigated, not fixed — blocked on a reproduction. Read every path that can
add a title from Discover (`hero-add-btn`, `hero-add-missing-btn`, both
funnelling through `addDiscoverHeroItem()` to `/api/discover/add-and-search`)
and ran the real route in a sandbox: it adds the library row with no path and
no quality set, returns torrent candidates, and the client opens the same
torrent modal every other add path uses. No "local file selection" or
"quality choice" control exists anywhere in the templates or JS — grepped for
both. This is what the row asks for already.

Two explanations fit: this was already fixed by earlier work on this branch
and the report predates it, or it is a specific case (a particular title, a
particular error path) that a straight read of the code does not surface.
Needs a live reproduction — which title, and what the screen actually shows —
to go further.

### B-0108.09 — ffprobe subprocess calls assume cp1252

Found live while running episode-ordering detection (F-0108.02) against the
real library: several files threw `UnicodeDecodeError` from a background
reader thread, because `subprocess.run(..., text=True)` with no `encoding=`
falls back to `locale.getpreferredencoding()`, which is cp1252 on this
machine. ffprobe's own output is UTF-8. The same pattern is repeated across six
call sites (`episode_ordering.py`, `playback.py`, `quality.py`, `subtitles.py`
×3) — one place, fixed six times, or missed the same way in a seventh.

Did not corrupt these three detections — Batman, Firefly and Parks and
Recreation all matched what was established earlier by hand — because the
exception happens in a stdlib reader thread outside `_probe`'s own try/except
and the run still completed for the affected files, likely on a mostly-empty
capture. Worth fixing on its own pass (`encoding='utf-8', errors='replace'` on
each), not folded into the change that happened to notice it.

