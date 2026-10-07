"""The only browser authentication material forwarded to the coordinator."""

import re
from urllib.parse import urlsplit

from fastapi import Request

COOKIE_VALUE = re.compile(r"^[A-Za-z0-9_-]{1,256}$")


def browser_headers(request: Request | None, *, include_flow: bool = False) -> dict[str, str]:
    cookie_names = ["toir_session", "toir_auth_flow"] if include_flow else ["toir_session"]
    cookies = (
        []
        if request is None
        else [
            f"{name}={request.cookies[name]}"
            for name in cookie_names
            if COOKIE_VALUE.fullmatch(request.cookies.get(name, ""))
        ]
    )
    # Always override httpx's shared cookie jar, including for anonymous requests.
    headers = {"Cookie": "; ".join(cookies), "Accept": "application/json"}
    origin = request.headers.get("origin", "") if request else ""
    try:
        parsed = urlsplit(origin)
        if (
            len(origin) < 2048
            and parsed.scheme in {"http", "https"}
            and parsed.netloc
            and not parsed.username
            and not parsed.password
            and not parsed.path
            and not parsed.query
            and not parsed.fragment
        ):
            headers["Origin"] = origin
    except ValueError:
        pass  # The coordinator rejects missing or invalid Origin on mutations.
    return headers
