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
| ML-16 | Refuse unreadable copies in `_best_local_video_path` — size alone can pick a file that will not play | Review finding | P2 | Approved |
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

Rewritten after ffprobing every duplicate in the library — 85 episodes held
twice, across X-Men (76), Buffy (7) and Avatar (2). The earlier Angel and
Dollhouse examples are gone; both now hold a single copy.

**Largest-wins is not the defect.** It picked the highest-resolution copy in all
85 groups. What it lacks is a readability check:

- Seven Buffy episodes hold an `.mp4` that ffprobe cannot open at all — *moov
  atom not found*, zero streams, the signature of a download that stopped before
  the index was written. They are not small: S01E06's broken copy is 1383.6 MB
  against a working 1411.5 MB. A 2% swing in the other direction and
  `_best_local_video_path` would hand the player a file that cannot be decoded,
  and the failure would surface as a transcode that dies rather than as
  "this file is broken".

So the fix is to drop candidates with no decodable video stream before ranking,
not to replace size with a cleverer score. Probing every file on every call is
too slow, so probe lazily: rank by size as now, and step to the next candidate
when the chosen one has no video stream.

**The embedded container title cannot help here.** It is on 76 of the 170 copies
— and on every single one it is the copy largest-wins *rejects*. Worse, it lies:
each X-Men `.mp4` carries `X-Men The Animated Series 1080p AI Upscale PROPER -
https://archive.org/...` while being 638x480, beside an untitled 1442x1080 HEVC
copy with six subtitle tracks. The title is a good signal for *what a file is*
(it settled the Batman ordering, and ML-15 rests on it) and a bad one for *which
copy is better* — it describes the release, not the file.

Nothing has been deleted.

### ML-15 — Batman Season 04

Season 04 on disk is *The New Batman Adventures*, a different series, which is why
no Batman ordering covers it and its 24 files carry numbers without titles. Their
containers name them correctly ("Holiday Knights", "Mad Love"), so they could be
titled from the embedded metadata or matched against TNBA's own TMDB entry —
whichever suits how the show should be grouped.
