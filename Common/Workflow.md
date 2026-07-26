# Workflow Runbook

This document defines repeatable workflows for operating Media-Library safely. For initial setup, prerequisites, and first run, see [README.md](../README.md).

## Validation Checklist

- App starts without traceback.
- Home page loads.
- Search page loads.
- Add-title workflow works with a known IMDb ID.
- Quality-check workflow returns expected status.

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

## Current Handoff: Video Player (2026-04-13)

Use this section to resume ML-4 without re-discovery.

### Current State

- Phase 1 baseline works: built-in player page, playback persistence, subtitle track rendering.
- FFmpeg HLS transcoding is wired and produces playable output for previously failing titles (for example, Blade and Alien Romulus).
- Audio compatibility improved by forcing browser-safe transcode output (`H.264 + AAC stereo`).
- Embedded subtitle streams are discovered and can be extracted to WebVTT on demand.
- Direct play implemented (2026-04-13): player calls `/api/video/<id>/strategy` on load. H.264 + AAC/MP3/Opus files are served via `/stream` (range requests) — instant start, full seek bar, no FFmpeg. Non-compatible files fall back to HLS transcode.
- HLS early-start implemented (2026-04-13): player now starts playback once 3 segments exist (~15-20s). Playlist type changed to `EVENT` so hls.js polls for new segments during encode. Seek bar covers only transcoded content until full encode completes.
- HLS keyframe alignment patched (2026-04-13): FFmpeg now forces keyframes at every segment boundary via `-force_key_frames:0 expr:gte(t,n_forced*6)`, disables scene-cut with `-sc_threshold:v:0 0`, and caps GOP with `-g 180 -keyint_min 6`. Segments were drifting to ~10s; this targets true 6-second segments.

### Known State

- H.264+AAC/MP3/Opus files: instant playback via direct play, full seek bar from the start.
- Other codecs (HEVC, DTS audio, etc.): ~15-20s HLS startup, seek bar grows as encode progresses.
- Keyframe alignment in place for HLS; `#EXTINF` values should be ~6s (verify after next HLS play).
- User validation (2026-04-13): Alien Romulus playback is now stable after cache/recovery fixes. HLS quality was bumped to `veryfast` + CRF 21, and a custom scrubber fallback was added so the playback bar is visible even during partial HLS playback.

### What Was Learned

- A strict "wait for `#EXT-X-ENDLIST`" gate is too slow for first-play user experience.
- A strict "start as soon as first segments exist" gate is fast but can produce timeline/control edge cases.
- Mature media servers (Jellyfin reference) solve this with a decision matrix: Direct Play → Direct Stream (remux) → Full Transcode. Only falling back when container or codec constraints require it.
- Jellyfin's `EncodingHelper.GetHlsVideoKeyFrameArguments` confirmed that `-force_key_frames` + `-sc_threshold 0` + `-g`/`-keyint_min` is the correct combo for seekable HLS.
- Without keyframe forcing, `hls_time` is only a hint; actual segment cuts happen at natural GOP boundaries which may be far apart in source content.

### Recommended Next Session Plan

1. Validate the 6s segment target: check `#EXTINF` values in `master.m3u8` after a fresh play.
2. Improve transcode quality for HLS fallback (currently `libx264 ultrafast + crf 24`): test `veryfast` and CRF 20-22, then compare startup impact vs quality.
3. Add a direct-stream/remux path for non-direct-play files where video can be copied and only audio needs conversion; this should preserve source video quality and still start quickly.
4. Seek bar across partial EVENT playlists: add a custom scrubber UI that knows the full duration from ffprobe even before full transcode completion.
5. Consider caching the strategy/probe result in DB so ffprobe does not run on every play.

### Quick Resume Commands

```powershell
cd "c:\Users\rache\Documents\Open Projects\Media-Library"
& ".\.venv\Scripts\Activate.ps1"
python app.py
```

Optional reset when testing transcode behavior changes:

```powershell
Get-Process ffmpeg -ErrorAction SilentlyContinue | Stop-Process -Force
if (Test-Path "tmp\hls") { Remove-Item "tmp\hls" -Recurse -Force }
New-Item -ItemType Directory -Path "tmp\hls" | Out-Null
```
