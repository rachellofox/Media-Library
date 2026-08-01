# Roadmap

Single source of truth for approved and open work.

## How this file is maintained

- **Open work only.** An item that has shipped is deleted from here, not marked
  done. The record of what shipped is `CHANGELOG.md`.
- **One row per item**, in the table below: ID, Title, Source, Priority, Status.
- **Nothing is removed until the CHANGELOG covers it.** The Roadmap says what is
  coming; the CHANGELOG says what arrived. An item deleted without a CHANGELOG
  line disappears from the record entirely.
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

## Open work

| ID | Title | Source | Priority | Status |
| -- | ----- | ------ | -------- | ------ |
| ML-10 | Fix subtitles in the player | User need | P1 | Proposed |
| ML-11 | Transcoding playback: poor quality, stream freezes, high CPU — full diagnosis needed | User need | P1 | Proposed |
| ML-8 | Per-episode playback progress — resume, watched marks, next-episode progression | Second pass of the TV work | P2 | Approved |
| ML-9 | Carry the detected episode ordering into the app, so the episode list matches the filenames | Follow-on from episode ordering | P2 | Approved |
| ML-16 | Decide how duplicate episode copies are resolved — "largest wins" picks badly | Review finding | P2 | Blocked |
| ML-12 | Tidy the settings page into sections (network, preferences, storage — categories to confirm) | User need | P3 | Proposed |
| ML-13 | Move the favourites icon | User need | P3 | Proposed |
| ML-15 | Title Batman's Season 04 from embedded metadata, or match it against The New Batman Adventures | Review finding | P3 | Proposed |

## Detail

Only where a row needs more than its title. Anything much longer than a paragraph
is a sign it should be worked on rather than described.

### ML-8 — per-episode playback progress

`playback_positions` is keyed by media item, so a show remembers one position
across every episode. It needs an episode key, and then resume, watched marks and
next-episode progression follow from it.

### ML-9 — carry the detected episode ordering into the app

The rename tool works out which ordering a show's files use, but the episode list
still asks TMDB for the default — so Firefly's browser shows broadcast-order
titles and synopses beside DVD-order files even though the filenames are right.

Detection is too slow to repeat per request, since it reads every file's running
time. So persist what the tool found (media_id → episode group id) and have
`/api/tv/<id>/season/<n>` read the ordering from there, falling back to the
default. Worth an override in the show's Settings card for the cases detection
cannot call — two orderings with the same episode count and interchangeable
runtimes.

### ML-16 — duplicate episode copies, and what they reveal

`_best_local_video_path` picks the largest file per episode, and on the
duplicates in the library that choice looks wrong or arbitrary:

- **Angel S01E16** — the two files are identical (1540.4 MB, 1920x1080, h264,
  42.8 min). Either can go.
- **Dollhouse S01E12** — kept a 1760 MB h264 file with no subtitles over a
  1330 MB HEVC copy of the same duration that *has* a subtitle track. The
  rejected file is arguably the better one; largest-wins simply favours the less
  efficient codec.

The heuristic needs more than size: resolution, and a duration check against the
TMDB runtime, would have chosen correctly in both. Blocked on deciding what
"better" should mean. Nothing has been deleted.

### ML-15 — Batman Season 04

Season 04 on disk is *The New Batman Adventures*, a different series, which is why
no Batman ordering covers it and its 24 files carry numbers without titles. Their
containers name them correctly ("Holiday Knights", "Mad Love"), so they could be
titled from the embedded metadata or matched against TNBA's own TMDB entry —
whichever suits how the show should be grouped.
