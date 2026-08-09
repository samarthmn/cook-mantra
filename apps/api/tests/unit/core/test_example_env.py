"""Keep example.env honest about what the application actually reads."""

import re
from pathlib import Path

from core.config import PROJECT_ROOT, Settings

EXAMPLE_ENV = PROJECT_ROOT / "example.env"

# Read straight from the environment by tests rather than by Settings, so they
# are legitimately absent from the Settings field list.
TEST_ONLY_VARIABLES = frozenset(
    {
        "COOK_MANTRA_RUN_LIVE",
        "COOK_MANTRA_RUN_LANGSMITH_LIVE",
    }
)

# Provider secret names are selected by validated runtime YAML and read directly
# from the process environment, so they deliberately are not fixed Settings fields.
RUNTIME_CONFIG_SECRET_VARIABLES = frozenset({"OPENROUTER_API_KEY"})

# Values that differ between machines. Everything else is a product decision
# that defaults from a constant in core.config, so it must not be listed.
ENVIRONMENT_SPECIFIC_VARIABLES = frozenset(
    {
        "LOG_LEVEL",
        "CORS_ORIGINS",
        "ARTIFACT_ROOT",
        "LANGSMITH_TRACING",
        "LANGSMITH_API_KEY",
        "LANGSMITH_PROJECT",
        *RUNTIME_CONFIG_SECRET_VARIABLES,
    }
)

_ASSIGNMENT = re.compile(r"^\s*(?:#\s*)?([A-Z][A-Z0-9_]*)\s*=", re.MULTILINE)


def documented_variables() -> set[str]:
    """Return every variable example.env mentions, commented out or not."""
    return set(_ASSIGNMENT.findall(EXAMPLE_ENV.read_text(encoding="utf-8")))


def test_every_documented_variable_is_actually_read() -> None:
    """A variable nothing reads is a lie that outlives whoever added it."""
    settings_fields = {name.upper() for name in Settings.model_fields}
    unread = (
        documented_variables()
        - settings_fields
        - TEST_ONLY_VARIABLES
        - RUNTIME_CONFIG_SECRET_VARIABLES
    )

    assert not unread, (
        f"example.env documents {sorted(unread)}, which no Settings field and no "
        f"test reads. Remove them or wire them up."
    )


def test_tuning_values_are_not_pushed_into_the_environment_file() -> None:
    """Product decisions belong in core.config, not in every operator's .env."""
    documented = documented_variables() - TEST_ONLY_VARIABLES
    unexpected = documented - ENVIRONMENT_SPECIFIC_VARIABLES

    assert not unexpected, (
        f"example.env documents tuning values {sorted(unexpected)}. Give them a "
        f"constant in core.config instead, or add them to "
        f"ENVIRONMENT_SPECIFIC_VARIABLES if they genuinely vary per machine."
    )


def test_example_does_not_document_retired_image_provider_variables() -> None:
    documented = documented_variables()

    assert {
        "BEAST_BASE_URL",
        "BEAST_API_KEY",
        "DISH_PREVIEWS_ENABLED",
        "IMAGE_GEN_TARGET",
        "OLLAMA_IMAGE_LOCAL_URL",
        "OLLAMA_IMAGE_REMOTE_URL",
    }.isdisjoint(documented)


def test_runtime_provider_secret_is_referenced_by_name_in_yaml() -> None:
    runtime_example = (PROJECT_ROOT / "config" / "cook-mantra.example.yaml").read_text(
        encoding="utf-8"
    )

    for variable in RUNTIME_CONFIG_SECRET_VARIABLES:
        assert f"api_key_env: {variable}" in runtime_example


def test_browser_variables_are_documented_where_next_actually_loads_them() -> None:
    """Next.js only reads env files under apps/web, never the repo root."""
    assert not any(
        name.startswith("NEXT_PUBLIC_") for name in documented_variables()
    ), "NEXT_PUBLIC_* belongs in apps/web/.env.example; Next never loads this file."

    web_example = Path(PROJECT_ROOT / "apps" / "web" / ".env.example")
    assert "NEXT_PUBLIC_COOK_MANTRA_API_URL" in web_example.read_text(encoding="utf-8")
