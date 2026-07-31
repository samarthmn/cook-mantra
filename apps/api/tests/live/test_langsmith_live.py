import os
from uuid import uuid4

import pytest
from langsmith import Client, traceable

from core.config import Settings
from services.tracing import TracingService

pytestmark = pytest.mark.live


def test_configured_langsmith_project_accepts_a_safe_dummy_trace() -> None:
    if os.getenv("COOK_MANTRA_RUN_LANGSMITH_LIVE") != "1":
        pytest.skip("Set COOK_MANTRA_RUN_LANGSMITH_LIVE=1 to run the LangSmith smoke.")

    settings = Settings(_env_file=None)
    tracing = TracingService(settings)
    if not tracing.enabled or settings.langsmith_api_key is None:
        pytest.skip("LangSmith tracing is not enabled with credentials.")

    client = Client(api_key=settings.langsmith_api_key.get_secret_value())
    run_id = uuid4()

    @traceable(
        name=f"cook-mantra-live-smoke-{run_id}",
        run_type="chain",
        project_name=settings.langsmith_project,
        client=client,
    )
    def safe_dummy_trace(value: str) -> str:
        return value

    assert (
        safe_dummy_trace(
            "cook-mantra-safe-live-smoke",
            langsmith_extra={"run_id": run_id},
        )
        == "cook-mantra-safe-live-smoke"
    )
    client.flush()

    stored = client.read_run(run_id)
    assert stored.id == run_id
