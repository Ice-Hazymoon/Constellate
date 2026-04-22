from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from fastapi import UploadFile

from .errors import HttpError

ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}


@dataclass(frozen=True, slots=True)
class UploadValidation:
    extension: str
    mime_type: str


def extract_allowed_image_extension(name: str) -> str | None:
    extension = Path(name).suffix.lower()
    if extension in {".jpg", ".jpeg", ".png", ".webp"}:
        return extension
    return None


def guess_extension(name: str, mime_type: str | None = None) -> str:
    extension = extract_allowed_image_extension(name)
    if extension:
        return extension
    if mime_type == "image/png":
        return ".png"
    if mime_type == "image/webp":
        return ".webp"
    return ".jpg"


def validate_image_upload(
    filename: str,
    mime_type: str | None,
    size: int,
    max_upload_bytes: int,
) -> UploadValidation:
    if size <= 0:
        raise HttpError(400, "uploaded file is empty")
    if size > max_upload_bytes:
        limit_mb = round(max_upload_bytes / (1024 * 1024))
        raise HttpError(413, f"image exceeds upload limit of {limit_mb} MB")

    normalized_mime_type = (mime_type or "").lower()
    extension = extract_allowed_image_extension(filename)
    if not extension and normalized_mime_type not in ALLOWED_IMAGE_TYPES:
        raise HttpError(415, "only JPG, PNG, and WebP images are supported")

    return UploadValidation(
        extension=guess_extension(filename, normalized_mime_type),
        mime_type=normalized_mime_type,
    )


async def save_upload_file(
    upload: UploadFile,
    destination: Path,
    max_upload_bytes: int,
    *,
    chunk_size: int = 1024 * 1024,
) -> UploadValidation:
    filename = upload.filename or ""
    normalized_mime_type = (upload.content_type or "").lower()
    extension = extract_allowed_image_extension(filename)
    if not extension and normalized_mime_type not in ALLOWED_IMAGE_TYPES:
        raise HttpError(415, "only JPG, PNG, and WebP images are supported")

    size = 0
    with destination.open("wb") as handle:
        while True:
            chunk = await upload.read(chunk_size)
            if not chunk:
                break
            size += len(chunk)
            if size > max_upload_bytes:
                limit_mb = round(max_upload_bytes / (1024 * 1024))
                raise HttpError(413, f"image exceeds upload limit of {limit_mb} MB")
            handle.write(chunk)

    return validate_image_upload(filename, normalized_mime_type, size, max_upload_bytes)
