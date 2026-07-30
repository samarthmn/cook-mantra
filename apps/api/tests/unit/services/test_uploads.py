from io import BytesIO

import pytest
from fastapi import UploadFile
from PIL import Image

from core.errors import AppError, ErrorCode
from services.uploads import ImageUploadValidator


def image_bytes(image_format: str) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (8, 8), "red").save(buffer, format=image_format)
    return buffer.getvalue()


@pytest.mark.asyncio
async def test_validator_detects_type_from_bytes() -> None:
    upload = UploadFile(filename="../../wrong.jpg", file=BytesIO(image_bytes("PNG")))

    image = await ImageUploadValidator(max_bytes=1024).read(upload)

    assert image.media_type == "image/png"
    assert image.suffix == ".png"


@pytest.mark.asyncio
async def test_validator_rejects_oversized_image() -> None:
    upload = UploadFile(filename="large.png", file=BytesIO(image_bytes("PNG")))

    with pytest.raises(AppError, match="10 MiB"):
        await ImageUploadValidator(max_bytes=4).read(upload)


@pytest.mark.asyncio
async def test_validator_rejects_corrupt_image_bytes() -> None:
    upload = UploadFile(filename="photo.png", file=BytesIO(b"not an image"))

    with pytest.raises(AppError) as error:
        await ImageUploadValidator(max_bytes=1024).read(upload)

    assert error.value.code is ErrorCode.INVALID_REQUEST
    assert error.value.status_code == 422
    assert error.value.retryable is False


@pytest.mark.asyncio
async def test_validator_rejects_unsupported_image_format() -> None:
    upload = UploadFile(filename="photo.png", file=BytesIO(image_bytes("GIF")))

    with pytest.raises(AppError) as error:
        await ImageUploadValidator(max_bytes=1024).read(upload)

    assert error.value.code is ErrorCode.INVALID_REQUEST
    assert error.value.status_code == 422
    assert error.value.retryable is False


@pytest.mark.asyncio
async def test_validator_converts_pillow_decompression_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 1)
    upload = UploadFile(filename="photo.png", file=BytesIO(image_bytes("PNG")))

    with pytest.raises(AppError) as error:
        await ImageUploadValidator(max_bytes=1024).read(upload)

    assert error.value.code is ErrorCode.INVALID_REQUEST
    assert error.value.status_code == 422
    assert error.value.retryable is False
