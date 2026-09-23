"""Safe, portable connection details for saved test reports."""

import re
from urllib.parse import urlsplit, urlunsplit

MAC_ADDRESS = re.compile(r"(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}\Z")


def connection_details(device_id, url):
    """Retain the device MAC and WS endpoint without URL credentials or queries."""
    details = {}
    if isinstance(device_id, str) and MAC_ADDRESS.fullmatch(device_id):
        details["device_mac"] = device_id.upper()
    if isinstance(url, str):
        try:
            parts = urlsplit(url)
            if parts.scheme in {"ws", "wss"} and parts.hostname:
                # Userinfo and query strings can contain service credentials.
                host = parts.netloc.rsplit("@", 1)[-1]
                details["websocket_url"] = urlunsplit(
                    (parts.scheme, host, parts.path or "/", "", "")
                )
        except ValueError:
            pass
    return details
