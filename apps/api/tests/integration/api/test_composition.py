from pathlib import Path

from api.app import create_app
from core.config import Settings


def test_create_app_constructs_the_local_application(
    project_tmp_path: Path,
) -> None:
    app = create_app(Settings(_env_file=None, artifact_root=project_tmp_path))

    assert app.state.session_store is not None
    assert app.state.job_store is not None
    assert app.state.artifact_store is not None
