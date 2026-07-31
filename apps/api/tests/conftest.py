import os
from collections.abc import Iterator
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from core.config import PROJECT_ROOT


@pytest.fixture(autouse=True)
def provide_test_ollama_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """Supply required Ollama configuration without overriding live-test input."""
    if "OLLAMA_BASE_URL" not in os.environ:
        monkeypatch.setenv("OLLAMA_BASE_URL", "http://ollama.test:11434")


@pytest.fixture
def project_tmp_path() -> Iterator[Path]:
    """Provide an auto-cleaned test directory inside the project runtime root."""
    runtime_root = PROJECT_ROOT / "tmp"
    runtime_root.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="cook-mantra-test-", dir=runtime_root) as directory:
        yield Path(directory)
