from __future__ import annotations

import hmac

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import PUBLISH_API_TOKEN

bearer_scheme = HTTPBearer(auto_error=False)


def is_authorized_token(token: str | None) -> bool:
    if not PUBLISH_API_TOKEN or not token:
        return False
    return hmac.compare_digest(token.strip(), PUBLISH_API_TOKEN)


async def require_api_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> str:
    """Router-level dependency: enforce the Bearer token, raising a real HTTP 401
    (with WWW-Authenticate) on failure instead of returning a 200 body."""
    if credentials and is_authorized_token(credentials.credentials):
        return credentials.credentials
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="unauthorized",
        headers={"WWW-Authenticate": "Bearer"},
    )
