"""Keep the importable Postman collection aligned with the public API."""

import json
from pathlib import Path
from typing import Any

COLLECTION_PATH = (
    Path(__file__).resolve().parents[3]
    / "postman"
    / "Cook-Mantra.postman_collection.json"
)

EXPECTED_REQUESTS = {
    ("GET", "/api/v1/health"),
    ("GET", "/api/v1/ready"),
    ("GET", "/api/v1/jobs/{{job_id}}"),
    ("POST", "/api/v1/sessions"),
    ("GET", "/api/v1/sessions/{{session_id}}"),
    ("PUT", "/api/v1/sessions/{{session_id}}/ingredients"),
    ("POST", "/api/v1/sessions/{{session_id}}/ingredients/confirm"),
    ("POST", "/api/v1/sessions/{{session_id}}/recipe-options"),
    ("POST", "/api/v1/sessions/{{session_id}}/recipe-options/more"),
    ("POST", "/api/v1/sessions/{{session_id}}/recipes"),
    ("GET", "/api/v1/artifacts/{{artifact_id}}"),
}

IMAGE_DESCRIPTION = "Choose a JPEG, PNG, or WebP ingredient image. Maximum size: 10 MB."


def _requests(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    requests: list[dict[str, Any]] = []
    for item in items:
        request = item.get("request")
        if isinstance(request, dict):
            requests.append(item)
        children = item.get("item")
        if isinstance(children, list):
            requests.extend(_requests(children))
    return requests


def test_postman_collection_is_portable_and_covers_the_public_api() -> None:
    collection = json.loads(COLLECTION_PATH.read_text())
    requests = _requests(collection["item"])

    assert collection["info"]["schema"].endswith(
        "/json/collection/v2.1.0/collection.json"
    )
    assert {
        (item["request"]["method"], item["request"]["url"].removeprefix("{{base_url}}"))
        for item in requests
    } == EXPECTED_REQUESTS
    assert {variable["key"] for variable in collection["variable"]} >= {
        "base_url",
        "session_id",
        "job_id",
        "ingredients_payload",
        "option_ids_json",
        "artifact_id",
    }

    create_session = next(
        item for item in requests if item["name"].startswith("03 Create Session")
    )
    assert create_session["request"]["body"] == {
        "mode": "formdata",
        "formdata": [
            {
                "key": "image",
                "type": "file",
                "src": [],
                "description": IMAGE_DESCRIPTION,
            }
        ],
    }
