from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

DEFAULT_ERROR_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    409: "conflict",
    422: "validation_error",
    500: "internal_error",
    503: "service_unavailable",
}

DEFAULT_ERROR_MESSAGES = {
    400: "Bad request",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Resource not found",
    409: "Conflict",
    422: "Validation failed",
    500: "Internal server error",
    503: "Service unavailable",
}


def build_error_response(*, status_code: int, message: str, code: str | None = None, details=None) -> dict:
    return {
        "error": {
            "code": code or DEFAULT_ERROR_CODES.get(status_code, "unknown_error"),
            "message": message,
            "details": details,
        }
    }


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(HTTPException)
    async def http_exception_handler(_: Request, exc: HTTPException):
        if isinstance(exc.detail, str):
            message = exc.detail
            details = None
        else:
            message = DEFAULT_ERROR_MESSAGES.get(exc.status_code, "Request failed")
            details = exc.detail

        return JSONResponse(
            status_code=exc.status_code,
            headers=exc.headers,
            content=build_error_response(status_code=exc.status_code, message=message, details=details),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(_: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content=build_error_response(
                status_code=422,
                message=DEFAULT_ERROR_MESSAGES[422],
                details=exc.errors(),
            ),
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(_: Request, __: Exception):
        return JSONResponse(
            status_code=500,
            content=build_error_response(status_code=500, message=DEFAULT_ERROR_MESSAGES[500]),
        )