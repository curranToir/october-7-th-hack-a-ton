"""Shared bearer boundary; credentials and provider responses never enter errors."""

import hmac
import os
from typing import Annotated

from fastapi import Header, HTTPException


async def bearer(authorization: Annotated[str | None, Header()] = None):
    expected = os.environ.get("BRAIN_API_TOKEN", "")
    if (
        not expected
        or authorization is None
        or not hmac.compare_digest(
            authorization.encode(),
            f"Bearer {expected}".encode(),
        )
    ):
        raise HTTPException(401, "invalid_token")
