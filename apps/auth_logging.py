"""Keep OAuth callback codes and state out of HTTP access logs."""

import logging


class AuthQueryFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not isinstance(record.args, tuple):
            return True
        args = list(record.args)
        # Uvicorn: (client, method, path, version, status); httpx: (method, URL, ...).
        index = 2 if record.name == "uvicorn.access" else 1
        if len(args) > index:
            target = str(args[index])
            path = target.partition("?")[0]
            if path.endswith(("/api/auth/callback", "/v1/auth/callback")):
                args[index] = path + ("?[redacted]" if "?" in target else "")
                record.args = tuple(args)
        return True


def protect_auth_logs() -> None:
    for name in ("uvicorn.access", "httpx"):
        logger = logging.getLogger(name)
        if not any(isinstance(item, AuthQueryFilter) for item in logger.filters):
            logger.addFilter(AuthQueryFilter())
