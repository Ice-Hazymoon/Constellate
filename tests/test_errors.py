from __future__ import annotations

from star_server.errors import HttpError, exception_to_response


def test_http_error_maps_to_explicit_status_and_message() -> None:
    response = exception_to_response(HttpError(400, "bad input"))
    assert response.status_code == 400
    assert response.body == b'{"error":"bad input"}'


def test_plate_solve_errors_map_to_422() -> None:
    response = exception_to_response(RuntimeError("plate solving aborted after 30.0s"))
    assert response.status_code == 422
    assert response.body == b'{"error":"plate solving aborted after 30.0s","code":"plate_solve_failed"}'


def test_unknown_errors_map_to_internal_server_error() -> None:
    response = exception_to_response(RuntimeError("boom"))
    assert response.status_code == 500
    assert response.body == b'{"error":"internal server error"}'
