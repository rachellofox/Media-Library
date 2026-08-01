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
| ML-16 | Surface duplicate episode copies instead of silently discarding them | Review finding | P2 | Approved |
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

### ML-16 — duplicate episode copies

The library holds 78 episodes twice — X-Men (76) and Avatar (2), about 9.7 GB.
`scan_local_episodes` keeps the largest of each pair and drops the other without
recording it, so the app knows about every one of those files and tells the user
about none of them.

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
copies, and on every one it is the copy largest-wins rejects — and it
misdescribes the file: each X-Men `.mp4` claims `1080p AI Upscale PROPER` while
being 638x480, beside an untitled 1442x1080 HEVC copy with six subtitle tracks.
It names the release, not the file, so it stays an identity signal (it settled
the Batman ordering, and ML-15 rests on it) and not a quality one.

Nothing has been deleted.

### ML-15 — Batman Season 04

Season 04 on disk is *The New Batman Adventures*, a different series, which is why
no Batman ordering covers it and its 24 files carry numbers without titles. Their
containers name them correctly ("Holiday Knights", "Mad Love"), so they could be
titled from the embedded metadata or matched against TNBA's own TMDB entry —
whichever suits how the show should be grouped.
