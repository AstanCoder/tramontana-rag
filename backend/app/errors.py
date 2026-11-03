from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.logging import current_trace_id, get_logger

log = get_logger(__name__)

_HTTP_CODES = {404: "not_found", 405: "method_not_allowed"}


class AppError(Exception):
    def __init__(self, code: str, message: str, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def _envelope(code: str, message: str, status_code: int) -> JSONResponse:
    body = {"error": {"code": code, "message": message, "trace_id": current_trace_id()}}
    return JSONResponse(body, status_code=status_code)


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    def _app_error(_: Request, exc: AppError) -> JSONResponse:
        return _envelope(exc.code, exc.message, exc.status_code)

    @app.exception_handler(RequestValidationError)
    def _validation_error(_: Request, __: RequestValidationError) -> JSONResponse:
        # Never echo the offending input: it may contain user content.
        return _envelope("validation_error", "La petición no es válida", 422)

    @app.exception_handler(StarletteHTTPException)
    def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _HTTP_CODES.get(exc.status_code, "http_error")
        return _envelope(code, str(exc.detail), exc.status_code)

    @app.exception_handler(Exception)
    def _unhandled_error(_: Request, exc: Exception) -> JSONResponse:
        log.error("unhandled_error", exc_type=type(exc).__name__)
        return _envelope("internal_error", "Error interno del servidor", 500)
