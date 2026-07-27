"""Public access: whether the app listens beyond this machine, and on what.

Kept separate from the sign-in code because the two answer different questions —
this one is about the socket, that one about the person. Sign-in becomes
mandatory only when public access is on, which is the single place the two meet.
"""

import ipaddress
import socket

from medialibrary import runtime

DEFAULT_SERVER_PORT = 5100

LOOPBACK_HOST = '127.0.0.1'

ALL_INTERFACES_HOST = '0.0.0.0'


def _public_access_enabled() -> bool:
    return (runtime.store().get_setting('public_access') or '0').strip() == '1'


def _server_port() -> int:
    raw = (runtime.store().get_setting('server_port') or '').strip()
    try:
        port = int(raw)
    except ValueError:
        return DEFAULT_SERVER_PORT
    return port if 1024 <= port <= 65535 else DEFAULT_SERVER_PORT


def _lan_ip_addresses() -> list[str]:
    """Private IPv4 addresses this machine holds, most likely LAN first.

    Every address is offered rather than one "best" guess: probing the outbound
    route returns the VPN tunnel address while a VPN is connected, and no device
    on the local network can reach that. Ordering is a heuristic only — home
    routers overwhelmingly hand out 192.168.x.x, while VPN clients tend to sit
    in 10.x.x.x — so the list is shown in full and the user picks.
    """
    try:
        candidates = {
            info[4][0] for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
        }
    except OSError:
        return []

    def rank(address: str) -> tuple[int, str]:
        if address.startswith('192.168.'):
            return 0, address
        if address.startswith('172.'):
            return 1, address
        return 2, address

    usable = []
    for raw in candidates:
        try:
            address = ipaddress.ip_address(raw)
        except ValueError:
            continue
        if address.is_private and not address.is_loopback and not address.is_link_local:
            usable.append(str(address))
    return sorted(usable, key=rank)


def _is_local_or_private_host(hostname: str | None) -> bool:
    host = (hostname or '').strip().lower()
    if not host:
        return False
    if host in {'localhost'}:
        return True
    try:
        addr = ipaddress.ip_address(host)
        return addr.is_loopback or addr.is_private
    except ValueError:
        return host.endswith('.local')


# What the server actually bound at startup. Host and port are only read when
# app.run() is called, so the UI compares these against the saved settings to
# tell you a restart is needed.
RUNNING_PORT = DEFAULT_SERVER_PORT
RUNNING_PUBLIC = False


def set_running(port: int, public: bool) -> None:
    """Record what was bound, so the settings page can spot a pending restart."""
    global RUNNING_PORT, RUNNING_PUBLIC
    RUNNING_PORT, RUNNING_PUBLIC = port, public
