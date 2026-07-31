from typing import Any

from core.config import PROJECT_ROOT, Settings
from services.tracing import TracingService


class RecordingTraceClient:
    otel_exporter = None
    tracing_sample_rate = None

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def create_run(self, **kwargs: Any) -> None:
        self.calls.append(("create", kwargs))

    def update_run(self, **kwargs: Any) -> None:
        self.calls.append(("update", kwargs))


def enabled_tracing(
    client: RecordingTraceClient | None = None,
) -> tuple[TracingService, RecordingTraceClient]:
    resolved_client = client or RecordingTraceClient()

    def factory(**_kwargs: object) -> RecordingTraceClient:
        return resolved_client

    tracing = TracingService(
        Settings(
            _env_file=None,
            artifact_root=PROJECT_ROOT / "tmp" / "tracing-agent-test",
            langsmith_tracing=True,
            langsmith_api_key="test-key",
        ),
        client_factory=factory,
    )
    return tracing, resolved_client
