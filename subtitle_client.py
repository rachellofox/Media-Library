"""Subtitle fetching via subliminal using free, ad-free providers."""

import os

import subliminal
from babelfish import Language

# Providers that are free and require no account or API key.
# Prefer providers that are currently reliable on this host.
_FREE_PROVIDERS = ['opensubtitles', 'tvsubtitles', 'bsplayer', 'podnapisi']


def fetch_subtitles(video_path: str, title: str | None = None, year: int | None = None) -> bool:
    """Fetch the best English subtitle for the given video file.

    Uses hash-based + metadata matching via subliminal. Saves the subtitle file
    (.srt) alongside the video. Returns True if a subtitle was downloaded and
    saved, False if nothing was found.
    """
    if not video_path or not os.path.isfile(video_path):
        return False

    lang_set = {Language('eng')}

    try:
        video = subliminal.scan_video(video_path)
    except Exception:
        # If the media-info scan fails (no ffprobe/mediainfo), fall back to a
        # plain Movie/Episode guessed from the filename.
        from subliminal import Movie

        guess_name = os.path.basename(video_path)
        video = Movie(guess_name, year=year)

    if title and not video.title:
        video.title = title
    if year and not video.year:
        video.year = year

    # Build provider-specific hashes/metadata before search (critical for bsplayer).
    subliminal.refine(video, providers=_FREE_PROVIDERS)

    subtitles = subliminal.download_best_subtitles(
        [video],
        lang_set,
        providers=_FREE_PROVIDERS,
    )

    found = subtitles.get(video)
    if not found:
        return False

    subliminal.save_subtitles(video, found)
    return True
