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

## Features

Something the app does not do yet.

| ID | Title | Source | Priority | Status |
| -- | ----- | ------ | -------- | ------ |

## Library

The collection on disk being wrong. No code changes; the fix is to the files.

| ID | Title | Source | Priority | Status |
| -- | ----- | ------ | -------- | ------ |

These rows are deleted once the files are right, and the commit that corrected
them is the record — there is no CHANGELOG line, because nothing about the app
changed.

Every 0108 item carries the date this scheme started, not necessarily when it
was raised — see the note at the top of this file.

## Detail

Only where a row needs more than its title. Anything much longer than a paragraph
is a sign it should be worked on rather than described.
