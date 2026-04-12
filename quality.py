import os
import re
import struct
import subprocess


QUALITY_ORDER = {
    '480p': 1,
    '720p': 2,
    '1080p': 3,
    '1440p': 4,
    '2160p': 5,
    '4k': 5,
}


def detect_quality(text: str) -> str | None:
    value = text.lower()
    for token in ('2160p', '4k', '1440p', '1080p', '720p', '480p'):
        if token in value:
            return token

    # Sometimes quality is encoded as dimensions.
    if re.search(r'3840\s*[xX]\s*2160', value):
        return '2160p'
    if re.search(r'1920\s*[xX]\s*1080', value):
        return '1080p'

    return None


def compare_quality(current_quality: str | None, candidate_quality: str | None) -> int:
    current_rank = QUALITY_ORDER.get((current_quality or '').lower(), 0)
    candidate_rank = QUALITY_ORDER.get((candidate_quality or '').lower(), 0)

    if candidate_rank > current_rank:
        return 1
    if candidate_rank == current_rank:
        return 0
    return -1


# File-based quality detection
def _dims_to_quality(w: int, h: int) -> str | None:
    """Map video pixel dimensions to a quality label."""
    if w >= 3840 or h >= 2160:
        return '2160p'
    if w >= 2560 or h >= 1440:
        return '1440p'
    if w >= 1920 or h >= 1080:
        return '1080p'
    if w >= 1280 or h >= 720:
        return '720p'
    if h >= 480:
        return '480p'
    if w > 0 and h > 0:
        return 'SD'
    return None


# ffprobe
def _ffprobe_dims(path: str, ffprobe_exe: str = 'ffprobe') -> tuple[int, int] | None:
    try:
        result = subprocess.run(
            [ffprobe_exe, '-v', 'quiet', '-select_streams', 'v:0',
             '-show_entries', 'stream=width,height', '-of', 'csv=s=x:p=0', path],
            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30,
        )
        if result.returncode == 0 and result.stdout.strip():
            parts = [p for p in result.stdout.strip().split('x') if p]
            if len(parts) >= 2:
                return int(parts[0]), int(parts[1])
    except Exception:
        pass
    return None


# MP4 / ISOBMFF atom parser
def _mp4_dims(path: str) -> tuple[int, int] | None:
    """Read video dimensions from an MP4/M4V file by parsing tkhd atoms."""
    try:
        with open(path, 'rb') as f:
            return _mp4_search(f, os.path.getsize(path))
    except Exception:
        return None


def _mp4_search(f, end_offset: int) -> tuple[int, int] | None:
    while f.tell() + 8 <= end_offset:
        hdr = f.read(8)
        if len(hdr) < 8:
            break
        size_raw = struct.unpack('>I', hdr[:4])[0]
        kind = hdr[4:8]
        pos = f.tell()
        if size_raw == 1:
            ext = f.read(8)
            if len(ext) < 8:
                break
            payload = struct.unpack('>Q', ext)[0] - 16
            pos = f.tell()
        elif size_raw == 0:
            payload = end_offset - pos
        else:
            payload = size_raw - 8

        if kind == b'tkhd':
            data = f.read(min(payload, 128))
            dims = _parse_tkhd(data)
            if dims and dims[0] > 0 and dims[1] > 0:
                return dims
        elif kind in (b'moov', b'trak'):
            result = _mp4_search(f, pos + payload)
            if result:
                return result
            f.seek(pos + payload)
            continue
        f.seek(pos + payload)
    return None


def _parse_tkhd(data: bytes) -> tuple[int, int] | None:
    """Extract width/height from a tkhd box payload (16.16 fixed-point at end)."""
    if not data:
        return None
    version = data[0]
    # version 0: 4+20 header bytes; version 1: 4+32 header bytes
    skip = (76 if version == 0 else 88)
    if len(data) < skip + 8:
        return None
    w = struct.unpack('>I', data[skip:skip + 4])[0] >> 16
    h = struct.unpack('>I', data[skip + 4:skip + 8])[0] >> 16
    return (w, h)


# MKV / EBML parser

_SEGMENT_ID = b'\x18\x53\x80\x67'
_TRACKS_ID  = b'\x16\x54\xae\x6b'
_ENTRY_ID   = b'\xae'
_TYPE_ID    = b'\x83'
_VIDEO_ID   = b'\xe0'
_WIDTH_ID   = b'\xb0'
_HEIGHT_ID  = b'\xba'
_EBML_UNKNOWN_SIZE = 2 ** 56  # sentinel for unknown-size elements


def _ebml_read_id(f) -> bytes | None:
    b = f.read(1)
    if not b:
        return None
    b0 = b[0]
    if b0 & 0x80:
        return b
    elif b0 & 0x40:
        r = f.read(1)
        return (b + r) if len(r) == 1 else None
    elif b0 & 0x20:
        r = f.read(2)
        return (b + r) if len(r) == 2 else None
    elif b0 & 0x10:
        r = f.read(3)
        return (b + r) if len(r) == 3 else None
    return None


def _ebml_read_size(f) -> int | None:
    b = f.read(1)
    if not b:
        return None
    b0 = b[0]
    if b0 & 0x80:
        v = b0 & 0x7F
        return _EBML_UNKNOWN_SIZE if v == 0x7F else v
    elif b0 & 0x40:
        r = f.read(1)
        if len(r) < 1:
            return None
        v = ((b0 & 0x3F) << 8) | r[0]
        return _EBML_UNKNOWN_SIZE if v == 0x3FFF else v
    elif b0 & 0x20:
        r = f.read(2)
        if len(r) < 2:
            return None
        v = ((b0 & 0x1F) << 16) | (r[0] << 8) | r[1]
        return _EBML_UNKNOWN_SIZE if v == 0x1FFFFF else v
    elif b0 & 0x10:
        r = f.read(3)
        if len(r) < 3:
            return None
        v = ((b0 & 0x0F) << 24) | (r[0] << 16) | (r[1] << 8) | r[2]
        return _EBML_UNKNOWN_SIZE if v == 0x0FFFFFFF else v
    elif b0 & 0x08:
        r = f.read(4)
        if len(r) < 4:
            return None
        v = ((b0 & 0x07) << 32) | (r[0] << 24) | (r[1] << 16) | (r[2] << 8) | r[3]
        return _EBML_UNKNOWN_SIZE if v == 0x07FFFFFFFF else v
    return _EBML_UNKNOWN_SIZE


def _mkv_dims(path: str) -> tuple[int, int] | None:
    """Read video dimensions from an MKV/WebM file via EBML parsing."""
    LIMIT = 20 * 1024 * 1024  # only search first 20 MB
    try:
        with open(path, 'rb') as f:
            while f.tell() < LIMIT:
                eid = _ebml_read_id(f)
                if eid is None:
                    break
                esize = _ebml_read_size(f)
                if esize is None:
                    break
                epos = f.tell()
                cap = min(esize, LIMIT - epos)

                if eid == _SEGMENT_ID:
                    result = _ebml_find_tracks(f, epos + cap)
                    if result:
                        return result
                    break
                f.seek(epos + min(esize, LIMIT))
    except Exception:
        pass
    return None


def _ebml_find_tracks(f, end: int) -> tuple[int, int] | None:
    while f.tell() + 4 < end:
        eid = _ebml_read_id(f)
        if eid is None:
            break
        esize = _ebml_read_size(f)
        if esize is None:
            break
        epos = f.tell()
        if eid == _TRACKS_ID:
            return _ebml_parse_tracks(f, epos + min(esize, end - epos))
        # Skip Clusters — they come after Tracks, stop searching
        if eid == b'\x1f\x43\xb6\x75':
            break
        f.seek(epos + min(esize, end - epos))
    return None


def _ebml_parse_tracks(f, end: int) -> tuple[int, int] | None:
    while f.tell() + 2 < end:
        eid = _ebml_read_id(f)
        if eid is None:
            break
        esize = _ebml_read_size(f)
        if esize is None:
            break
        epos = f.tell()
        if eid == _ENTRY_ID:
            result = _ebml_parse_track_entry(f, epos + esize)
            if result:
                return result
        f.seek(epos + esize)
    return None


def _ebml_parse_track_entry(f, end: int) -> tuple[int, int] | None:
    track_type = None
    video_payload_pos = None
    video_payload_end = None

    while f.tell() + 2 < end:
        eid = _ebml_read_id(f)
        if eid is None:
            break
        esize = _ebml_read_size(f)
        if esize is None:
            break
        epos = f.tell()

        if eid == _TYPE_ID:
            data = f.read(esize)
            track_type = data[0] if data else None
        elif eid == _VIDEO_ID:
            video_payload_pos = epos
            video_payload_end = epos + esize
            f.seek(epos + esize)
        else:
            f.seek(epos + esize)

    if track_type == 1 and video_payload_pos is not None:
        f.seek(video_payload_pos)
        return _ebml_parse_video(f, video_payload_end)
    return None


def _ebml_parse_video(f, end: int) -> tuple[int, int] | None:
    w = h = None
    while f.tell() + 2 < end:
        eid = _ebml_read_id(f)
        if eid is None:
            break
        esize = _ebml_read_size(f)
        if esize is None:
            break
        epos = f.tell()

        if eid == _WIDTH_ID:
            data = f.read(esize)
            w = int.from_bytes(data, 'big')
        elif eid == _HEIGHT_ID:
            data = f.read(esize)
            h = int.from_bytes(data, 'big')
        else:
            f.seek(epos + esize)

        if w and h:
            return (w, h)
    return (w, h) if (w and h) else None


# Public entry point
def detect_quality_from_file(path: str, ffprobe_exe: str | None = None) -> str | None:
    """Detect video quality by inspecting the file.

    Strategy:
    1. ffprobe if available (most accurate).
    2. MP4 atom parser for .mp4 / .m4v files.
    3. MKV EBML parser for .mkv / .webm files.
    4. Filename-based heuristic as last resort.
    """
    if not path or not os.path.isfile(path):
        return None

    dims: tuple[int, int] | None = None

    # 1. ffprobe
    exe = ffprobe_exe or 'ffprobe'
    dims = _ffprobe_dims(path, exe)

    # 2/3. Pure-Python binary parsers
    if not dims:
        _, ext = os.path.splitext(path)
        ext = ext.lower()
        if ext in ('.mp4', '.m4v', '.mov'):
            dims = _mp4_dims(path)
        elif ext in ('.mkv', '.webm'):
            dims = _mkv_dims(path)

    if dims:
        return _dims_to_quality(dims[0], dims[1])

    # 4. Filename fallback
    return detect_quality(os.path.basename(path))
