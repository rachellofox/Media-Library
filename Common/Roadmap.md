# Roadmap

Single source of truth for approved and open work.

## Status Model

- Proposed
- Approved
- In Progress
- Blocked
- Done

## Next Features (confirmed next features to implement)

-[DONE] Discover new seasons & episodes. A "New & Missing Episodes" block on Discover
 lists owned shows that are missing episodes which have **already aired**, grouped by show
 then season, with an Airing/Ended badge and the next air date. Clicking an episode searches
 for it (`Show S04E02`) and opens the existing torrent modal, so add-to-library follows the
 movie flow; finalisation files the result as an episode thanks to the TV fix below.
 Seasons or whole shows can be hidden (`discover_ignored_tv`).
 Filtering on air date is essential, not cosmetic: X-Men '97 S02 has 6 aired episodes
 missing and 3 that do not exist yet. Specials are never reported, and a show with no
 identifiable local episodes is skipped rather than reported as entirely missing.
 Caching was the main design constraint — checking 38 shows cold takes ~57s, so seasons and
 show status are cached in the database (`tmdb_season_cache`, `tmdb_tv_status_cache`,
 reusing DISCOVER_COLLECTION_CACHE_HOURS). Warm: 0.26s. The block is fetched separately from
 the rest of Discover so it cannot hold the other blocks up.
 Current state of the library: 10 shows with gaps, 233 aired episodes missing.
 Covered by scratchpad/test_missing_episodes.py (23 assertions, TMDB stubbed).

-[DONE] Per-show entry point for the same data, kept alongside the Discover block since the
 two answer different questions ("what is new anywhere?" vs "what am I missing for this
 show?"). `/api/tv/<id>/missing` reuses `_missing_episodes_for_show()`, the same function the
 Discover block loops over, and renders with the same markup so the two look alike. Uses
 `activateSection`, so the show's hero card stays above the results.
 The chip and the button are deliberately independent, which the first version got wrong:
 gating the button on "airing" left an ended show with gaps unreachable from its own hero —
 Hacks has 5 seasons, 4 owned, status Ended, so its 10 missing episodes showed in Discover
 but had no route from the show itself. Now the **Airing** chip reports production status,
 while the button follows whether anything is actually missing: "Find 10 missing" when there
 are gaps, "Check for new content" for an airing show with none, and hidden entirely for a
 complete ended show. `/api/tv/<id>/seasons` carries `missing_count` so the hero needs no
 extra request. Covered by scratchpad/test_airing_ui.js (29 assertions).

-[FIXED] A search that found nothing was reported as "engine unreachable". nova2 puts
 failures on stderr only for the mirrors that broke — a mirror that answered with no
 matches says nothing — so a zero-result run with some complaints was indistinguishable
 from a dead engine, and got reported as one. `_any_mirror_reachable()` now asks the mirrors
 directly on that ambiguous path only. Confirmed against a real case: The Witcher S04E01
 does not exist on the index (the broad "The Witcher" query returns 120 rows with no stderr
 at all), and now correctly returns "no candidates" instead of a 503. This is the mirror
 image of the earlier bug where a failure looked like an empty result.

-[DONE] Alternative posters. "View metadata / Inspect" is now a gear **Settings** card on
 the hero, opening the same inspector plus an **Artwork** section. Artwork is only fetched
 when asked for — TMDB holds 103 posters for Blade: Trinity alone, too many to pull just for
 opening the panel — with a language filter (English by default, "No text" for the
 language-neutral art, or any language). Choosing one downloads it over the cached file and
 updates the card, hero background and in-memory item together, so the change shows without
 a reload; a cache-buster is needed on those img/background URLs because the poster keeps the
 same filename.
 Only URLs on `https://image.tmdb.org/t/p/` are accepted (`TMDB_IMAGE_PREFIX`), so a crafted
 request cannot make the server fetch an arbitrary URL — a lookalike host like
 `image.tmdb.org.evil.example` is rejected too. A failed download leaves the record alone.
 Covered by scratchpad/test_posters.py (22 assertions).

-[DONE] The "Downloading" chip stays, and now filters. Clicking it (or Enter/Space) switches
 to the matching section with the existing `downloading` toolbar filter applied, and opens
 the filter row so it is visible and can be clicked off again — reusing that filter rather
 than a second mechanism. The media type is stashed on the element because `showSection`
 clears `HERO_STATE` before the filter is applied. Mirrors the genre chips, and the chip only
 advertises itself as a control while it is actually shown.
 Covered by scratchpad/test_downloading_chip.js (16 assertions).

-[DONE] Standard naming convention for TV shows. `scripts/_plan_tv_naming.py` brings episodes
 to `<Show>/Season NN/<Show> - SxxExx - Episode Title.<ext>`, dry-run by default. Applied:
 1811 files renamed, 0 failed, 300 left alone (all genuine bonus material). The quality tag is
 dropped from filenames because quality is read from the file with ffprobe, not the name.
 Identifying files that carry no SxxExx needed `episode_match.py` and four tiers, each earned
 on real data: exact title, TMDB title as a leading phrase ("Beyond Good and Evil (1)" plus a
 subtitle the release added), order-independent token match ("The Phoenix Saga, Part I
 Sacrifice" vs "The Phoenix Saga: Sacrifice (1)"), then resolution by elimination — a bare
 "Night of the Sentinels" must be part 1 once part 2 is claimed by a file that says so.
 Anything still ambiguous is refused rather than guessed.
 The obvious approach would have been wrong: X-Men's files are numbered EP01..EP76, but that
 is production order while TMDB uses broadcast order, so trusting the number would have
 mislabelled 39 of 76 episodes. Titles were the only reliable signal. X-Men went from 0
 identifiable episodes to all 76, across 5 seasons.
 Two safety checks earned their place. A collision check (two files planned onto one
 destination) caught a real misidentification: the featurette `Featurettes/Season 4/Campaign
 Ads.mkv` matched the episode `S04E12 - Campaign Ad`, and would have been silently renamed as
 that episode had the real one been absent — bonus material is now excluded by folder and
 never title-matched. A before/after integrity snapshot then proved the rename safe: 2227
 video files before and after, no size or byte-total change on any show, no episode
 reassigned, and 76 indexed episodes gained (X-Men's). 76 folders left empty by the moves were
 removed afterwards.
 Covered by scratchpad/test_episode_match.py (35 assertions, pure logic).

-Per-episode playback progress (second pass of the TV work): `playback_positions` needs an
 episode key, then resume, watched marks and next-episode progression.

## Known bugs (bugs identified that need to be worked on)

-[FIXED] A Chernobyl episode was filed, renamed and played as Parks and Recreation S01E01.
 The file was sitting in the Parks folder — a misplacement that predates any of this work —
 and because its name carried `S01E01`, `scan_local_episodes` indexed it as Parks S01E01 with
 no check that the name in front of the marker was even the right show. At 710.5 MB against
 the real Pilot's 692.9 MB it also won largest-wins, so it became the episode, and the rename
 then wrote "Parks and Recreation - S01E01 - Pilot.mkv" over it and hid the evidence.
 Identified by ffprobe: 58.9 min, container title "Chernobyl S01E01 [1:23:45] ~ RAGA", Hindi
 and English audio. Chernobyl's own folder holds all five episodes and its E01 is byte-identical
 to the stray, so nothing was lost; the stray was moved to a dot-prefixed `.misplaced` folder
 (skipped by the scan) under a name carrying no marker, so no show can re-adopt it. The real
 Pilot was untouched and is back at `Season 01\Parks and Recreation - S01E01 - Pilot.mkv`.
 Fix: `names_other_show` in `episode_match.py`. A marker is no longer trusted when the text
 before it names a different show — refused files are reported as unmatched, so they stay
 visible and playable without being counted, played or renamed as an episode they are not.
 Only an outright disagreement counts: releases abbreviate ("Parks.and.Rec"), so one shared
 significant word accepts, and a bare "S01E01.mkv" with no prefix accepts too. It applies only
 to files that carry a marker — judging a title-only file this way would have refused every
 file the title matcher exists to identify.
 Audited the whole library against it: 1925 marked files, 0 refused — no other contamination,
 and no false positives. Also audited all 1671 episodes' running time against their TMDB
 runtime; the only mismatches were legitimate two-parter files and the two ordering cases below.
 Covered by scratchpad/test_intruder_guard.py (24 assertions, including the reported case
 end-to-end with the stray deliberately the larger file).

-[FIXED] The episode numbering is now detected from the files rather than assumed. The
 numbering a release uses is a property of the download, not of the show — the same "S01E01"
 means different episodes in different releases — so it cannot be a setting. `episode_orderings`
 reads every ordering TMDB publishes for a show (DVD, Digital, Production, Absolute, Story arc)
 into the same shape as the default air order, and `_resolve_ordering` scores each on how many
 files its runtimes can explain, adopting one only when it leaves nothing contradicted *and*
 the default could not. Running time is the evidence because it is the one thing a filename
 cannot lie about. Runtimes are read once per file and reused across candidates, and a file
 covering a range is scored against the sum of its episodes so a two-parter is not read as
 double-length. Where several orderings fit equally — Firefly's DVD and "intended" orders are
 the same sequence — DVD wins, so the result is the same on every run.
 Three signals, since runtime alone was not enough. The strongest is the episode title many
 rips write into the container, which no filename can distort: Batman's files claim
 "S01E01 - The Cat and the Claw" while the container says "On Leather Wings", exactly what the
 DVD ordering puts there. It counts only *for* an ordering, never against one — most containers
 hold a disc label ("WENTWORTH Series 2 Disc 2"), a rip artefact ("S01D01title_t02") or a
 release name instead of a title, and a first attempt that read those as disagreement condemned
 correct orderings and proposed 545 needless renames. A title matching nothing is simply silent.
 Coverage is next — whether the ordering even has the episode a file claims — and running time
 is the only signal trusted to contradict. Settling for the default also requires more than the
 absence of contradiction: where the files name their own episodes and the default matches none
 of them, alternatives are considered anyway, since a straight permutation has every slot
 present and every runtime plausible with each title one place out.
 Confirmed on the real library: Firefly detected as **DVD Order** on coverage (14 of 14 slots
 against the default's 11), independently reproducing the names applied by hand; Batman detected
 as **DVD Order** on 79 files whose embedded titles all agree, correcting 90 filenames that were
 shifted by one. Every other show keeps the default, and a second run plans 0 renames.
 Verified after applying: all 85 Batman episodes match the title inside the file, 78 exactly and
 7 differing only in house style ("The Under-Dwellers" v "The Underdwellers", "House and Garden"
 v "House & Garden"), where TMDB's spelling is kept.
 Limitation: an ordering can still only be told apart where one of the three signals speaks.
 X-Men remains the hard shape — same episode count, interchangeable runtimes, no container
 titles — and is handled instead by matching filename titles against the episode list.
 Covered by scratchpad/test_ordering.py (19 assertions, TMDB stubbed), including the junk
 container titles that caused the 545-rename misfire.

-[NOTED] Batman's Season 04 is The New Batman Adventures, a different series, which is why no
 Batman ordering covers it and its 24 files carry numbers without titles. Their containers name
 them correctly ("Holiday Knights", "Mad Love"), so they could be titled from the embedded
 metadata or matched against TNBA's own TMDB entry, whichever suits how you want the show
 grouped. Left alone rather than guessed at.

-[FIXED] A file filed under Featurettes is no longer dragged back among the episodes. A marker
 in the name was enough to plan it as an episode, so Dollhouse's unaired pilot and Epitaph One —
 deliberately kept out of the run — were planned back into `Season 01` with their titles
 stripped. Placement in an extras folder is a decision, and the rename now respects it.

-[FIXED] Firefly and Parks and Recreation S06 were given the wrong episode titles by the TV
 rename, because their local numbering is not TMDB's. TMDB lists Firefly in broadcast order —
 11 episodes, the double-length "Serenity" last at E11 — while the local rip holds 14 with the
 87-minute pilot first, so TMDB's E01 title "The Train Job" was written onto Serenity and every
 title after it was shifted by one. Parks S06 is the same shape: TMDB merges the double finale
 into 20 entries, the local rip numbers 22, so "Moving Up" landed on a 21.6-minute file.
 Neither is a bug in the matcher — it was handed numbers from a different scheme and had no way
 to know. Fix: `_mismatched_seasons` withholds titles for a season on two signals together.
 A local season running past the end of TMDB's is only a suspicion, since a show may simply
 hold episodes TMDB never listed — Dollhouse (13 v 12) and Batman: The Animated Series both do,
 with every title correct. What confirms it is a file whose running time cannot be the episode
 it claims, measured against the sum of its episodes so a legitimate two-parter is not caught.
 The count test alone flagged 4 shows and would have stripped 79 correct titles; both together
 flag exactly the 2 seasons that are wrong, 30 files. Those files now carry numbers and no
 title, which is the honest state — a bare "Firefly - S01E04.mkv" beats a confidently wrong
 title. Restoring the titles needs per-show episode ordering; see Notes.

-[FIXED] The TV rename moved episodes but left their subtitles and featurettes behind, so a
 show ended up with both "Season 1" and "Season 01". All 111 subtitles in the library were
 orphaned from their episodes, which silently broke subtitles for 111 episodes — the pack
 splitter already carried sidecars and I failed to carry that into the TV script.
 `_plan_tv_naming.py` now plans three groups in one pass: episodes, subtitle sidecars (moved
 beside their episode and renamed to match, language suffixes preserved), and files stranded
 in a superseded season folder. Bonus videos go to `Season NN/Featurettes/`, per-episode
 sidecars like .sfk waveform caches go directly into `Season NN/` beside the episode, and the
 grouping below the season folder is kept — flattening to the basename collided, since
 Wentworth has a "Kate A.mkv" under more than one season's "The Cast's Favourite Scenes".
 The collision check now covers all three groups, not just episodes.
 Applied: 111 subtitles + 503 bonus/sidecar files relocated, 86 emptied folders removed.
 Verified byte-for-byte: 2632 files and 2650.7 GiB before and after, zero discrepancies, and
 a Game of Thrones episode now offers its subtitle track where it offered none.
 Duplicate season folders went from 23 to 3, the remaining three holding duplicate episode
 copies left for a decision — see below.

-[DECISION NEEDED] Three duplicate episode copies, and what they reveal about "largest wins".
 `_best_local_video_path` picks the largest file per episode, and on all three duplicates that
 choice looks wrong or arbitrary:
   Angel S01E16 — the two files are identical (1540.4 MB, 1920x1080, h264, 42.8 min). Either
   can go.
   Dollhouse S01E12 — kept 1760 MB h264 with no subtitles over a 1330 MB HEVC copy of the
   same duration that *has* a subtitle track. The rejected file is arguably the better one;
   largest-wins simply favours the less efficient codec.
   Parks and Recreation S01E01 — RESOLVED, and it was not a duplicate at all: the 58.9 min
   file was an episode of Chernobyl misfiled in the Parks folder. See the fixed entry above.
   It is the clearest evidence for the change, though — largest-wins picked a file from a
   different show over the correct Pilot, and only duration exposed it.
 So the heuristic needs more than size: resolution, and a duration sanity check against the
 TMDB runtime, would have picked correctly in all three. Nothing has been deleted.


-[FIXED] The download progress ring stayed frozen on the previous title's percentage after
 switching to another title. `openHero` reset the download UI *inside* the downloading branch
 only, so opening a title that is not downloading took the `else` branch and left the ring on
 screen, still showing the last percentage, with a poll timer queued behind it. Clicking a
 card calls `openHero` directly rather than `closeHero`, which is where the reset already
 lived — hence the gap. `_stopDlPoll()` and `_hideDlProgress()` now run unconditionally at the
 top of that block, before the branch and before the new poll timer is armed, so the ordering
 cannot cancel the timer it just set. The duplicate calls inside the downloading branch were
 removed.
 The ring only ever froze, it never showed wrong live data: a late poll for an idle title gets
 409 `download_not_active` and bails on `!response.ok`.
 Covered by scratchpad/test_hero_switch_reset.js (13 assertions, including the call ordering
 that makes the fix work).

-[FIXED] Downloads in progress showed the Upgrade check bar. `shouldShowUpgrade` tested
 `!item.file_missing` but not whether a download was running — and `file_missing` is
 deliberately suppressed for an active download, so a title with no file yet had
 `current_quality = null`, which ranks below the preferred quality and lit the card up as
 "↑ Check". All 13 of the library's current downloads were in exactly that state.
 Not only cosmetic: clicking it ran a quality check, and on a hit the flow opens the torrent
 modal, so it could have started a second download for a title already downloading.
 The three renderers had drifted apart — the client-side card badge already excluded
 downloading items, while the server-rendered badge and the hero card did not, despite the
 Runbook's own precedence rule that "downloading overrides" the other badges. All three now
 agree. Covered by scratchpad/test_upgrade_during_download.py (9 assertions, rendering two
 genuinely-upgradeable films where the only difference is that one is mid-download).

-[FIXED, DATA LOSS] Finishing any download against a TV show recycled the entire show
 folder. Reachable today via Retry / Find Torrent on a TV hero: a show's `path` is a folder
 of episodes, and the movie finalisation treats `path` as "the file this download replaces",
 so `retire_target = previous_path if os.path.isdir(previous_path)` sent the whole show to
 the Recycle Bin. Reproduced with a 20-episode sandbox show: all 20 files gone.
 Fixes: TV now has its own `_finalize_tv_episode()`, which files the download as
 `Season NN/<Show> - SxxExx.<ext>`, refuses a download that is not better quality than the
 episode it would replace, and retires only that one episode's file — never the folder, and
 never a file that also covers other episodes (retiring an `S04E01-E02` file because E02
 was upgraded would take E01 with it). A download with no SxxExx is left where it is.
 Also a general guard for movies: when the item's folder holds more than one video, which
 file belongs to the item is a guess (`_best_local_video_path` picks the largest), so
 nothing is retired at all. The first version of this guard still recycled the wrong file
 in that case — a test caught it. Covered by scratchpad/test_tv_finalize.py (20 assertions).

-[FIXED] Multi-episode files were reported as missing episodes. A two-part episode that
 aired as one file, e.g. `Prison Break (2005) - S04E01-E02 - Scylla & Breaking and
 Entering`, was indexed only under E01, so E02 looked absent. `_episodes_covered()` now
 expands a range across every episode it claims. The second number must carry its own `E`
 or be joined by a bare hyphen, so `S01E01 - 1984` and `S01E01.2160p` cannot be read as
 ranges, and a span is capped at 8 episodes. Six files library-wide use this form; false
 "missing" reports for Prison Break, Mr. Robot and Parks and Recreation are all gone.

-[DECIDED] The "Downloading" chip stays. It is the at-a-glance state marker while the ring
 beside the action buttons carries the progress, and it has since been given a job of its
 own: clicking it filters the library to everything downloading. See the entry under Next
 Features.

-[FIXED] Three separate download indicators on the hero card, one of them invisible.
 The progress bar was `width: 100%` inside `.hero-library-actions`, a wrapping flex row,
 so it always broke onto its own line and ended up behind the info cards below. Replaced
 with an inline 44px SVG progress ring in the same cyan, sitting beside Retry / Find
 Torrent with the rounded percentage in its centre; the download state and ETA moved into
 its tooltip. The redundant "Downloading…" / "Waiting for torrent client…" text next to
 the buttons is gone — `hero-library-status` is now only used for completion and errors.
 Ring geometry is asserted against 2πr in the tests so the CSS dasharray, the JS constant
 and the SVG radius cannot drift apart. Covered by scratchpad/test_dl_ring.js
 (16 assertions, including clamping of >100%, negatives and null).

-[FIXED] IP man titles still showing upgrade option. Root cause: `quality_checks.found`
 is a boolean frozen at search time and never invalidated, so it kept offering a 1080p
 release for a file that is now 2160p. The UI trusted that flag instead of comparing
 qualities. Now `_upgrade_available()` re-compares stored result vs current quality at
 render time (self-correcting however stale the check), and a completed upgrade clears
 the item's quality_checks. 6 items were affected; 4 correctly suppressed.

-[FIXED] Missing genres. Mostly a display problem: 26 of the 27 "missing" titles are
 genuinely single-genre on TMDB, and the hero was labelling the empty chip
 "Genre 2 Missing". The second chip is now hidden when there is no second genre.
 Three real defects sat behind the one genuine case (Alien³).
 (a) `backfill_genres()` treated a missing *second* genre as incomplete, so single-genre
 titles would be re-queried against TMDB forever — which is why the one-shot
 `genres_backfilled_v1` flag existed. Only a missing first genre now counts.
 (b) That flag was set even when items failed, permanently stranding them. Removed; the
 backfill is now driven by whether any item actually lacks a first genre, so it settles
 at zero candidates on its own.
 (c) `_fetch_best_metadata()` ignored the stored TMDB id and resolved via imdb_id then a
 fuzzy title search. Alien³ had a wrong imdb_id (`tt36842917`), so the fallback matched
 "The Alien Hand 3" — a different film. It now prefers the stored TMDB id, which is
 exact. That is the same failure mode that produced the phantom Lara Croft entry, so it
 is fixed for every title, not just this one.
 Alien³ now has Science Fiction/Action and its imdb_id was corrected to tt0103644.

-[ENVIRONMENTAL] Add to library broken from Discover. Root cause: not the plugin - all
 three kickasstorrents mirrors were being intercepted by the ISP and redirected to
 assets.virginmedia.com/site-blocked.html, failing the TLS handshake. nova2.py exits 0
 even then, so the app saw "success, zero results". Search works again when not blocked
 (confirmed 128 results). Needs a VPN when the block is active.

-[FIXED] Upgrade check presented no error. Root cause: `_run_search` only raised when
 returncode != 0 AND stdout was empty; nova2 exits 0 on total connection failure and
 writes to stderr, which was discarded — so a dead engine was indistinguishable from
 "no results". Added `SearchEngineError` + `_describe_failure()` which maps stderr to a
 cause (ISP block / DNS / timeout). Routes return 503 `search_unavailable`, the UI shows
 it, and a failed check is no longer recorded as a result. `/check-all` now stops on
 engine failure instead of retrying against a dead mirror, and reports partial counts.

-[FIXED] Bulk actions gave no sign they were running. They are plain form posts that
 block until done, so the page just sat there. Each bulk form now carries a
 `data-bulk-label`, and on submit an in-progress bar with a spinner appears immediately;
 the page reload that follows carries the completion status and replaces it, which is
 the existing override behaviour rather than a new mechanism. All bulk buttons disable
 while one runs so a second cannot be queued mid-run. Disabling is deferred one tick —
 disabling a submit button synchronously inside its own submit handler can cancel the
 submission in some browsers. Covered by scratchpad/test_bulk_progress.js, which runs
 the shipped code against a DOM stub (13 assertions).
 Note: `/check-all` and `/sync-trakt` are not reachable from the UI, so they were left
 alone — worth deciding whether to expose or remove them.

-[FIXED] Titles added from Discover disappeared from Movies after an app restart and
 reappeared under Discover > Incomplete collections, despite qBittorrent still actively
 downloading them. Root cause: `_qbt_webui_torrents_info()` returned `[]` both when
 qBittorrent had no torrents and when it could not be reached, so a restart while
 qBittorrent was closed looked like "every download vanished". Any state older than the
 12h staleness window was then cleared, and because the library view lists items by
 local file or active download, a path-less title silently dropped out of Movies — and
 the same "owned" test in `list_collection_items` put it back into Discover as a gap.
 Same failure shape as the search bug: a failure indistinguishable from an empty result.
 Fixes: `QbtUnavailableError` is now raised on an unreachable qBittorrent and
 reconciliation is skipped entirely rather than clearing anything;
 `_readopt_orphaned_downloads()` rebuilds a lost link from a still-running torrent
 (requiring a year match so a sequel cannot be adopted by mistake); and the Settings
 "Test connection" button no longer reports a failed connection as "reachable, 0
 torrents". The Terminator and Cradle of Life were relinked and are downloading again.

## Notes (ideas not yet planned)

-Tidy up the settings page, move settings into relevant sections such as network, preferences, storage - exact categories still to be confirmed
-Why are completed tasks not being moved from the roadmap to the changelog?
-Move favourites icon
-Search entire repo for dead code and useless files
-Fix subtitles in player
-Ongoing play issues for transcoding, quality isn't great, stream freezes, high CPU usage - full diagnosis and troubleshooting required
-Carry the detected episode ordering into the app. The rename tool now works out which ordering
 a show's files use (see the fixed entry above), but the episode list still asks TMDB for the
 default, so Firefly's browser shows broadcast-order titles and synopses beside DVD-order files
 even though the filenames are right. Detection is too slow to repeat per request — it reads
 every file's running time — so the answer is to persist what the tool found (media_id →
 episode group id) and have `/api/tv/<id>/season/<n>` read the ordering from there, falling back
 to the default. Worth an override in the show's Settings card for the cases detection cannot
 call, such as two orderings with the same episode count and interchangeable runtimes.
-[ANSWERED] Why did public access resolve to a 10. address? That was PIA's VPN tunnel, not
 the LAN. The original `_lan_ip_address()` found the address by probing the route to the
 internet, and a VPN captures that route, so it reported the tunnel address (10.195.133.245)
 which no device on the network can reach. Already fixed while chasing "cannot connect from
 another device": `_lan_ip_addresses()` enumerates every private IPv4, discards loopback and
 link-local, and orders 192.168 → 172.16 → 10.x so the real LAN address comes first; Settings
 lists them all and warns that a VPN adapter shows up there too. It reads 192.168.0.251 now
 both because of that fix and because the VPN is currently down — its adapter holds
 169.254.28.64, a link-local address, which is filtered out. Reconnecting PIA will make
 10.195.x reappear second in the list, which is correct. Note the server binds 0.0.0.0 and so
 listens on every interface regardless; the address only decides which URL to type. 10. which doesn't work when tyring to access from another device on my LAN. Or is 10. strictly
