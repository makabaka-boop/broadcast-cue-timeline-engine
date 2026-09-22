"""Stable error codes returned by the HTTP API."""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .validation import ValidationFailure


class APIError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        detail: str,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.status_code = status_code
        self.code = code
        self.detail = detail
        self.extra = extra or {}
        super().__init__(detail)


def _envelope(code: str, detail: str, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    body: Dict[str, Any] = {"error": {"code": code, "detail": detail}}
    if extra:
        body["error"].update(extra)
    return body


def register_exception_handlers(app: Any) -> None:
    @app.exception_handler(ValidationFailure)
    async def _handle_validation_failure(
        request: Request, exc: ValidationFailure
    ) -> JSONResponse:
        return JSONResponse(
            status_code=400,
            content=_envelope(exc.code, exc.detail),
        )

    @app.exception_handler(APIError)
    async def _handle_api_error(request: Request, exc: APIError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=_envelope(exc.code, exc.detail, exc.extra),
        )

    @app.exception_handler(RequestValidationError)
    async def _handle_request_validation(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=400,
            content=_envelope(
                "invalid_json", "request body is not valid JSON or has the wrong type"
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _handle_http_exception(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        if exc.status_code == 404:
            code, detail = "not_found", "resource not found"
        elif exc.status_code == 405:
            code, detail = "method_not_allowed", "HTTP method not allowed"
        else:
            code, detail = "http_error", str(exc.detail)
        return JSONResponse(
            status_code=exc.status_code, content=_envelope(code, detail)
        )

    @app.exception_handler(json.JSONDecodeError)
    async def _handle_json_decode_error(
        request: Request, exc: json.JSONDecodeError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=400,
            content=_envelope("invalid_json", "request body is not valid JSON"),
        )

    @app.exception_handler(Exception)
    async def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=500,
            content=_envelope("internal_error", "unexpected server error"),
        )
