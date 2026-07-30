import os

import pytest

from agents.master_chef import OllamaMasterChef
from core.config import Settings
from domain.recipe_options import RecipeOptionDraft, RecipePreferences

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.getenv("COOK_MANTRA_RUN_LIVE") != "1",
        reason="Set COOK_MANTRA_RUN_LIVE=1 to run the real Ollama Master Chef.",
    ),
]


@pytest.mark.asyncio
async def test_real_master_chef_returns_one_valid_structured_option() -> None:
    options = await OllamaMasterChef(settings=Settings(_env_file=None)).generate(
        ["Tomato", "Onion"],
        RecipePreferences(option_count=1),
        set(),
    )

    assert len(options) == 1
    assert RecipeOptionDraft.model_validate(options[0])
