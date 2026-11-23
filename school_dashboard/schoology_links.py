"""Resolve assignment links against the household's configured Schoology portal."""
import json
import os
import re
import shlex
from ipaddress import IPv6Address
from pathlib import Path
from urllib.parse import urlsplit

from school_dashboard.assignments.readmodel import safe_url

_HOST_LABEL = re.compile(r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?")


def _http_url(value: object) -> str:
    if (not isinstance(value, str) or not value or "\\" in value
            or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value)):
        return ""
    try:
        parts = urlsplit(value)
        host = parts.hostname
        port = parts.port
        if host and ":" in host:
            IPv6Address(host)
    except ValueError:
        return ""
    if not host or port == 0 or parts.netloc.endswith(":"):
        return ""
    host = host.removesuffix(".")
    if host == "api.schoology.com" or (":" not in host and not all(_HOST_LABEL.fullmatch(label)
                                                                 for label in host.split("."))):
        return ""
    return safe_url(value) or ""


def _origin(value: object) -> str:
    url = _http_url(value)
    if not url:
        return ""
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def _env_file_origin(path: Path) -> str:
    try:
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                match = re.fullmatch(r"\s*(?:export\s+)?SGY_BASE_URL\s*=\s*(.*?)\s*", line)
                if match:
                    words = shlex.split(match[1], comments=True)
                    return _origin(words[0]) if len(words) == 1 else ""
    except (OSError, ValueError):
        pass
    return ""


def portal_origin() -> str:
    for key in ("SGY_PORTAL_URL", "SGY_BASE_URL"):
        origin = _origin(os.environ.get(key))
        if origin:
            return origin
    config_dir = Path.home() / ".sgy"
    origin = _env_file_origin(config_dir / ".env")
    if origin:
        return origin
    try:
        config = json.loads((config_dir / "config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    return _origin(config.get("base_url")) if isinstance(config, dict) else ""


def schoology_url(link: object, origin: str) -> str:
    if isinstance(link, str) and link.startswith("/") and not link.startswith("//"):
        return _http_url(origin + link) if origin else ""
    return _http_url(link)
