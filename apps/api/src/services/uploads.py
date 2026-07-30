"""Validation for uploaded ingredient images."""

from dataclasses import dataclass
from io import BytesIO

from fastapi import UploadFile
from PIL import Image

from core.errors import AppError, ErrorCode

IMAGE_FORMATS = {
    "JPEG": ("image/jpeg", ".jpg"),
    "PNG": ("image/png", ".png"),
    "WEBP": ("image/webp", ".webp"),
}


@dataclass(frozen=True, slots=True)
class ValidatedImage:
    """An image whose bytes and media type have been validated."""

    data: bytes
    media_type: str
    suffix: str


class ImageUploadValidator:
    """Read a bounded upload and validate its image contents."""

    def __init__(self, max_bytes: int) -> None:
        self.max_bytes = max_bytes

    async def read(self, upload: UploadFile) -> ValidatedImage:
        data = await upload.read(self.max_bytes + 1)
        if len(data) > self.max_bytes:
            raise _invalid_upload("Image uploads must be no larger than 10 MiB.")

        try:
            with Image.open(BytesIO(data)) as image:
                image.verify()
                detected_format = image.format
        except (Image.DecompressionBombError, OSError, SyntaxError, ValueError):
            raise _invalid_upload("Upload must be a valid image.") from None

        try:
            media_type, suffix = IMAGE_FORMATS[detected_format]
        except KeyError:
            raise _invalid_upload("Image format must be JPEG, PNG, or WebP.") from None

        return ValidatedImage(data=data, media_type=media_type, suffix=suffix)


def _invalid_upload(message: str) -> AppError:
    return AppError(
        code=ErrorCode.INVALID_REQUEST,
        message=message,
        status_code=422,
        retryable=False,
    )
