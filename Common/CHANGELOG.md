# Changelog

All notable user-facing changes to this project are documented here.

## v0.1.5 - 2026-08-02

- The Settings page is now grouped into five collapsible sections — Library,
  Network & Access, Downloads, Integrations, Discover — instead of ten panels
  stacked flat in one long scroll. Each section remembers whether you left it
  collapsed or open, per browser. No field moved or changed name; this only
  changes how they're grouped and shown.

## v0.1.4 - 2026-08-02

- Added watch-history import, in Settings. Upload a JSON or CSV export and
  it's read into its own database, separate from the library — Trakt,
  Letterboxd and IMDb exports are recognised automatically, and a file from
  somewhere else is read on a best effort if it has title/year/date columns
  of its own. Safe to import the same file more than once; only genuinely new
  records are added.

## v0.1.3 - 2026-08-01

- Picking a search result in Discover now opens the hero and pulls a torrent,
  the same as every other Discover surface (watchlist, trending, collections).
  It used to show a standalone form asking you to type in a quality and,
  optionally, a local path — a leftover from before the hero-based flow
  existed, still wired to a title's search results specifically.

- Fixed an ffprobe call that could throw a decoding error on Windows for a
  file whose metadata was not valid Windows-1252 — found live while checking
  episode-ordering detection against the real library, harmless there but
  worth closing.

- A TV show is no longer offered a quality check that goes nowhere. The
  "↑ Check" upgrade card searches for one release by title and year, which is
  a film's shape — a show has no one file or one quality — so checking it on
  a show could report a release "found" and then open nothing, since the
  torrent pane that finding one leads to only ever knew how to open for a
  film. The check is no longer offered on TV at all.

- "Find missing" on a show can now search for a whole season at once instead
  of one release per episode — a "Find season" button appears next to "Hide
  season" whenever more than one episode is missing. Downloading a season
  pack now files every episode it contains rather than only the largest file
  in it, which used to leave the rest sitting unfiled in the download folder
  with nothing to say they were there.

- Trakt no longer disconnects itself on a bad moment. Refreshing an expiring
  token used to clear the whole connection on any failure at all — a network
  blip, a Trakt outage, a rate limit — not just a token Trakt itself rejects,
  so a single failed refresh near expiry could silently turn "your watchlist"
  into "connect Trakt" with nothing to explain why. Only a refresh Trakt
  explicitly rejects clears the connection now; anything else is treated as
  try again later. Checked against this install directly: Trakt has never
  actually been connected here — no token has been stored at all — so the
  watchlist showing "Connect Trakt" is correct as things stand, not this bug;
  Settings → Connect Trakt is what starts it.
- The watchlist also now says when it failed to load, rather than showing
  "your watchlist is empty" for that too — a genuinely empty watchlist and a
  failed request looked identical before.

- Fixed two causes of subtitles silently not appearing. A subtitle track
  stored on disc as an image rather than text (common on DVD-sourced rips —
  found on X-Men: The Animated Series, which offered six caption tracks and
  always failed on three of them) was offered in the menu and always failed
  to load with nothing shown; those are no longer offered, since nothing can
  convert an image to captions. Separately, two episodes of the same show
  could read each other's caption files if one was requested shortly after
  the other, because captions were tracked per show rather than per episode.
  Verified against a real file with the picture-based tracks the first fix
  removes. If subtitles are still missing for a specific title, the codec
  the container actually uses is the next thing to check.

- The episode list now uses the numbering a show's files actually follow,
  not always TMDB's default. Batman: The Animated Series and Firefly are
  numbered on disk in DVD order; the episode list was still asking TMDB for
  broadcast order, so titles, synopses and "up next" pointed at the wrong
  episode from S01E02 onward on Batman, and put Firefly's 87-minute pilot in
  the wrong slot entirely. Detection now runs once per show in the background
  after startup and its answer is kept, rather than being repeated on every
  request. Where no published ordering fits the files, episode titles are
  withheld rather than shown wrong, exactly as the offline rename tool already
  did — this and that tool now share the same detection code.

- A show's episodes now remember where you stopped watching each one
  separately, instead of one shared position for the whole show — resuming
  S01E02 no longer opened at wherever S01E01 was left off. An episode watched
  to the end is marked watched with a checkmark on its thumbnail, a partly
  watched one shows a progress bar, and the player now offers to play the next
  episode in the last 30 seconds, or moves on to it automatically once the
  current one ends.

- The "Back to Shows" button no longer scrolls out of reach on a long episode
  list. It has moved from the top of the list into the hero, beside the season
  picker, where it stays on screen.
- A download holding several films no longer files the wrong one. It was picking
  the largest video in the torrent, so a five-film Predator pack put Predator 2
  into the library as "Predator (1987)" — renamed on the way in, so nothing
  afterwards showed which film it really was. The film whose own title and year
  match is the one taken now, and where no single film matches, the download is
  flagged for review and left completely untouched.
- Every film in a pack is now added to the library, not just the one you asked
  for. Downloading a five-film Predator pack for Predator (1987) also files
  Predator 2, Predators, The Predator and Prey, each in its own correctly named
  folder. A film that cannot be identified confidently is left in the download
  folder rather than filed under a guess, and one you already hold is skipped.
- A show that holds the same episode twice now says so, with a chip on its hero
  reading "2 duplicated". Until now the app quietly kept the larger copy and
  never mentioned the other, so a library could hold 78 duplicated episodes
  across two shows without a word — which is exactly what this one did. The chip
  is a flag and nothing more: it offers no action and deletes nothing, because
  which copy to keep depends on codec, subtitles, disc space and whether you
  want an upscale over its source, and getting it wrong cannot be undone.
- Fixed the startup scan that fills in a missing quality. It had been raising on
  its very first item and the error was caught and logged as a warning, so a
  title imported before its download finished kept a blank quality until it was
  refreshed by hand. Nothing showed that anything had failed.

## v0.1.2 - 2026-07-25

- The pages now load their JavaScript from a cached file instead of carrying it
  inline, so a second visit does not re-download it. No change to how anything
  behaves.
- A title's quality is now read from its main video rather than whichever file
  came first alphabetically. A folder shipping a small "sample" file alongside
  the feature could record the whole title at the sample's quality, which then
  offered an upgrade and could start a download to replace a file that was fine.
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
