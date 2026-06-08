from __future__ import annotations

import hmac

from fastapi import Header

from app.core.config import PUBLISH_API_TOKEN


def unauthorized_response() -> dict:
    return {"code": 401, "message": "unauthorized", "data": {}}


def is_authorized_header(authorization: str | None) -> bool:
    if not PUBLISH_API_TOKEN:
        return False
    if not authorization:
        return False

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return False
    return hmac.compare_digest(token.strip(), PUBLISH_API_TOKEN)


async def require_api_token(authorization: str | None = Header(default=None)) -> dict | None:
    if is_authorized_header(authorization):
        return None
    return unauthorized_response()
