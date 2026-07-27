"""Paths and external tool locations, in one place.

Kept separate from app.py so the modules split out of it do not have to import
the application to find out where the cache lives.
"""

import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, 'library.db')
POSTER_DIR = os.path.join(BASE_DIR, 'static', 'posters')
HLS_CACHE_DIR = os.path.join(BASE_DIR, 'tmp', 'hls')

FFPROBE_EXE = os.environ.get('FFPROBE_EXE', 'ffprobe')
FFMPEG_EXE = os.environ.get('FFMPEG_EXE', 'ffmpeg')

# qBittorrent. The URL and username are also settable in the UI, and the stored
# setting wins over the environment; the password is environment-only.
QBT_NOVA_PATH = os.environ.get(
    'QBT_NOVA_PATH',
    os.path.expandvars(r'%LOCALAPPDATA%\\qBittorrent\\nova3'),
)
QBT_WEBUI_URL = os.environ.get('QBT_WEBUI_URL', '').strip().rstrip('/')
QBT_WEBUI_USERNAME = os.environ.get('QBT_WEBUI_USERNAME', '').strip()
QBT_WEBUI_PASSWORD = os.environ.get('QBT_WEBUI_PASSWORD', '').strip()

# How long Discover's TMDB lookups stay cached. Checking every show cold takes
# about a minute, so these are deliberately generous.
DISCOVER_COLLECTION_CACHE_HOURS = int(os.environ.get('DISCOVER_COLLECTION_CACHE_HOURS', '24'))
DISCOVER_WATCHLIST_CACHE_HOURS = int(os.environ.get('DISCOVER_WATCHLIST_CACHE_HOURS', '24'))

TRUSTED_RELEASE_GROUPS = (
    'qxr',
    'tigole',
    'ctrlhd',
    'framestor',
    'flux',
    'ntb',
    'rarbg',
    'yts',
)
