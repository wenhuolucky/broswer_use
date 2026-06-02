"""
Request middleware - injects unique request_id, tracks elapsed time
"""
import time
import uuid
from contextvars import ContextVar

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

# Global context vars for passing request_id between middleware and routes
request_id_var: ContextVar[str] = ContextVar("request_id", default="")
request_start_var: ContextVar[float] = ContextVar("request_start", default=0.0)


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Inject unique request_id per request, track elapsed time"""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = str(uuid.uuid4())
        start_time = time.time()

        # Inject into context vars
        request_id_var.set(request_id)
        request_start_var.set(start_time)

        # Call next handler
        response = await call_next(request)

        # Add response headers
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Response-Time"] = f"{time.time() - start_time:.3f}s"

        # Log
        from publish_service.logger_config import get_service_logger
        logger = get_service_logger()
        logger.info(
            f"[HTTP] {request.method} {request.url.path} | "
            f"status={response.status_code} | "
            f"elapsed={time.time() - start_time:.3f}s | "
            f"request_id={request_id}"
        )

        return response
