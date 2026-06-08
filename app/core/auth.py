from __future__ import annotations

import hmac

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import PUBLISH_API_TOKEN

bearer_scheme = HTTPBearer(auto_error=False)


def unauthorized_response() -> dict:
    return {"code": 401, "message": "unauthorized", "data": {}}


def is_authorized_token(token: str | None) -> bool:
    if not PUBLISH_API_TOKEN:
        return False
    if not token:
        return False
    return hmac.compare_digest(token.strip(), PUBLISH_API_TOKEN)


async def require_api_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> dict | None:
    if credentials and is_authorized_token(credentials.credentials):
        return None
    return unauthorized_response()
