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

## Features

Something the app does not do yet.

| ID | Title | Source | Priority | Status |
| -- | ----- | ------ | -------- | ------ |
| F-0108.01 | Per-episode playback progress — resume, watched marks, next-episode progression | Second pass of the TV work | P2 | Approved |
| F-0108.02 | Carry the detected episode ordering into the app, so the episode list matches the filenames | Follow-on from episode ordering | P2 | Approved |
| F-0108.03 | Surface duplicate episode copies instead of silently discarding them | Review finding | P2 | Approved |
| F-0108.04 | Tidy the settings page into sections (network, preferences, storage — categories to confirm) | User need | P3 | Proposed |
| F-0108.05 | Move the favourites icon | User need | P3 | Proposed |

## Library

The collection on disk being wrong. No code changes; the fix is to the files.

Nothing open. These rows are deleted once the files are right, and the commit
that corrected them is the record — there is no CHANGELOG line, because nothing
about the app changed.

Priorities for B-0108.03 and B-0108.04 were assumed, not given — adjust if wrong.

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

### F-0108.03 — duplicate episode copies

`scan_local_episodes` keeps the largest of two files claiming one episode and
drops the other without recording it, so the app knows about every duplicate it
holds and tells the user about none of them.

All 78 have since been resolved by hand, which is what this item exists to make
unnecessary: they were only ever found because a renaming pass happened to make
them legible, and the app never mentioned them at all.

**These are not a good copy and a bad copy.** Each X-Men pair is the original
episode at its native 638x480 beside an AI upscale of it at 1442x1080, and
Avatar is the same arrangement the other way round in the folder. An upscale is
invented detail, not recovered detail: it is the one to keep if the show is
being watched on a large screen and the wrong one to keep if the original
transfer is what matters. Nothing in a file can decide that, which is the whole
case for reporting rather than resolving. Largest-wins currently discards the
originals without saying so.

The renaming work did not cause this, though it is how the duplicates came to
light: every copy was modified in June 2024, from two separate downloads of each
show. Giving both copies the same canonical name made a pairing visible that had
been on disk, unnoticed, for two years — which is the argument for reporting
duplicates rather than the argument against it.

Every pair was then checked by picture rather than by name, because Explorer
picks its thumbnail from a different position for `.mkv` than for `.mp4` and the
pairs look like different scenes in a folder listing. Comparing a frame at the
same fraction of running time settled 74; the remaining four needed a 30-second
window slid against the other copy to find the offset, since a fixed timestamp
lands either side of a cut, and Avatar's `.mkv` copies carry 30s and 77s more
intro than their `.mp4`. All 78 are the same episode twice. Any report built
here should list the facts and let a person decide — a filename is not evidence
that two files hold the same thing, and neither is a thumbnail.

**The work is to report duplicates, not to choose between them better.** A
duplicate is a housekeeping decision that wants a person: which copy to keep is
a judgement about codecs, subtitles and disc space, and deleting the wrong one
is unrecoverable. `scan_local_episodes` already has the shape for this — files
it cannot match are returned as `unmatched` and surfaced through
`/api/tv/<id>/unmatched` and the season payload's `unmatched_count`. Duplicates
should travel the same route: returned beside `matched`, counted, listed with
size, resolution, codec and subtitle count so the choice can be made on sight,
and left entirely alone on disk. Largest-wins stays as the display default,
because the episode list still has to show something.

One case argues for reporting rather than a cleverer heuristic. Seven Buffy
episodes hold an `.mp4` ffprobe cannot open at all — *moov atom not found*, the
signature of a download that stopped before its index was written. They are not
small: S01E06's broken copy is 1383.6 MB against a working 1411.5 MB. Largest
happens to win correctly by 2%, and no rule tuned on size would have known why.
A duplicates list with a "cannot be read" mark makes it obvious.

**The embedded container title cannot help choose.** It is on 76 of the 170
copies, and on every one it is the copy largest-wins rejects — and read as a
description of the file it is simply wrong: each X-Men `.mp4` claims `1080p AI
Upscale PROPER` while being 638x480. It is naming the release the file was
downloaded from, and that release is the upscale *set*, of which this file is
the source. So the title stays an identity signal — what a file is, which is how
it settled the Batman ordering, and how L-0108.01 was resolved — and never a
quality
one. Worth remembering that it can describe a sibling rather than itself.

Nothing has been deleted.

### B-0108.05 — "Back to TV Shows" scrolls away

Blocked on a decision, not on work. Either pin it so it stays put while the page
scrolls, or move it into the bottom-left of the hero pane where it is always on
screen at the top and is not needed further down. Pinning is the smaller change
and helps most on a long season list; moving it keeps the chrome quieter.

