# Changelog

All notable user-facing changes to this project are documented here.

## v0.1.2 - 2026-07-25

- The pages now load their JavaScript from a cached file instead of carrying it
  inline, so a second visit does not re-download it. No change to how anything
  behaves.
- A download whose filename is plainly a different show is no longer filed as an
  episode of the show you started it from. It is flagged for review and left
  where it is, with the filename in the message. Filing renames the file, so the
  evidence of what it really was would have been gone a moment later — which is
  how a Chernobyl episode once became Parks and Recreation S01E01.
- Fixed a sign-in redirect that could send you to another website. A crafted
  link could set the "return to" address to something that looked relative but
  was not, so signing in would hand you to a site you did not choose. Return
  addresses are now checked properly. Only reachable by following a crafted link.
- Pages now carry security headers, including a content policy that stops the
  app being embedded in another site's page.
- Fixed a security flaw that let a request read video files from anywhere on the
  machine. The direct-stream playback address blocked "../" but not a full path,
  so any .mp4, .m3u8 or .m4s outside the library could be fetched through it.
  Requests are now confined to the transcode cache. Sign-in already blocked this
  from other devices whenever public access was on, so exposure was limited to
  the machine itself and to anyone on your network while public access was off.
- Fixed a file from one show being played and renamed as an episode of another. A
  Chernobyl episode misfiled in the Parks and Recreation folder was indexed as its
  S01E01 — its filename carried "S01E01" and nothing checked which show the name
  in front of it belonged to — and being the larger file it replaced the real
  Pilot. An episode number is no longer trusted when the filename names a
  different show; such a file stays listed and playable but is never counted,
  played or renamed as an episode. The real Pilot is back and the misfiled copy
  has been set aside. No other title in the library was affected.
- Episode naming now works out which numbering a download uses instead of
  assuming TMDB's. A release can be numbered in DVD, digital, production or
  absolute order, and the same "S01E01" then means a different episode — Firefly
  ships with its double-length pilot first where TMDB lists it last, so every
  episode had been given the title of the one after it. Each ordering the show
  was released in is now checked against how long the files actually run, and the
  one that fits is used — going on the episode title written inside the file
  where there is one, how many of the claimed episodes the ordering actually has,
  and how long the files run. Where none fits, as with Parks and Recreation
  series 6, episodes keep their number and show no title rather than the wrong
  one. This corrected all 90 Batman: The Animated Series filenames, which were
  each one episode out.
- Files kept in a Featurettes folder are no longer moved back in among the
  episodes because their name happens to carry an episode number.
- Fixed titles repeatedly showing as missing when a same-named but empty folder
  sat alongside the real one. The library scan no longer treats a folder with no
  video file as importable, and a scan can no longer repoint an item that
  already has a playable file.
- Quality upgrades now replace the file they were meant to replace. A completed
  upgrade is renamed to the library naming convention, moved into place, and the
  superseded file is sent to the Recycle Bin.
- Upgrades only finalise once qBittorrent reports the download fully written to
  disk, and are rejected if the new file is not actually better quality.
- Added a Downloads staging folder setting so in-progress downloads are never
  visible to the library scan.
- Canonical names are taken from the stored TMDB title and year, so a misnamed
  release can no longer rename a library entry.
- Collection packs holding several films in one folder are now imported as
  separate titles instead of being skipped entirely. A folder counts as a pack
  only when two or more feature-sized videos each carry their own year, so a
  film shipped with bonus features is still imported as one title.
- Titles are now parsed up to the release year, so release metadata no longer
  leaks into the search query. Numerals that belong to the title are kept
  ("Blade Runner 2049").
- Films imported from a collection pack can be split into their own canonical
  folders, so every film becomes a standalone library title.
- The upgrade badge no longer sticks after a title has been upgraded. It is now
  worked out by comparing the last search result against the file's current
  quality, so an old result cannot advertise a lower-quality release.
- A torrent search that cannot reach its mirrors now reports "search engine
  unreachable" instead of looking like a title with no available releases, and
  a failed check is no longer stored as though the title had been checked.
- Titles with a single genre no longer show a "Genre 2 Missing" chip — the
  second chip is simply hidden, as most single-genre titles are correct.
- Metadata lookups now use the stored TMDB id when there is one, so a title
  with a bad IMDb ID can no longer be re-matched onto a different film.
- Added a Public access section in Settings to control whether the app is
  reachable from other devices, and on which port. Previously this was hardcoded
  to listen on all interfaces. Turning it on disables the Flask debugger, which
  would otherwise expose a remote code execution console.
- TV episodes now follow a single naming convention,
  "Show/Season NN/Show - SxxExx - Episode Title.ext". Files that only carried an
  episode title are identified against TMDB, so a show like X-Men The Animated
  Series — where none of the 152 files could be read as episodes — now browses by
  season and episode like everything else. Bonus material is left alone.
- Subtitles now travel with their episode when the library is renamed, and sit
  beside it under a matching name. Featurettes and other per-episode files are
  gathered into the right season folder too, so a show no longer ends up with both
  a "Season 1" and a "Season 01".
- The download progress ring no longer stays on screen, frozen at the last
  percentage, after you click through to a title that is not downloading.
- A title that is downloading no longer offers a quality upgrade. It had no file
  yet, so it counted as below your preferred quality and invited you to look for a
  better copy — which could have started a second download of the same title.
- "View metadata" is now a Settings gear on the hero card, and alongside the
  metadata it offers the alternative artwork TMDB holds for a title. Pick a poster
  in any language — or a version with no text on it — and it replaces the current
  one straight away.
- The "Downloading" chip on the hero card is now clickable, filtering the library
  to everything currently downloading.
- Discover now has a "New & Missing Episodes" block, listing shows that are
  missing episodes which have already aired, grouped by season with an Airing or
  Ended badge and the next air date. Clicking an episode searches for it and
  offers the usual torrent choices. Seasons or whole shows can be hidden.
- A TV show hero card now offers its own missing episodes next to Watch: "Find 10
  missing" when there are gaps, or "Check for new content" for a show still airing
  with nothing outstanding. Shows still on the air also carry an "Airing" chip by
  the genres. A show you have in full shows neither.
- A torrent search that genuinely found nothing is no longer reported as
  "search engine unreachable".
- Fixed a data-loss bug: finishing a download against a TV show recycled the
  whole show folder. A finished TV download is now filed as a single episode, and
  only the one episode file it replaces is ever retired.
- A two-part episode held in one file (e.g. `S04E01-E02`) no longer reports its
  later episodes as missing.
- TV shows can now be browsed by season and episode. A season dropdown next to
  Watch opens that season's episode list — with thumbnails, titles, air dates,
  runtimes and synopses from TMDB — and each episode has its own Watch button.
  The show's hero card stays in place above the list, and "Back to Shows" returns
  to the poster grid.
- Featurettes and extras are listed below the episodes of the season whose folder
  holds them, so bonus material is playable instead of hidden without cluttering
  other seasons. Names are cleaned up from their release filenames, and extras
  that belong to no particular season appear under an Extras entry.
- Watch on a TV show previously played whichever episode had the largest file
  rather than one you chose. Playback now targets a specific episode, with its
  own transcode cache and subtitles.
- Genre chips on the hero card are now clickable and filter the library to that
  genre, with a pill in the toolbar to clear it.
- The Watchlist, Incomplete Collections and Trending blocks on Discover can be
  collapsed, and remember whether they were left open.
- Download progress on the hero card is now a compact ring beside the action
  buttons, showing the percentage in its centre, with the download state and ETA
  in its tooltip. It replaces a full-width bar that was drawn behind the info
  cards and could not be seen, along with a duplicate "Downloading…" message.
- Downloads are no longer forgotten when qBittorrent is closed or unreachable. A
  restart during an outage used to clear the download state, which made titles
  with no file yet disappear from the library and show up again as gaps under
  Discover. An item that loses its link is now relinked automatically from the
  running torrent.
- Added sign-in for public access. A username and password must be set before
  the app can be exposed to the network, and every page, API call and video
  stream then requires a session. Passwords are stored hashed, sessions last 30
  days with "stay signed in", and repeated failed attempts lock out for five
  minutes. Local-only use needs no sign-in.
- Bulk actions now show an in-progress bar with a spinner as soon as they start,
  replaced by the completion message when they finish. The buttons disable while
  one is running so a second action cannot be started mid-run.

## v0.1.1 - 2026-05-16

- Added automatic library refresh on load
- Custom video player with full playback controls, keyboard shortcuts, and subtitle support.
- Three-tier playback strategy: direct play, direct stream, and HLS transcode.
- Items with a missing file now show a badge and offer a re-download action.
- Library refresh now auto-detects quality for newly discovered files.
- Genre metadata from TMDB is now shown on the hero card.
- Added movie collections support.
- Added subtitle fetching.
- Added metadata error handling, search for missing metadata, scan quality, fetch subtitles from the movie settings.

## v0.1.0 - 2026-04-12

- Initial public release.
