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
| B-0108.01 | Subtitles do not work in the player | User need | P1 | Proposed |
| B-0108.02 | Transcoded playback: poor quality, stream freezes, high CPU — full diagnosis needed | User need | P1 | Proposed |
| B-0108.03 | Trakt watching list not loading | User need | P1 | Proposed |
| B-0108.04 | Upgrade check on a TV show reports "complete" without opening the torrent pane, which is what it does for a film | User need | P2 | Proposed |
| B-0108.05 | "Back to TV Shows" scrolls out of reach — it can only be pressed from the top of the page | User need | P3 | Blocked |
| B-0108.06 | A completed movie download is not moved into the Movies folder, renamed, or restructured into the expected folder and file layout | User need | P1 | Proposed |
| B-0108.07 | Discover's "add new movie" offers local file selection and a quality choice instead of showing in the hero card and pulling a torrent like everywhere else | User need | P1 | Proposed |

## Features

Something the app does not do yet.

| ID | Title | Source | Priority | Status |
| -- | ----- | ------ | -------- | ------ |
| F-0108.01 | Per-episode playback progress — resume, watched marks, next-episode progression | Second pass of the TV work | P2 | Approved |
| F-0108.02 | Carry the detected episode ordering into the app, so the episode list matches the filenames | Follow-on from episode ordering | P2 | Approved |
| F-0108.04 | Tidy the settings page into sections (network, preferences, storage — categories to confirm) | User need | P3 | Proposed |
| F-0108.05 | Move the favourites icon | User need | P3 | Proposed |
| F-0108.06 | "Find missing" on a show should offer the whole season as well as a single episode | User need | P2 | Proposed |

## Library

The collection on disk being wrong. No code changes; the fix is to the files.

| ID | Title | Source | Priority | Status |
| -- | ----- | ------ | -------- | ------ |

Nothing open. These rows are deleted once the files are right, and the commit
that corrected them is the record — there is no CHANGELOG line, because nothing
about the app changed.

Priorities for B-0108.03, B-0108.04, B-0108.06, B-0108.07 and F-0108.06 were
assumed, not given — adjust if wrong.

Every item above carries today's date because that is when this scheme started,
not because they were all raised today. Dates are meaningful from here on.

## Detail

Only where a row needs more than its title. Anything much longer than a paragraph
is a sign it should be worked on rather than described.

### F-0108.01 — per-episode playback progress

`playback_positions` is keyed by media item, so a show remembers one position
across every episode. It needs an episode key, and then resume, watched marks and
next-episode progression follow from it.

### F-0108.02 — carry the detected episode ordering into the app

The rename tool works out which ordering a show's files use, but the episode list
still asks TMDB for the default — so Firefly's browser shows broadcast-order
titles and synopses beside DVD-order files even though the filenames are right.

Detection is too slow to repeat per request, since it reads every file's running
time. So persist what the tool found (media_id → episode group id) and have
`/api/tv/<id>/season/<n>` read the ordering from there, falling back to the
default. Worth an override in the show's Settings card for the cases detection
cannot call — two orderings with the same episode count and interchangeable
runtimes.

### B-0108.05 — "Back to TV Shows" scrolls away

Blocked on a decision, not on work. Either pin it so it stays put while the page
scrolls, or move it into the bottom-left of the hero pane where it is always on
screen at the top and is not needed further down. Pinning is the smaller change
and helps most on a long season list; moving it keeps the chrome quieter.
