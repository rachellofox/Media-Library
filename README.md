# Media Library

A local-first media library for movies and TV. Self-hosted, no cloud required. Built with Python, Flask, and SQLite.

## What it does

- Imports movies and TV shows from your local media folders automatically.
- Fetches metadata, posters, information via TMDB.
- Fetches quality information via FFprobe.
- Tracks collection completeness — flags missing entries from multi-film series.
- Syncs your Trakt watchlist and collection via OAuth.
- Discovers trending titles, missing collection entries, and watchlist items.
- Searches for higher-quality torrents via a torrent plugin and surfaces upgrade candidates.
- Submits downloads directly to your torrent client's Web UI with the correct save path.

## Prerequisites

| Prerequisite | Required | Notes |
| --- | --- | --- |
| Python 3.10+ | Yes | |
| qBittorrent | For torrent search and download | Enable the Web UI in qBittorrent preferences |
| KAT nova3 plugin | For quality-search feature | See [Configure the torrent plugin](#configure-the-torrent-plugin) |
| FFprobe | For accurate quality detection | Install via FFmpeg; path set in Settings |
| TMDB API key | For metadata and posters | Free at [developer.themoviedb.org](https://developer.themoviedb.org/docs/getting-started) |
| Trakt account + API app | For watchlist and collection sync | OAuth credentials configured in Settings |

## Project layout

| Path | Purpose |
| --- | --- |
| `app.py` | Flask routes and request handling |
| `storage.py` | SQLite schema and data-access helpers |
| `tmdb_client.py` | TMDB API client (metadata, posters, collections, search) |
| `trakt_client.py` | Trakt API client (OAuth, watchlist, collection sync) |
| `qb_search.py` | qBittorrent nova3 plugin integration |
| `quality.py` | Quality string parsing and comparison logic |
| `imdb_client.py` | Retained; not in active use |
| `templates/` | Jinja2 HTML templates |
| `static/posters/` | Cached poster images (runtime, not tracked in git) |
| `Common/` | Governance docs: [Runbook](Common/Runbook.md), [Roadmap](Common/Roadmap.md), [Changelog](Common/CHANGELOG.md) |
| `docs/` | Technical reference documents |
| `scripts/` | Developer maintenance utilities (audit, cleanup, debug) |

## Setup

```powershell
cd "path\to\Media-Library"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Run

```powershell
.\.venv\Scripts\Activate.ps1
python app.py
```

Open: <http://127.0.0.1:5100>

All configuration is done from the **Settings** tab inside the app. Nothing needs to be set in the environment before first run.

## Configure the torrent plugin

The quality-search and upgrade features require a plugin for your torrent client. This repo uses a KAT plugin for qBittorrent's nova3 search framework.

1. Install qBittorrent if you haven't already.
2. Copy `kickasstorrents.py` from this repo into:
   `%LOCALAPPDATA%\qBittorrent\nova3\engines\`
3. In Settings, set **Nova path** to the nova3 folder:
   `%LOCALAPPDATA%\qBittorrent\nova3`
4. In Settings, add one or more working KAT mirror URLs under **Torrent mirrors**.

See [Common/Runbook.md](Common/Runbook.md) for plugin troubleshooting steps and validation commands.

## Configure torrent client Web UI

These instructions are specific to qBittorrent. For other torrent clients, use their official docs.

1. Enable the Web UI in qBittorrent: Tools → Preferences → Web UI.
2. In Settings, set **qBittorrent Web UI URL**, **username**, and **password**.
3. When configured, Discover download selections are submitted directly to qBittorrent and saved to your configured movies or TV path.

## Configure TMDB

1. Get a free API key at <https://developer.themoviedb.org/docs/getting-started>.
2. Open Settings in the app and paste the key into **TMDB API key**.
3. Click **Test connection** to verify.

The key is stored in the OS credential store via `keyring` — not in `.env` or the database.

## Configure Trakt

1. Create a Trakt API application at <https://trakt.tv/oauth/applications> to get a Client ID and Client Secret.
2. Open Settings and save your Trakt **Username**, **Client ID**, and **Client Secret**.
3. Click **Connect Trakt** and follow the device activation flow: note the code shown, open the activation page, and enter the code.
4. The app polls for approval automatically. Once approved, your Trakt account is linked.

The Client Secret is stored in the OS credential store. See [Common/Runbook.md](Common/Runbook.md) for the full OAuth flow and token lifecycle.

## Configure FFprobe

1. Install FFmpeg (includes FFprobe). Available via [gyan.dev](https://www.gyan.dev/ffmpeg/builds/) or `winget install Gyan.FFmpeg`.
2. In Settings, set the **FFprobe path** to the `ffprobe.exe` location.

Without FFprobe, quality detection falls back to filename parsing only.

## Environment variables (optional)

Two variables can be set in `.env` or the shell to override Settings for `QBT_NOVA_PATH` and `FFPROBE_EXE`. All other credentials must be configured through the Settings tab.

## Security

- API keys and OAuth credentials are stored in the OS credential store via `keyring`.
- `library.db` (your library data) and `static/posters/` (cached images) are excluded from version control.
- No secrets are written to `.env`, the database, or rendered into page source.

## Project standards

- Runbook and operational procedures: [Common/Runbook.md](Common/Runbook.md)
- Feature roadmap: [Common/Roadmap.md](Common/Roadmap.md)
- Shipped changes: [Common/CHANGELOG.md](Common/CHANGELOG.md)
