import os
from collections.abc import Iterator
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from core.config import PROJECT_ROOT

_TEST_RUNTIME_CONFIG = Path(__file__).parent / "fixtures" / "cook-mantra.yaml"
# Conftest loads before test modules, including parameter values built at import time.
os.environ["COOK_MANTRA_CONFIG_PATH"] = str(_TEST_RUNTIME_CONFIG)


@pytest.fixture(autouse=True)
def isolate_runtime_config_cache() -> Iterator[None]:
    """Keep runtime snapshots independent between tests and active user config."""
    from core.runtime_config import get_runtime_snapshot

    get_runtime_snapshot.cache_clear()
    yield
    get_runtime_snapshot.cache_clear()


@pytest.fixture
def project_tmp_path() -> Iterator[Path]:
    """Provide an auto-cleaned test directory inside the project runtime root."""
    runtime_root = PROJECT_ROOT / "tmp"
    runtime_root.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="cook-mantra-test-", dir=runtime_root) as directory:
        yield Path(directory)
