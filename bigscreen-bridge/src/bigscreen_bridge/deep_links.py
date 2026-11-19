"""One bounded URI format that resolves to the existing typed adapter commands."""

import re
from urllib.parse import parse_qsl, unquote, urlsplit

from .adapters import APPS, BridgeError
from .couchside import validate_content as validate_music
from .flimmer import validate_channel
from .stremio import validate_content as validate_stremio
from .vacuumtube import validate_content as validate_youtube


def parse_uri(uri):
    if (
        not isinstance(uri, str)
        or not uri
        or len(uri) > 8192
        or "#" in uri
        or any(ord(c) < 32 or ord(c) == 127 for c in uri)
        or re.search(r"%(?![0-9A-Fa-f]{2})", uri)
    ):
        raise BridgeError("invalid_request", "Invalid Bigscreen URI")
    try:
        parsed = urlsplit(uri)
        app = parsed.netloc
        if parsed.scheme != "bigscreen" or app not in {a.id for a in APPS}:
            raise BridgeError("invalid_request", "Use a bigscreen:// URI with a supported app ID")
        parts = parsed.path[1:].split("/") if parsed.path.startswith("/") else []
        if parts == [""]:
            parts = []
        if any(not part for part in parts):
            raise BridgeError("invalid_request", "Empty URI path segments are unsupported")
        parts = [unquote(part, errors="strict") for part in parts]
        pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True, errors="strict")
        query = dict(pairs)
        if len(query) != len(pairs):
            raise BridgeError("invalid_request", "Duplicate URI parameters are unsupported")
        if not parts:
            if query:
                raise BridgeError("invalid_request", "Focus URIs do not accept parameters")
            return {"command": "focus", "app": app}
        kind, *values = parts
        if query.keys() - ({"resume"} if app == "stremio" else set()):
            raise BridgeError("invalid_request", "Unsupported URI parameters")
        result = {"command": "open", "app": app, "content_type": kind}
        if app == "stremio":
            if query.get("resume", "false") not in ("true", "false"):
                raise BridgeError("invalid_request", "Resume must be true or false")
            expected = 2 if kind == "episode" else 1
            if len(values) != expected:
                raise BridgeError("invalid_request", "Invalid Stremio URI path")
            value = {"series_id": values[0], "video_id": values[1]} if kind == "episode" else values[0]
            validate_stremio(kind, value, query.get("resume") == "true")
            if "resume" in query:
                result["resume"] = query["resume"] == "true"
        elif app == "couchside":
            if len(values) > 1:
                raise BridgeError(
                    "invalid_request", "Encode the whole Music Assistant URI as one path segment"
                )
            value = values[0] if values else ""
            validate_music(kind, value)
        elif app == "flimmer":
            if kind != "channel" or len(values) != 1:
                raise BridgeError("unsupported", "Flimmer links require a channel ID")
            value = validate_channel(values[0])
        else:
            if len(values) != 1:
                raise BridgeError("invalid_request", "YouTube links require one content ID or search text")
            value = values[0]
            validate_youtube(kind, value)
        return {**result, "content_id": value}
    except (ValueError, UnicodeError) as exc:
        raise BridgeError("invalid_request", "Invalid Bigscreen URI encoding") from exc
