from __future__ import annotations

from fastapi.responses import JSONResponse


class HttpError(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code


PLATE_SOLVE_FAILURE_MARKERS = (
    "plate solving aborted",
    "plate solving failed",
)


def is_plate_solve_failure_message(message: str) -> bool:
    lower = message.lower()
    return any(marker in lower for marker in PLATE_SOLVE_FAILURE_MARKERS)


def create_request_aborted_error() -> HttpError:
    return HttpError(499, "request aborted")


def exception_to_response(error: Exception) -> JSONResponse:
    if isinstance(error, HttpError):
        return JSONResponse({"error": str(error)}, status_code=error.status_code)

    message = str(error)
    if is_plate_solve_failure_message(message):
        return JSONResponse(
            {"error": message, "code": "plate_solve_failed"},
            status_code=422,
        )

    return JSONResponse({"error": "internal server error"}, status_code=500)
