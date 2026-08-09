"""Bounded streaming readers for untrusted provider HTTP response bodies."""

import json

import httpx

DISCOVERY_BODY_LIMIT = 4 * 1024 * 1024
ERROR_BODY_LIMIT = 64 * 1024
IMAGE_BODY_LIMIT = 16 * 1024 * 1024


class ResponseBodyError(Exception):
    """Signal a detached oversized or malformed provider response body."""


async def read_bounded_json_object(
    response: httpx.Response,
    *,
    success_limit: int,
) -> dict[str, object]:
    """Stream/count decompressed bytes and parse one JSON object safely."""
    is_success = response.status_code == 200
    limit = success_limit if is_success else ERROR_BODY_LIMIT
    declared = response.headers.get("content-length")
    if declared is not None:
        if not declared.isascii() or not declared.isdecimal():
            raise ResponseBodyError
        if int(declared) > limit:
            raise ResponseBodyError

    body = bytearray()
    try:
        async for chunk in response.aiter_bytes():
            if len(chunk) > limit - len(body):
                raise ResponseBodyError
            body.extend(chunk)
    except ResponseBodyError:
        raise
    except (httpx.DecodingError, UnicodeError, ValueError):
        raise ResponseBodyError from None

    if not body and not is_success:
        return {}
    try:
        parsed = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError, RecursionError):
        if not is_success:
            return {}
        raise ResponseBodyError from None
    if not isinstance(parsed, dict):
        if not is_success:
            return {}
        raise ResponseBodyError
    return parsed
