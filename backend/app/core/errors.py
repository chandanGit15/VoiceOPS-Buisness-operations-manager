"""
Custom API exceptions and centralized error handling.

Every expected business error raises an APIError; the app registers handlers
in register_error_handlers() so all clients receive the same JSON envelope:

    {"status": "error", "error": {"code": ..., "message": ..., "details": ...}}
"""

from datetime import datetime, timezone

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class APIError(Exception):
    """Base class for expected business errors surfaced to API clients."""

    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    code: str = "internal_error"
    message: str = "An unexpected error occurred."

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        status_code: int | None = None,
        details: dict | None = None,
    ) -> None:
        if message:
            self.message = message
        if code:
            self.code = code
        if status_code:
            self.status_code = status_code
        self.details = details
        super().__init__(self.message)


class NotFoundError(APIError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"
    message = "Resource not found."


def error_payload(code: str, message: str, details: dict | None = None, request_id: str | None = None) -> dict:
    """Build the standard error envelope used by all handlers."""
    body: dict = {
        "status": "error",
        "error": {
            "code": code,
            "message": message,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    }
    if request_id:
        body["error"]["request_id"] = request_id
    if details:
        body["error"]["details"] = details
    return body


def register_error_handlers(app: FastAPI) -> None:
    """Attach centralized exception handlers to the FastAPI app."""

    @app.exception_handler(APIError)
    async def handle_api_error(request: Request, exc: APIError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=error_payload(exc.code, exc.message, exc.details,
                                  request_id=request.headers.get("X-Request-ID")),
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        # Preserve structured detail (code/message) raised by internal raise-sites;
        # fall back to generic codes for plain HTTP errors.
        code, message, details = "http_error", "Request failed.", None
        if isinstance(exc.detail, dict):
            code = str(exc.detail.get("code", code))
            message = str(exc.detail.get("message", message))
            extra = {k: v for k, v in exc.detail.items() if k not in ("code", "message")}
            if extra:
                details = extra
        elif isinstance(exc.detail, str):
            code = "not_found" if exc.status_code == 404 else "http_error"
            message = exc.detail
        return JSONResponse(
            status_code=exc.status_code,
            content=error_payload(code, message, details,
                                  request_id=request.headers.get("X-Request-ID")),
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=error_payload(
                "validation_error",
                "Request payload failed validation.",
                details=jsonable_encoder(exc.errors()),
                request_id=request.headers.get("X-Request-ID"),
            ),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        # Log with traceback, but keep the response generic so internals
        # (stack frames, library names, config) never leak to clients.
        # The request id ties the client-visible error to this log line.
        import logging

        request_id = request.headers.get("X-Request-ID", "-")
        logging.getLogger("app.errors").exception(
            "request_id=%s unhandled exception: %s", request_id, exc
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=error_payload(
                "internal_error", "An unexpected error occurred.",
                request_id=request_id if request_id != "-" else None,
            ),
        )
