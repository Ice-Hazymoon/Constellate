from __future__ import annotations

import pytest

from star_server.errors import HttpError
from star_server.uploads import guess_extension, validate_image_upload


def test_guess_extension_falls_back_from_mime_type() -> None:
    assert guess_extension("mystery", "image/png") == ".png"
    assert guess_extension("mystery", "image/webp") == ".webp"
    assert guess_extension("night.jpeg", "image/jpeg") == ".jpeg"


def test_validate_image_upload_accepts_supported_images() -> None:
    result = validate_image_upload("night-sky.jpeg", "image/jpeg", 32, 1024)
    assert result.extension == ".jpeg"
    assert result.mime_type == "image/jpeg"


def test_validate_image_upload_rejects_unsupported_images() -> None:
    with pytest.raises(HttpError) as exc_info:
        validate_image_upload("notes.txt", "text/plain", 4, 1024)
    assert exc_info.value.status_code == 415


def test_validate_image_upload_enforces_upload_limit() -> None:
    with pytest.raises(HttpError) as exc_info:
        validate_image_upload("night-sky.jpg", "image/jpeg", 2048, 1024)
    assert exc_info.value.status_code == 413
