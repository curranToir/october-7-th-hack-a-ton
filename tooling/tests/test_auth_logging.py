import logging

import pytest

from apps.auth_logging import AuthQueryFilter


@pytest.mark.parametrize(
    "logger,args",
    [
        (
            "uvicorn.access",
            ("127.0.0.1", "GET", "/api/auth/callback?code=secret&state=secret", "1.1", 303),
        ),
        (
            "httpx",
            (
                "GET",
                "http://coordinator/v1/auth/callback?code=secret&state=secret",
                "HTTP/1.1",
                303,
                "See Other",
            ),
        ),
    ],
)
def test_codes_are_redacted_but_http_status_is_preserved(logger, args):
    record = logging.LogRecord(logger, logging.INFO, "", 1, "%s %s %s %s %s", args, None)
    assert AuthQueryFilter().filter(record)
    output = record.getMessage()
    assert "secret" not in output and "[redacted]" in output and "303" in output
    assert "auth/callback" in output


def test_non_auth_access_logs_are_unchanged():
    args = ("127.0.0.1", "GET", "/api/sales/workspace", "1.1", 200)
    record = logging.LogRecord("uvicorn.access", logging.INFO, "", 1, "%s %s %s %s %s", args, None)
    AuthQueryFilter().filter(record)
    assert record.args == args
