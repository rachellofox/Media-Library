# Runbook

This document defines repeatable operational procedures for Media-Library. For initial setup, prerequisites, and first run, see [README.md](../README.md).

## Validation Checklist

- App starts without traceback.
- Home page loads.
- Search page loads.
- Add-title workflow works with a known IMDb ID.
- Quality-check workflow returns expected status.

## Public Access

Settings → Public access controls the bind address and port. Host and port are
only read when `app.run()` is called, so **a change needs an app restart**; the
settings page says so while the saved values differ from the running ones.

- Off (default): binds `127.0.0.1` — this computer only.
- On: binds `0.0.0.0` — reachable at `http://<lan-ip>:<port>`.

Public access forces Flask's debugger off. The Werkzeug debugger exposes an
interactive console that can execute arbitrary code, so it must never be
reachable from another machine. Debug stays on for loopback-only runs.

### Sign-in

A username and password must be set before public access can be turned on — the
settings form refuses the change otherwise, since there is no safe way to expose
the library without credentials.

- Sign-in is enforced **only while public access is on**. A loopback-only server
  is already limited to whoever is at this machine, so local use stays open.
- Passwords are stored as a `werkzeug.security` hash, never in plain text. Leave
  the password fields blank to keep the current password when changing username.
- Sessions are signed with a key kept in the OS keyring
  (`MediaLibrary/session_secret_key`) so they survive restarts. Deleting that
  entry signs everyone out.
- "Stay signed in" lasts 30 days; without it the session ends with the browser.
- Five failed attempts from one address triggers a five-minute lockout, applied
  even to the correct password.
- Changing the username invalidates existing sessions.

Enabling public access signs you out immediately — the next request is gated, so
sign in with the credentials you just set.

Anyone who signs in has full control: browsing, streaming, settings and
downloads. Only expose this on a network you trust, and do not port-forward it.

## Library Naming Convention

Canonical names come from the stored TMDB title and year — never from a torrent
or release name, so a misnamed release cannot rename a library entry.

- Movies: `<Title> (<Year>)/<Title> (<Year>).<ext>`
- TV: `<Title>/<Title>.<ext>` (series are not year-stamped)
- Characters Windows forbids are mapped in `ILLEGAL_CHAR_MAP` in `naming.py`.
  Colons are dropped: `Alien: Covenant` becomes `Alien Covenant`.

New and upgraded files are named this way automatically. Existing folders are
left alone; to preview aligning them run
`python scripts/_plan_canonical_names.py` (read-only) and re-run with `--apply`
to perform the renames.

## TV Episodes

An episode is identified **only** by an `SxxExx` marker in its filename. The
containing folder is ignored, because files do turn up under the wrong season.
Files without a marker are not given episode numbers — most are featurettes, and
guessing from folder order would invent episodes that do not exist.

One file can cover several episodes (`S04E01-E02` for a two-parter that aired as
one), and is indexed under every episode it claims. The second number must carry
its own `E` or be joined by a bare hyphen, so `S01E01 - 1984` is not a range.

### Naming

Canonical layout is `<Show>/Season NN/<Show> - SxxExx - Episode Title.<ext>`.
`scripts/_plan_tv_naming.py` brings a library to it, dry-run by default:

```text
python scripts/_plan_tv_naming.py                 # preview everything
python scripts/_plan_tv_naming.py --show "Alias"  # preview one show
python scripts/_plan_tv_naming.py --apply         # rename
```

Quality tags are deliberately dropped from filenames — quality is read from the
file with ffprobe, so the name does not need to carry it.

Files with no `SxxExx` are identified against TMDB by title (`episode_match.py`),
refusing anything ambiguous rather than guessing. **Do not be tempted to trust a
sequential `EPnn` number**: X-Men The Animated Series is numbered EP01–EP76 in
production order while TMDB uses broadcast order, so that would mislabel 39 of its
76 episodes. Titles are the only reliable signal.

Bonus material is excluded by folder name and never title-matched — the Parks and
Recreation featurette "Campaign Ads" otherwise matches the episode "Campaign Ad"
closely enough to be renamed as it. The script also refuses any plan where two
files land on one destination, which is how that was found.

The script plans three groups in one pass, and all three matter:

1. **Episodes** — renamed to the canonical form.
2. **Subtitle sidecars** — moved beside their episode and renamed to match, so the
   player still finds them. Renaming episodes without their sidecars orphans every
   subtitle in the library; a language suffix like `.en` is carried over, but a
   trailing release-group token is not.
3. **Files stranded in a superseded season folder** — bonus videos into
   `Season NN/Featurettes/`, per-episode sidecars such as `.sfk` waveform caches
   directly into `Season NN/` beside the episode. Whatever grouping sits below the
   season folder is preserved: flattening to the basename collides, because
   Wentworth has a `Kate A.mkv` under more than one season's featurette folder.

Before applying a large rename, snapshot each episode's file size and compare
afterwards; that is what proved the 1811-file run had lost nothing. The script does
not remove folders left empty by its moves — do that separately.

### Duplicate copies are invisible to the planner

`scan_local_episodes` returns only the largest file per episode, so a second copy is
never planned and stays where it is. Worth knowing that **largest-wins is a poor
proxy for best**: it favoured an h264 file over a smaller HEVC copy that had
subtitles, and picked a 58.9 minute file over the correct 21.6 minute Pilot for a
22-minute show. Resolution plus a duration check against the TMDB runtime would
choose better.

## Artwork

The gear **Settings** card on the hero opens the metadata inspector plus an
**Artwork** section. Posters are fetched only when asked for — TMDB holds over a
hundred for some titles — and can be filtered by language, including "No text"
for the language-neutral artwork.

Only URLs on `https://image.tmdb.org/t/p/` are accepted (`TMDB_IMAGE_PREFIX`), so
a crafted request cannot make the server fetch an arbitrary URL. A chosen poster
is downloaded over the existing cached file, which keeps its name — so anything
displaying it needs a cache-busting query when it changes, or the browser shows
the old image.

## New & Missing Episodes

Discover lists owned shows missing episodes that have **already aired**. Episodes
with no air date, or one in the future, are never offered — a season part-way
through broadcast lists episodes that do not exist yet.

- Specials (season 0) are never reported.
- A show with no identifiable local episodes is skipped, not reported as entirely
  missing, since there is no gap that can be reasoned about.
- Seasons and whole shows can be hidden; `discover_ignored_tv` stores season `-1`
  for a whole show.
- Season lists and show status are cached in the database
  (`tmdb_season_cache`, `tmdb_tv_status_cache`) for `DISCOVER_COLLECTION_CACHE_HOURS`.
  A cold check of every show costs about a minute of TMDB requests; warm it is
  well under a second. The block is fetched separately from the rest of Discover
  so it cannot delay the other blocks.

Clicking an episode searches `Show SxxExx` and opens the normal torrent modal, so
adding an episode follows the same path as a film.

The same data is reachable per show from the hero, where the chip and the button
track **different** things:

- The **Airing** chip appears only while TMDB reports the show `in_production`.
- The button follows whether anything is missing, regardless of airing status:
  "Find N missing" when there are gaps, "Check for new content" for an airing show
  with none, and hidden for a show you have in full.

They must stay independent. Gating the button on "airing" hides gaps in ended
shows — a show can finish having never been completely downloaded.

`/api/tv/<id>/seasons` carries `in_production` and `missing_count`, so the hero
needs no extra request, and `/api/tv/<id>/missing` reuses the very function the
Discover block loops over.

## Finishing a TV Download

A show's `path` is a folder of episodes, not a single file, so TV never goes
through the movie replace-and-retire path — doing so recycled entire show folders.
`_finalize_tv_episode()` instead:

- files the download as `Season NN/<Show> - SxxExx.<ext>`,
- refuses it if it is not better quality than the episode already there,
- retires only that one episode's file, never the folder,
- never retires a file that also covers other episodes,
- leaves a download with no `SxxExx` exactly where it is.

For movies, when the item's folder holds more than one video, nothing is retired
at all: `_best_local_video_path` picks the largest file, so which one belongs to
the item is a guess, and acting on it could remove an unrelated title.

Playback targets one episode via `?episode=<path relative to the show folder>`:

- The path is resolved inside the show folder and refused if it escapes it.
- An episode that is named but cannot be resolved returns 404 rather than falling
  back to another file, so a broken link never silently plays the wrong episode.
- Transcode caches are keyed per episode. Playlists rewrite their segment URLs to
  carry the selector, since a player resolves bare segment names against the
  playlist URL and drops its query string.
- Without the parameter, playback falls back to the largest video in the folder —
  which is correct for a film but arbitrary for a series.

TMDB season data is cached in process for 24 hours (`TMDB_SEASON_CACHE_TTL`);
losing it on restart costs one request per season viewed.

### Featurettes

Files with no `SxxExx` are attributed to a season by their **folders**, never their
filename — a featurette called "Season 4 Overview" sitting in `Season 1` belongs
to Season 1. The deepest folder wins, so `.../Season 1-9 S01-S09/Featurettes/Season 9/`
resolves to 9 rather than the range above it. Season ranges are rejected outright
at both ends, which is why the pattern has a lookbehind as well as a lookahead.

Anything no folder attributes to a single season is show-wide and appears under an
**Extras** entry in the season dropdown. That entry matters: a show whose files
carry no `SxxExx` at all lists no seasons, so Extras is the only way to reach it.

## Collection Packs

A movie folder is treated as a pack — importing each film separately — only
when **two or more** videos above `PACK_MIN_FEATURE_BYTES` (300 MB) each parse
to their own release year. Bonus features routinely exceed the size floor but
are not year-stamped, which is what keeps a film-plus-extras folder importing as
a single title.

If extras ever do get imported as films, they show up as entries whose `path` is
a file nested inside another title's folder. To find them:

```text
python scripts/_consolidate_duplicates.py     # read-only; also lists duplicates
```

Imported pack members initially point at a file still inside the shared pack
folder. To give each film its own canonical folder:

```text
python scripts/_split_packs.py            # preview
python scripts/_split_packs.py --apply    # move them
```

Names come from the stored TMDB title and year, matching subtitle sidecars
travel with their film, and the emptied pack folder goes to the Recycle Bin
once no film is left inside it.

Moves use `os.rename`, never `shutil.move`. `shutil` silently falls back to
copy-then-delete when a file is locked, which leaves a full duplicate behind if
the delete then fails — the maintenance scripts fail loudly instead.

## Downloads and qBittorrent Availability

A title with no local file is listed in the library only while it has an active
download state — that state is the sole thing keeping a not-yet-downloaded title
visible in Movies or TV. If it is lost, the title vanishes from the library and
reappears as a gap under Discover.

Reconciliation therefore never runs against an unknown qBittorrent: an
unreachable WebUI raises `QbtUnavailableError` and the pass is skipped, instead
of reading the silence as "every torrent is gone". `_readopt_orphaned_downloads()`
then rebuilds a lost link from whatever qBittorrent is still running, matching on
title **and** year so a sequel cannot be adopted by mistake.

If a title has gone missing from the library while its torrent is still active,
just load any page with qBittorrent running — the relink happens automatically.

## Moving Files qBittorrent Is Seeding

Renaming or recycling a file that a torrent is still seeding breaks that
torrent: qBittorrent keeps pointing at the old path and stops. Stop the relevant
torrents before running any of the maintenance scripts, then clear up after with:

```text
python scripts/_qbt_prune_broken.py                        # list broken torrents
python scripts/_qbt_prune_broken.py --only "Ip Man" --apply  # remove, scoped
```

It removes torrent entries with `deleteFiles=false`, so files on disk are never
touched. Always scope with `--only` so unrelated broken torrents are left alone.

## Quality Upgrade Replacement

An upgrade finalises only when all of these hold:

1. qBittorrent reports the torrent complete — a done state **and**
   `progress == 1.0` **and** `amount_left == 0`.
2. A playable video file is present in the download.
3. The new file is genuinely better quality than the one it replaces.
   Otherwise the item is flagged `needs_review` and nothing is touched.

The replacement is hardlinked into the library (instant, no extra space, and
qBittorrent keeps seeding from the staging copy), falling back to a copy when
staging and library are on different volumes. The superseded file is sent to the
Recycle Bin only after the replacement is verifiably in place — if it cannot be
recycled, the upgrade is abandoned and the original is left untouched.

Set a **Downloads staging folder** in Settings, on the same drive as the
library, so in-progress downloads are never scanned and finished files link
instead of copying.

## UI State Semantics

Use these rules for all card badges and hero state actions so behavior stays predictable.

### Symbol meanings

- `⚠`: file missing. This means no playable local video file exists for the configured item path.
- `↓`: active download (`starting`, `handed_off`, `downloading`).
- `needs_review`: a finished upgrade was refused because it was not better
  quality than the file it would have replaced. Nothing was moved or deleted;
  the original is intact and the download is still in qBittorrent.
- `</>`: metadata incomplete (poster, synopsis, year, or quality missing).
- `CC`: subtitles missing.
- `↑`: higher quality available.

### Precedence rules

- File missing overrides metadata and subtitle badges.
- Downloading overrides filemissing, metadata, subtitle **and upgrade** badges, on
  the cards and on the hero card alike. A title still downloading has no file to
  improve on, and its `current_quality` is empty — which otherwise reads as "below
  your preferred quality" and offers an upgrade. Worse, acting on that offer can
  start a second download of the same title.
- Show at most one top-left state badge at a time: file missing first, otherwise upgrade available.
- Incomplete and `CC` can co-exist only when not missing and not downloading.

There are three places that render these badges — the server-side card, the
client-side card, and the hero card. They have drifted apart before; a change to
one belongs in all three.

### Hero actions

- Missing file state: show `Add to library` action using the torrent candidate flow.
- Downloading state: show download progress actions only.
- Local playable file state: show `Watch` action.

### Color intent

- Danger/missing: amber palette for file-missing state. #ffb700
- Info/in-progress: cyan palette for download-related state. #00e5e5
- Warning/incomplete: pink palette for incomplete metadata and missing subtitles. #d4006b
- Opportunity/upgrade: green for upgrade signals. #00e676

Do not communicate meaning by color alone. Keep symbol/text labels with state colors.

### UI validation checklist

- Missing file item shows only missing badge.
- Missing file item does not show incomplete or subtitles badges.
- Downloading item shows only download state badge.
- A local playable item with complete metadata shows no missing/incomplete subtitle warnings.

## Design Tokens & Branding Guidelines

Use these rules to maintain visual consistency across the UI. All theme properties are defined in the CSS `:root` scope and should be referenced via CSS variables where possible.

### Color Tokens

- `--bg`: #0b0919 (page background)
- `--surface`: #130c25 (card/panel background)
- `--surface2`: #1b1035 (input/secondary surface)
- `--border`: #2d1a52 (border/divider color)
- `--text`: #f0e6ff (primary text)
- `--text-muted`: #8870b0 (secondary/disabled text)
- `--pink`: #d4006b (warning/incomplete state)
- `--cyan`: #00e5e5 (info/active state)
- `--amber`: #ffb700 (danger/missing state + hyperlinks)
- `--purple`: #9b4dff (for legacy upgrade badges only; prefer green)
- `--green`: #00e676 (opportunity/upgrade state)

### Semantic Color Usage

**State badges on cards:**

- `⚠` File missing: amber
- `↓` Download active: cyan
- `</>` Incomplete metadata: pink
- `CC` Missing subtitles: pink
- `↑` Upgrade available: green

**Info cards in hero panel:**

- Quality: amber
- Subtitles: pink
- Rating: cyan
- Upgrade: green

**Interactive elements:**

- All hyperlinks and primary actions: amber (#ffb700)
- Primary buttons: cyan (#00e5e5)
- Destructive/warning buttons: pink (#d4006b)
- Secondary buttons: surface2 with border

### UI Elements & Styling

**Poster images:**

- Use sharp corners (border-radius: 0)
- Maintain 2:3 aspect ratio for consistency
- Do not round corners on collection badges or posters

**Hyperlinks:**

- All links use amber (#ffb700)
- Hover state: #ffd24d (lightened amber)
- Use underline or text color only; avoid background color

**Rounded corners (where used):**

- Inputs, buttons, modals: 5-6px border-radius
- Pills/badges: 10-12px border-radius
- Never apply border-radius to poster images

**Typography:**

- Keep color changes semantic; avoid using color for decoration
- Always pair color changes with text labels or symbols for accessibility
- Do not communicate meaning by color alone

### CSS Variable References

When styling new elements, always use CSS variables from `:root`:

- Use `var(--amber)` instead of `#ffb700`
- Use `var(--pink)` instead of `#d4006b`
- Use `var(--cyan)` instead of `#00e5e5`
- Use `var(--green)` instead of `#00e676`

Avoid hardcoded hex colors in inline styles or new CSS rules.

## qBittorrent Plugin Troubleshooting

Use this when the higher-quality search stops returning results or starts returning unrelated results.

### Plugin Location

- Engine file: `%LOCALAPPDATA%\qBittorrent\nova3\engines\kickasstorrents.py`
- Debug log: `%LOCALAPPDATA%\qBittorrent\logs\kickasstorrents-plugin.log`

### Known Failure Modes

- Some KAT mirrors serve JS-rendered placeholder pages for `/usearch/...` instead of static HTML rows.
- Some mirrors return homepage content for search-like URLs and produce unrelated results.
- Some mirrors wrap magnet links through `mylink.cloud` and require unwrapping.
- Mirror availability changes over time, so a previously working domain can degrade without code changes in this repo.

### Current Failsafe Behavior

- Mirror URLs are configured in the app Settings screen and synced into the qBittorrent engine file before searches run.
- The plugin tries mirrors in order from `MIRROR_URLS` in the engine file.
- It keeps the first mirror that returns parseable search rows.
- Detail-page magnet resolution also retries across mirrors.
- Keep the most reliable mirror first in the UI mirror list.

### Validation Commands

Run from the qBittorrent nova folder:

```powershell
cd $env:LOCALAPPDATA\qBittorrent\nova3
python nova2.py kickasstorrents all "the dark knight"
Get-Content "$env:LOCALAPPDATA\qBittorrent\logs\kickasstorrents-plugin.log" -Tail 30
```

Expected signals:

- Log lines should show `/usearch/...` URLs, not `/?q=` URLs.
- `parsed_rows` should be greater than `0` on at least one mirror.
- Emitted results should include magnet links and KAT detail URLs.

### If Search Breaks Again

1. Check the plugin log for which mirror was tried and whether `parsed_rows=0` on every page.
2. Open the mirror manually in a browser and confirm `/usearch/<query>/` shows real result rows.
3. If one mirror works in the browser, move it to the top of `MIRROR_URLS`.
4. If detail pages load but downloads fail, inspect whether magnets are still wrapped and update the unwrap regex if needed.
5. Re-run the validation commands above before touching app code.

## Trakt OAuth Setup

Use this when connecting or managing a Trakt account. For credential setup, see [README.md § Configure Trakt](../README.md#configure-trakt).

### Connect Flow

1. Start the app and open Settings.
2. Click **Connect Trakt** — the app calls `POST /api/trakt/connect/start`, stores the pending device code, and displays an 8-character activation code.
3. Click **Open activation page** (or visit <https://trakt.tv/activate> manually) and enter the code shown.
4. Approve the app on the Trakt site.
5. The Settings page polls `POST /api/trakt/connect/poll` every 5 seconds. On approval it fetches your Trakt profile, stores the token, and reloads Settings showing "Connected as [slug] via OAuth".

If you reload the page mid-flow, polling resumes automatically.

### Token Lifecycle

- Access tokens are valid for 3 months.
- The app checks expiry on every Trakt-using request and refreshes automatically.
- If refresh fails (revoked, expired, or app credentials changed), the stored token is cleared and the Connect button reappears.

### Disconnect

1. Open Settings.
2. Click **Disconnect** — calls `POST /api/trakt/disconnect`, revokes the token on Trakt's side, and clears all stored token data.

### Validation

1. Save Username, Client ID, and Client Secret from Settings.
2. Click **Test connection**.
3. Click **Connect Trakt** and finish the device activation flow.
4. Confirm the Settings page shows the connected Trakt account.

## TMDB Setup

Use this when validating TMDB access. For key setup, see [README.md § Configure TMDB](../README.md#configure-tmdb).

### TMDB Validation

1. Save the TMDB API key from Settings.
2. Click **Test connection**.
3. Confirm the Settings page reports a successful TMDB connection.

## Related Documents

- [README.md](../README.md) — setup, prerequisites, and configuration
- [Common/Roadmap.md](Roadmap.md) — approved and open work
- [Common/CHANGELOG.md](CHANGELOG.md) — shipped changes
- [docs/README.md](../docs/README.md) — technical reference index
