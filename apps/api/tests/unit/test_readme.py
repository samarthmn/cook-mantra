from pathlib import Path


def test_readme_documents_required_local_commands() -> None:
    readme = Path("README.md").read_text()

    for command in {
        "uv sync",
        "uv run uvicorn main:app",
        "uv run pytest",
        "COOK_MANTRA_RUN_LIVE=1",
    }:
        assert command in readme
