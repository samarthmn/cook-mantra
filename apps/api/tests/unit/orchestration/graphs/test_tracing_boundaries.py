from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

import pytest

from domain.sessions import SessionStage
from orchestration.graphs import (
    complete_recipes,
    ingredient_extraction,
    recipe_options,
)

_TRACING_ENABLED: ContextVar[bool] = ContextVar(
    "test_graph_auto_tracing_enabled",
    default=True,
)


@contextmanager
def recording_tracing_context(*, enabled: bool):
    token = _TRACING_ENABLED.set(enabled)
    try:
        yield
    finally:
        _TRACING_ENABLED.reset(token)


class BoundaryCheckingGraph:
    async def ainvoke(self, state: dict[str, object]) -> dict[str, object]:
        assert _TRACING_ENABLED.get() is False
        return state


async def ignore_progress(_value: int) -> None:
    return None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "module",
    [ingredient_extraction, recipe_options, complete_recipes],
)
async def test_real_graph_runner_disables_automatic_outer_tracing(
    module: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        module,
        "tracing_context",
        recording_tracing_context,
        raising=False,
    )
    if module is ingredient_extraction:
        monkeypatch.setattr(
            module,
            "build_ingredient_extraction_graph",
            lambda _dependencies: BoundaryCheckingGraph(),
        )
        dependencies = module.ExtractionDependencies(None, None, None, None)
        await module.run_ingredient_extraction(
            "session-1",
            "artifact-1",
            ignore_progress,
            dependencies=dependencies,
        )
    elif module is recipe_options:
        monkeypatch.setattr(
            module,
            "build_recipe_options_graph",
            lambda _dependencies: BoundaryCheckingGraph(),
        )
        dependencies = module.RecipeOptionDependencies(None, None, None, None, None)
        await module.run_recipe_options(
            "session-1",
            {},
            False,
            object(),
            ignore_progress,
            dependencies=dependencies,
        )
    else:
        monkeypatch.setattr(
            module,
            "build_complete_recipes_graph",
            lambda _dependencies: BoundaryCheckingGraph(),
        )
        dependencies = module.CompleteRecipeDependencies(None, None, None)
        context = type(
            "GenerationContext",
            (),
            {
                "selected_option_ids": (),
                "previous_stage": SessionStage.OPTIONS_READY,
            },
        )()
        await module.run_complete_recipes(
            "session-1",
            context,
            ignore_progress,
            dependencies=dependencies,
        )
