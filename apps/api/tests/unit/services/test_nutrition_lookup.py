import asyncio
import json

import httpx
import pytest

from core.errors import AppError, ErrorCode
from services.nutrition_lookup import (
    HttpNutritionLookup,
    NutritionLookupIngredient,
)


def nutrition_payload(*, unmatched: list[dict[str, str]] | None = None) -> dict:
    return {
        "totals": {
            "energy_kcal": 180,
            "protein_g": 6,
            "carbohydrates_g": 30,
            "sugars_g": 8,
            "fat_g": 4,
            "saturated_fat_g": 1,
            "fiber_g": 5,
            "salt_g": 0.4,
            "sodium_mg": 160,
        },
        "items": [
            {
                "name": "Tomato",
                "source_tier": "MEASURED",
                "confidence": 0.98,
                "match_type": "exact",
                "grams": 300,
            }
        ],
        "unmatched": unmatched or [],
        "warnings": [],
    }


@pytest.mark.asyncio
async def test_lookup_posts_gram_ingredients_and_parses_success() -> None:
    requests: list[httpx.Request] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=nutrition_payload())

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        lookup = HttpNutritionLookup(
            base_url="http://nutrition.test/root/",
            client=client,
        )
        result = await lookup.lookup(
            [NutritionLookupIngredient(name="Tomato", qty=300)]
        )

    assert result.totals.energy_kcal == 180
    assert result.items[0].confidence == 0.98
    assert requests[0].url.path == "/root/nutrition"
    assert "authorization" not in requests[0].headers
    assert json.loads(requests[0].content) == {
        "ingredients": [{"name": "Tomato", "qty": 300.0, "unit": "g"}]
    }


@pytest.mark.asyncio
async def test_lookup_preserves_unmatched_entries() -> None:
    async def respond(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=nutrition_payload(
                unmatched=[{"name": "Homemade masala", "reason": "no match"}]
            ),
        )

    lookup = HttpNutritionLookup(
        base_url="http://nutrition.test",
        transport=httpx.MockTransport(respond),
    )
    try:
        result = await lookup.lookup(
            [NutritionLookupIngredient(name="Homemade masala", qty=12)]
        )
    finally:
        await lookup.aclose()

    assert [(item.name, item.reason) for item in result.unmatched] == [
        ("Homemade masala", "no match")
    ]


@pytest.mark.asyncio
async def test_lookup_maps_server_errors_to_retryable_provider_error() -> None:
    async def respond(_: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"detail": "database loading"})

    lookup = HttpNutritionLookup(
        base_url="http://nutrition.test",
        transport=httpx.MockTransport(respond),
    )
    try:
        with pytest.raises(AppError) as raised:
            await lookup.lookup([NutritionLookupIngredient(name="Tomato", qty=100)])
    finally:
        await lookup.aclose()

    assert raised.value.code is ErrorCode.NUTRITION_PROVIDER_UNAVAILABLE
    assert raised.value.status_code == 503
    assert raised.value.retryable is True
    assert "database loading" not in str(raised.value)


@pytest.mark.asyncio
async def test_lookup_maps_overall_timeout_to_retryable_provider_error() -> None:
    async def respond(_: httpx.Request) -> httpx.Response:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    lookup = HttpNutritionLookup(
        base_url="http://nutrition.test",
        transport=httpx.MockTransport(respond),
        timeout_seconds=0.001,
    )
    try:
        with pytest.raises(AppError) as raised:
            await lookup.lookup([NutritionLookupIngredient(name="Tomato", qty=100)])
    finally:
        await lookup.aclose()

    assert raised.value.code is ErrorCode.OPERATION_TIMED_OUT
    assert raised.value.status_code == 504
    assert raised.value.retryable is True
    assert str(raised.value) == "Nutrition lookup timed out."
