"""Access to the application's long-lived objects, without importing it.

The route modules and the extracted logic both need the store, the TMDB client
and the qBittorrent search client. Importing `app` to reach them would be
circular, since `app` imports these modules to register them.

So the application hands over *getters* at startup. Getters rather than the
objects themselves, for two reasons learnt the hard way: `tmdb` is rebuilt
whenever the API key changes, and the tests swap the store wholesale — either
would leave a module holding something the rest of the app has replaced.
"""

_getters: dict = {}


def configure(**getters) -> None:
    """Called once by the application, after its objects exist."""
    _getters.update(getters)


def _get(name):
    getter = _getters.get(name)
    if getter is None:
        raise RuntimeError(
            f'medialibrary.runtime was never given a {name} getter; '
            'the application calls configure() at startup'
        )
    return getter()


def store():
    return _get('store')


def tmdb():
    """The TMDB client, or None when no API key is configured."""
    return _getters['tmdb']() if 'tmdb' in _getters else None


def qb():
    return _get('qb')
