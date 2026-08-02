"""A multi-film download must not file the wrong film under the right name.

Taken from a real case: a Predator pack of five films was finalised by picking
the largest video, which hardlinked Predator 2 (1990) into
`D:\\Movies\\Predator (1987)\\Predator (1987).mkv`. The library then played the
wrong film under a name that gave no hint of it, and the other four films sat
unfiled in the download folder.

Sandbox only — throwaway library root, staging dir and DB.
"""

import os
import shutil
import sys
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

import app
import medialibrary.downloads
from medialibrary.storage import Storage

PASS, FAIL = [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    note = ('  -- ' + str(detail)) if detail and not cond else ''
    print(f'{"PASS" if cond else "FAIL"}  {name}{note}')


def make_video(path, mb):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as handle:
        handle.write(b'\0' * int(mb * 1024 * 1024))


def setup(tmp):
    lib = os.path.join(tmp, 'Movies')
    staging = os.path.join(tmp, 'Downloads', 'movies')
    trash = os.path.join(tmp, 'Trash')
    for folder in (lib, staging, trash):
        os.makedirs(folder, exist_ok=True)
    app.store = Storage(os.path.join(tmp, 'test.db'))
    app.store.initialize()
    app.store.set_setting('movies_path', lib)
    app.store.set_setting('downloads_path', os.path.join(tmp, 'Downloads'))
    medialibrary.downloads.send2trash = lambda p: shutil.move(
        p, os.path.join(trash, os.path.basename(p))
    )
    return lib, staging


def add(imdb, title, year):
    app.store.add_media_item(
        imdb_id=imdb,
        tmdb_id=None,
        title=title,
        year=year,
        media_type='movie',
        collection_id=None,
        collection_name=None,
        current_quality=None,
        path=None,
    )
    return app.store.get_media_item_by_imdb_id(imdb)['id']


def row_for(media_id):
    return next(r for r in app.store.list_media_items() if r['id'] == media_id)


def build_pack(staging):
    """The real pack, with the wanted film deliberately not the largest."""
    pack = os.path.join(staging, 'Predator.1987-2022.Movie.Pack.2160p.x264.AAC-AOC')
    films = {
        'Predator.1987.2160p.UHD.BDRIP.x264-AOC': 420,
        'Predator.2.1990.2160p.UHD.BDRIP.x264-AOC': 460,  # largest
        'Predators.2010.2160p.UHD.BDRIP.x264-AOC': 430,
        'The.Predator.2018.2160p.UHD.BDRIP.x264-AOC': 440,
        'Prey.2022.2160p.WEBRIP.x264-AOC': 410,
    }
    for stem, mb in films.items():
        make_video(os.path.join(pack, stem, f'{stem}.mkv'), mb)
    return pack


print('\n=== 1. The wanted film is filed, not the largest ===')
with tempfile.TemporaryDirectory() as tmp:
    lib, staging = setup(tmp)
    pack = build_pack(staging)
    media_id = add('tt0093773', 'Predator', 1987)
    app.store.set_download_state(
        media_id, 'downloading', 'qb_webui', 'x', torrent_hash='A' * 40, mode='fill'
    )
    app._finalize_completed_download(
        row_for(media_id),
        {'state': 'stalledUP', 'progress': 1.0, 'amount_left': 0, 'content_path': pack},
    )

    want = os.path.join(lib, 'Predator (1987)', 'Predator (1987).mkv')
    check('the canonical file exists', os.path.isfile(want), want)
    if os.path.isfile(want):
        source = os.path.join(
            pack,
            'Predator.1987.2160p.UHD.BDRIP.x264-AOC',
            'Predator.1987.2160p.UHD.BDRIP.x264-AOC.mkv',
        )
        wrong = os.path.join(
            pack,
            'Predator.2.1990.2160p.UHD.BDRIP.x264-AOC',
            'Predator.2.1990.2160p.UHD.BDRIP.x264-AOC.mkv',
        )
        check('it is the 1987 film', os.path.samefile(want, source))
        check('it is NOT Predator 2, the largest', not os.path.samefile(want, wrong))
    check(
        'every film is still in the download folder',
        len([1 for _r, _d, f in os.walk(pack) for n in f if n.endswith('.mkv')]) == 5,
    )

print('\n=== 2. A pack that matches nothing is left alone and flagged ===')
with tempfile.TemporaryDirectory() as tmp:
    lib, staging = setup(tmp)
    pack = build_pack(staging)
    media_id = add('tt0000002', 'Alien', 1979)
    app.store.set_download_state(
        media_id, 'downloading', 'qb_webui', 'x', torrent_hash='B' * 40, mode='fill'
    )
    app._finalize_completed_download(
        row_for(media_id),
        {'state': 'stalledUP', 'progress': 1.0, 'amount_left': 0, 'content_path': pack},
    )
    check('nothing was placed in the library', os.listdir(lib) == [], str(os.listdir(lib)))
    check(
        'flagged for review',
        row_for(media_id)['download_status'] == 'needs_review',
        str(row_for(media_id)['download_status']),
    )
    message = row_for(media_id)['download_message'] or ''
    check(
        'the message says how many films and where they are',
        '5 films' in message and 'untouched' in message,
        message,
    )

print('\n=== 4. The other films in the pack are filed too ===')
with tempfile.TemporaryDirectory() as tmp:
    lib, staging = setup(tmp)
    pack = build_pack(staging)

    # Stub the identification so the test needs no network and no API key.
    CATALOGUE = {
        'predator2': ('tt0099740', 'Predator 2', 1990),
        'predators': ('tt1424381', 'Predators', 2010),
        'thepredator': ('tt3829266', 'The Predator', 2018),
        'prey': ('tt11866324', 'Prey', 2022),
    }

    def fake_identify(name, media_type):
        # Parse the release name the way the real lookup does, then answer from
        # the table — so the test exercises the parsing rather than replacing it.
        import re as _re

        from medialibrary.identify import normalize_media_name

        parsed, _year = normalize_media_name(name, media_type)
        key = _re.sub(r'[^a-z0-9]', '', (parsed or '').lower())
        if key not in CATALOGUE:
            return None
        imdb, title, year = CATALOGUE[key]
        return {
            'imdb_id': imdb,
            'tmdb_id': None,
            'title': title,
            'year': year,
            'media_type': 'movie',
            'poster_url': '',
            'synopsis': '',
            'actors': '',
            'genre_1': '',
            'genre_2': '',
            'rating': None,
        }

    import medialibrary.importer

    medialibrary.importer.identify_for_library = fake_identify
    medialibrary.downloads.cache_poster = lambda *a, **k: ''

    media_id = add('tt0093773', 'Predator', 1987)
    app.store.set_download_state(
        media_id, 'downloading', 'qb_webui', 'x', torrent_hash='D' * 40, mode='fill'
    )
    app._finalize_completed_download(
        row_for(media_id),
        {'state': 'stalledUP', 'progress': 1.0, 'amount_left': 0, 'content_path': pack},
    )

    folders = sorted(os.listdir(lib))
    check('all five films have canonical folders', len(folders) == 5, str(folders))
    for expected in (
        'Predator (1987)',
        'Predator 2 (1990)',
        'Predators (2010)',
        'The Predator (2018)',
        'Prey (2022)',
    ):
        check(f'  {expected}', expected in folders)
    check(
        'each folder holds its canonically named film',
        all(os.path.isfile(os.path.join(lib, f, f + '.mkv')) for f in folders),
        str(folders),
    )
    titles = {i['title'] for i in app.store.list_media_items()}
    check(
        'library items exist for the extras',
        {'Predator 2', 'Predators', 'The Predator', 'Prey'} <= titles,
        str(sorted(titles)),
    )
    check(
        'the originally wanted item is unchanged',
        app.store.get_media_item(media_id)['path'] == os.path.join(lib, 'Predator (1987)'),
    )
    check(
        'nothing left the download folder',
        len([1 for _r, _d, f in os.walk(pack) for n in f if n.endswith('.mkv')]) == 5,
    )

print('\n=== 5. An unidentifiable extra is left where it is ===')
with tempfile.TemporaryDirectory() as tmp:
    lib, staging = setup(tmp)
    pack = build_pack(staging)
    import medialibrary.importer

    medialibrary.importer.identify_for_library = lambda name, media_type: None
    medialibrary.downloads.cache_poster = lambda *a, **k: ''

    media_id = add('tt0093773', 'Predator', 1987)
    app.store.set_download_state(
        media_id, 'downloading', 'qb_webui', 'x', torrent_hash='E' * 40, mode='fill'
    )
    app._finalize_completed_download(
        row_for(media_id),
        {'state': 'stalledUP', 'progress': 1.0, 'amount_left': 0, 'content_path': pack},
    )
    check(
        'only the wanted film is filed',
        os.listdir(lib) == ['Predator (1987)'],
        str(os.listdir(lib)),
    )
    check('no invented library items', len(app.store.list_media_items()) == 1)

print('\n=== 3. An ordinary single-film download is unaffected ===')
with tempfile.TemporaryDirectory() as tmp:
    lib, staging = setup(tmp)
    folder = os.path.join(staging, 'Predator.1987.2160p.UHD.BDRIP.x264-AOC')
    make_video(os.path.join(folder, 'Predator.1987.2160p.mkv'), 420)
    make_video(os.path.join(folder, 'sample.mkv'), 2)
    media_id = add('tt0093773', 'Predator', 1987)
    app.store.set_download_state(
        media_id, 'downloading', 'qb_webui', 'x', torrent_hash='C' * 40, mode='fill'
    )
    app._finalize_completed_download(
        row_for(media_id),
        {'state': 'stalledUP', 'progress': 1.0, 'amount_left': 0, 'content_path': folder},
    )
    want = os.path.join(lib, 'Predator (1987)', 'Predator (1987).mkv')
    check('the film is filed as normal', os.path.isfile(want), want)
    check('download state cleared', row_for(media_id)['download_status'] is None)

print(f'\npassed {len(PASS)}   failed {len(FAIL)}')
if FAIL:
    print('FAILED: ' + ', '.join(FAIL))
sys.exit(1 if FAIL else 0)
