"""HMAC-signed media URLs: /media/<path>?exp=<unix seconds>&sig=<hex>.

The signature covers the path and the expiry, so a URL can't be retargeted or extended.
Never log these URLs.
"""

import hashlib
import hmac
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlsplit

DEFAULT_TTL = 4 * 3600


def _sig(secret: str, path: str, exp: int) -> str:
    return hmac.new(secret.encode(), f"{path}\n{exp}".encode(), hashlib.sha256).hexdigest()


def sign(secret: str, path: str, now: float, ttl: int = DEFAULT_TTL) -> str:
    exp = int(now) + ttl
    return f"/media/{quote(path)}?exp={exp}&sig={_sig(secret, path, exp)}"


def verify(secret: str, path: str, exp: int, sig: str, now: float) -> bool:
    if exp < now:
        return False
    return hmac.compare_digest(_sig(secret, path, exp), sig)


def verify_uri(secret: str, uri: str, now: float) -> bool:
    """Check a whole signed URI (`/media/<path>?exp=..&sig=..`), as Caddy forwards it."""
    parts = urlsplit(uri)
    if not parts.path.startswith("/media/"):
        return False
    query = parse_qs(parts.query)
    exp, sig = query.get("exp", []), query.get("sig", [])
    if len(exp) != 1 or len(sig) != 1 or not exp[0].isdigit():
        return False
    return verify(secret, unquote(parts.path.removeprefix("/media/")), int(exp[0]), sig[0], now)


def resolve(root: Path, path: str) -> Path | None:
    """The file under `root`, or None if the path escapes it or doesn't exist."""
    base = root.resolve()
    target = (base / path).resolve()
    if not target.is_relative_to(base) or not target.is_file():
        return None
    return target
