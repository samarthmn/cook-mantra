"""HTTP adapter for ingredient-level nutrition lookup."""

import asyncio
from collections.abc import Sequence
from types import TracebackType
from typing import Literal, Protocol, Self

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

from core.errors import AppError, ErrorCode


class NutritionLookupIngredient(BaseModel):
    """One whole-dish ingredient normalized to grams."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    name: str
    qty: float = Field(gt=0)
    unit: Literal["g"] = "g"

    @field_validator("name")
    @classmethod
    def validate_name(cls, name: str) -> str:
        normalized = " ".join(name.split())
        if not normalized:
            raise ValueError("Ingredient names must not be blank.")
        return normalized


class NutritionTotals(BaseModel):
    """Nutrients returned for the matched ingredients in a whole dish."""

    model_config = ConfigDict(frozen=True, extra="ignore", allow_inf_nan=False)

    energy_kcal: float = Field(ge=0)
    protein_g: float = Field(ge=0)
    carbohydrates_g: float = Field(ge=0)
    sugars_g: float = Field(ge=0)
    fat_g: float = Field(ge=0)
    saturated_fat_g: float = Field(ge=0)
    fiber_g: float = Field(ge=0)
    salt_g: float = Field(ge=0)
    sodium_mg: float = Field(ge=0)


class NutritionLookupItem(BaseModel):
    """Matched ingredient provenance returned by the lookup service."""

    model_config = ConfigDict(frozen=True, extra="ignore", allow_inf_nan=False)

    name: str
    source_tier: Literal["MEASURED", "INHERITED"]
    confidence: float = Field(ge=0, le=1)
    match_type: Literal["exact", "fuzzy"]
    grams: float = Field(gt=0)


class UnmatchedNutritionIngredient(BaseModel):
    """An ingredient the lookup service could not resolve."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    name: str
    reason: str


class NutritionLookupResult(BaseModel):
    """Validated response from the nutrition service."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    totals: NutritionTotals
    items: tuple[NutritionLookupItem, ...] = ()
    unmatched: tuple[UnmatchedNutritionIngredient, ...] = ()
    warnings: tuple[str, ...] = ()


class NutritionLookup(Protocol):
    """Look up whole-dish nutrition for gram-normalized ingredients."""

    async def lookup(
        self,
        ingredients: Sequence[NutritionLookupIngredient],
    ) -> NutritionLookupResult:
        raise NotImplementedError


class HttpNutritionLookup:
    """Call the local nutrition service through one owned or injected client."""

    def __init__(
        self,
        *,
        base_url: str,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if client is not None and transport is not None:
            raise ValueError("transport cannot be supplied with an injected client")

        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._owns_client = client is None
        self._client = (
            client
            if client is not None
            else httpx.AsyncClient(timeout=timeout_seconds, transport=transport)
        )
        self._closed = False

    async def lookup(
        self,
        ingredients: Sequence[NutritionLookupIngredient],
    ) -> NutritionLookupResult:
        """Return validated nutrition totals within one overall timeout."""
        try:
            async with asyncio.timeout(self._timeout_seconds):
                response = await self._client.post(
                    f"{self._base_url}/nutrition",
                    json={
                        "ingredients": [
                            ingredient.model_dump(mode="json")
                            for ingredient in ingredients
                        ]
                    },
                )
                response.raise_for_status()
                return NutritionLookupResult.model_validate(response.json())
        except (httpx.TimeoutException, TimeoutError) as error:
            raise AppError(
                code=ErrorCode.OPERATION_TIMED_OUT,
                message="Nutrition lookup timed out.",
                status_code=504,
                retryable=True,
            ) from error
        except (
            httpx.HTTPStatusError,
            httpx.RequestError,
            UnicodeError,
            ValueError,
        ) as error:
            raise _provider_unavailable() from error

    async def aclose(self) -> None:
        """Close only the HTTP client created by this adapter."""
        if self._owns_client and not self._closed:
            await self._client.aclose()
            self._closed = True

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.aclose()


class UnavailableNutritionLookup:
    """Fail safely when nutrition lookup is not configured."""

    async def lookup(
        self,
        ingredients: Sequence[NutritionLookupIngredient],
    ) -> NutritionLookupResult:
        raise _provider_unavailable()


def _provider_unavailable(
    message: str = "The nutrition provider is unavailable.",
) -> AppError:
    return AppError(
        code=ErrorCode.NUTRITION_PROVIDER_UNAVAILABLE,
        message=message,
        status_code=503,
        retryable=True,
    )
