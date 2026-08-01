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
- `Common/DevLog.md` — working notes and handoffs (not in version control)
- [docs/README.md](../docs/README.md) — technical reference index
