"""Task 2A adapter seam for the local Codex app-server owner."""

from collections.abc import Callable, Sequence
from typing import Protocol

from pydantic import BaseModel

from domain.model_runtime import ModelMessage, ModelResult, ProviderDiscoveryResult


class CodexStructuredInvoker(Protocol):
    """Narrow invocation surface shared with the app-server owner."""

    async def invoke[OutputT: BaseModel](
        self,
        messages: Sequence[ModelMessage],
        schema: type[OutputT],
        *,
        model: str,
        timeout_seconds: float,
    ) -> ModelResult[OutputT]: ...

    async def invoke_admitted[OutputT: BaseModel](
        self,
        messages: Sequence[ModelMessage],
        schema: type[OutputT],
        *,
        model: str,
        timeout_seconds: float,
        admit: Callable[[ProviderDiscoveryResult], None],
    ) -> ModelResult[OutputT]: ...


class CodexOwner(CodexStructuredInvoker, Protocol):
    """One object owns Codex discovery, invocation, and shutdown."""

    async def inspect(self) -> ProviderDiscoveryResult: ...

    async def aclose(self) -> None: ...


class CodexStructuredAdapter:
    """Bind one exact configured model and timeout to the shared client."""

    def __init__(
        self,
        client: CodexStructuredInvoker,
        *,
        model: str,
        timeout_seconds: float,
    ) -> None:
        self._client = client
        self._model = model
        self._timeout_seconds = timeout_seconds

    async def invoke[OutputT: BaseModel](
        self,
        messages: Sequence[ModelMessage],
        schema: type[OutputT],
    ) -> ModelResult[OutputT]:
        return await self._client.invoke(
            messages,
            schema,
            model=self._model,
            timeout_seconds=self._timeout_seconds,
        )

    async def invoke_admitted[OutputT: BaseModel](
        self,
        messages: Sequence[ModelMessage],
        schema: type[OutputT],
        *,
        admit: Callable[[ProviderDiscoveryResult], None],
    ) -> ModelResult[OutputT]:
        return await self._client.invoke_admitted(
            messages,
            schema,
            model=self._model,
            timeout_seconds=self._timeout_seconds,
            admit=admit,
        )
