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
