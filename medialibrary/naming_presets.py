"""The naming conventions the Tools section offers (F-0808.01).

A fixed list rather than free-form templates: the app has to be able to read
its own filenames back — `scan_local_episodes` recovers an episode's season
and number from its name — so a convention that dropped the SxxExx marker
would leave the library unable to find its own episodes. Picking from a list
of known-good shapes removes that failure mode entirely.

Templates are internal. They use `str.format`, which is safe here only
because every template in this file is written by hand and none comes from
user input.
"""

from dataclasses import dataclass

from medialibrary.naming import sanitize_title


@dataclass(frozen=True)
class NamingPreset:
    key: str
    label: str
    description: str
    # Movies
    movie_folder: str
    movie_file: str
    # TV
    show_folder: str
    season_folder: str
    episode_file: str
    # Used when the episode's title is not known — never invented, so the
    # marker alone has to make a valid name.
    episode_file_untitled: str

    def movie_names(self, title: str, year: int | None) -> tuple[str, str]:
        """(folder_name, file_stem) for a movie."""
        clean = sanitize_title(title)
        if not clean:
            return '', ''
        tokens = {'title': clean, 'year': year if year else ''}
        folder = _tidy(self.movie_folder.format(**tokens))
        stem = _tidy(self.movie_file.format(**tokens))
        return folder, stem

    def show_folder_name(self, title: str, year: int | None) -> str:
        clean = sanitize_title(title)
        if not clean:
            return ''
        return _tidy(self.show_folder.format(title=clean, year=year if year else ''))

    def season_folder_name(self, season: int) -> str:
        return _tidy(self.season_folder.format(season=int(season)))

    def episode_file_stem(
        self,
        show_title: str,
        season: int,
        episode: int,
        episode_title: str | None,
        last_episode: int | None = None,
    ) -> str:
        """Filename stem for one episode, with no extension.

        `last_episode` covers a single file holding a run of episodes: the
        range has to stay in the name or the scanner stops seeing the later
        ones as present.
        """
        show = sanitize_title(show_title) or 'Show'
        clean_episode_title = sanitize_title(episode_title or '')
        tokens = {
            'title': show,
            'season': int(season),
            'episode': int(episode),
            'episode_title': clean_episode_title,
        }
        template = self.episode_file if clean_episode_title else self.episode_file_untitled
        stem = _tidy(template.format(**tokens))
        if last_episode is not None and int(last_episode) != int(episode):
            marker = f'S{int(season):02d}E{int(episode):02d}'
            stem = stem.replace(marker, f'{marker}-E{int(last_episode):02d}', 1)
        return stem


def _tidy(text: str) -> str:
    """Collapse the gaps an empty token leaves behind, e.g. a missing year."""
    text = ' '.join(text.split())
    # "Title ()" when a year is unknown, and a separator left dangling.
    text = text.replace(' ()', '').replace(' []', '')
    return text.strip(' -').strip()


PRESETS: tuple[NamingPreset, ...] = (
    NamingPreset(
        key='house',
        label='Current (house style)',
        description=(
            'What the library already uses: movies year-stamped, TV shows not. '
            'Episodes read "Show - S01E01 - Episode Title".'
        ),
        movie_folder='{title} ({year})',
        movie_file='{title} ({year})',
        show_folder='{title}',
        season_folder='Season {season:02d}',
        episode_file='{title} - S{season:02d}E{episode:02d} - {episode_title}',
        episode_file_untitled='{title} - S{season:02d}E{episode:02d}',
    ),
    NamingPreset(
        key='plex',
        label='Plex / Jellyfin / Emby',
        description=(
            'As recommended by Plex: show folders carry the year too, which '
            'helps a media server match the right series.'
        ),
        movie_folder='{title} ({year})',
        movie_file='{title} ({year})',
        show_folder='{title} ({year})',
        season_folder='Season {season:02d}',
        episode_file='{title} - S{season:02d}E{episode:02d} - {episode_title}',
        episode_file_untitled='{title} - S{season:02d}E{episode:02d}',
    ),
    NamingPreset(
        key='kodi',
        label='Kodi',
        description='Year-stamped throughout, with spaces instead of dashes between the parts.',
        movie_folder='{title} ({year})',
        movie_file='{title} ({year})',
        show_folder='{title} ({year})',
        season_folder='Season {season:02d}',
        episode_file='{title} S{season:02d}E{episode:02d} {episode_title}',
        episode_file_untitled='{title} S{season:02d}E{episode:02d}',
    ),
    NamingPreset(
        key='no_year',
        label='Titles only, no year',
        description=(
            'No year anywhere. Simplest to read; a remake shares a folder name '
            'with its original.'
        ),
        movie_folder='{title}',
        movie_file='{title}',
        show_folder='{title}',
        season_folder='Season {season:02d}',
        episode_file='{title} - S{season:02d}E{episode:02d} - {episode_title}',
        episode_file_untitled='{title} - S{season:02d}E{episode:02d}',
    ),
)

DEFAULT_PRESET_KEY = 'house'
PRESET_SETTING = 'naming_preset'

_BY_KEY = {preset.key: preset for preset in PRESETS}


def get_preset(key: str | None) -> NamingPreset:
    """The named preset, or the house style for anything unrecognised."""
    return _BY_KEY.get((key or '').strip(), _BY_KEY[DEFAULT_PRESET_KEY])


def configured_preset(store) -> NamingPreset:
    return get_preset(store.get_setting(PRESET_SETTING))


def preset_choices() -> list[dict]:
    """The list the Tools section renders, with an example of each."""
    return [
        {
            'key': preset.key,
            'label': preset.label,
            'description': preset.description,
            'example_movie': (
                f'{preset.movie_names("Blade Runner", 1982)[0]}/'
                f'{preset.movie_names("Blade Runner", 1982)[1]}.mkv'
            ),
            'example_episode': (
                f'{preset.show_folder_name("Breaking Bad", 2008)}/'
                f'{preset.season_folder_name(1)}/'
                f'{preset.episode_file_stem("Breaking Bad", 1, 1, "Pilot")}.mkv'
            ),
        }
        for preset in PRESETS
    ]
