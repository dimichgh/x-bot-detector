"""Load X session cookies (``auth_token`` + ``ct0``) from a string, file, env or a local browser."""

from __future__ import annotations

import json
import os
from pathlib import Path

REQUIRED = ("auth_token", "ct0")
BROWSERS = ("chrome", "chromium", "brave", "edge", "firefox", "safari", "opera", "vivaldi")

_ENV_PAIRS = (
    ("X_AUTH_TOKEN", "X_CT0"),
    ("AUTH_TOKEN", "CT0"),  # same names the `bird` CLI uses
    ("TWITTER_AUTH_TOKEN", "TWITTER_CT0"),
)


class CookieError(ValueError):
    pass


def parse_cookie_text(text: str) -> dict[str, str]:
    """Parse a ``Cookie:`` header, a JSON dict/list export, or a Netscape cookies.txt."""
    text = text.strip()
    if text.lower().startswith("cookie:"):
        text = text[7:].strip()
    if text.startswith("{") or text.startswith("["):
        data = json.loads(text)
        if isinstance(data, dict) and "cookies" in data:
            data = data["cookies"]
        if isinstance(data, list):
            return {str(c["name"]): str(c["value"]) for c in data if isinstance(c, dict) and "name" in c}
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items()}
        raise CookieError("unrecognised JSON cookie export")
    lines = [ln for ln in text.splitlines() if ln.strip() and not ln.startswith("#")]
    if lines and all(ln.count("\t") >= 6 for ln in lines):  # Netscape cookies.txt
        out: dict[str, str] = {}
        for ln in lines:
            parts = ln.split("\t")
            if any(d in parts[0] for d in ("x.com", "twitter.com")):
                out[parts[5]] = parts[6].strip()
        return out
    return {
        k.strip(): v.strip()
        for k, v in (p.split("=", 1) for p in text.replace("\n", ";").split(";") if "=" in p)
    }


def from_browser(browser: str) -> dict[str, str]:
    try:
        import browser_cookie3  # type: ignore[import-not-found]
    except ImportError as e:
        raise CookieError(
            "reading cookies from a browser needs the optional dependency: pip install 'x-bot-detector[browser]'"
        ) from e
    loader = getattr(browser_cookie3, browser.lower(), None)
    if loader is None:
        raise CookieError(f"unsupported browser {browser!r}; choose from {', '.join(BROWSERS)}")
    out: dict[str, str] = {}
    for domain in ("twitter.com", "x.com"):  # x.com last so it wins
        try:
            jar = loader(domain_name=domain)
        except Exception as e:  # browser_cookie3 raises many different errors
            raise CookieError(f"could not read {browser} cookies: {e}") from e
        out.update({c.name: c.value for c in jar if c.value})
    return out


def load_cookies(
    cookies: str | None = None,
    cookies_file: str | os.PathLike[str] | None = None,
    browser: str | None = None,
    env: dict[str, str] | None = None,
) -> dict[str, str] | None:
    """Return a cookie dict with ``auth_token`` and ``ct0``, or ``None`` if nothing was configured."""
    env = os.environ if env is None else env
    found: dict[str, str] | None = None
    if cookies:
        found = parse_cookie_text(cookies)
    elif cookies_file:
        found = parse_cookie_text(Path(cookies_file).expanduser().read_text())
    elif browser:
        found = from_browser(browser)
    elif env.get("X_COOKIES"):
        found = parse_cookie_text(env["X_COOKIES"])
    else:
        for tok, ct0 in _ENV_PAIRS:
            if env.get(tok) and env.get(ct0):
                found = {"auth_token": env[tok], "ct0": env[ct0]}
                break
    if found is None:
        return None
    missing = [k for k in REQUIRED if not found.get(k)]
    if missing:
        raise CookieError(f"X session cookies are missing {', '.join(missing)} (log in to x.com first)")
    return found


def cookie_header(cookies: dict[str, str]) -> str:
    return "; ".join(f"{k}={v}" for k, v in cookies.items())
