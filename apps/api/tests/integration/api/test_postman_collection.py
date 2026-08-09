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
    ("GET", "/api/v1/runtime-status"),
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


def _script(item: dict[str, Any], listen: str) -> str:
    event = next(event for event in item["event"] if event["listen"] == listen)
    return "\n".join(event["script"]["exec"])


def test_postman_collection_is_portable_and_covers_the_public_api() -> None:
    collection = json.loads(COLLECTION_PATH.read_text())
    requests = _requests(collection["item"])
    variables = {
        variable["key"]: variable["value"] for variable in collection["variable"]
    }

    assert collection["info"]["schema"].endswith(
        "/json/collection/v2.1.0/collection.json"
    )
    assert {
        (item["request"]["method"], item["request"]["url"].removeprefix("{{base_url}}"))
        for item in requests
    } == EXPECTED_REQUESTS
    assert variables.keys() >= {
        "base_url",
        "session_id",
        "job_id",
        "ingredients_payload",
        "available_option_ids_json",
        "selected_option_ids_json",
        "artifact_id",
        "runtime_revision",
    }
    assert variables["selected_option_ids_json"] == "[]"
    assert "selected_option_count" not in variables

    create_session = next(
        item for item in requests if item["name"].startswith("04 Create Session")
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
    assert create_session["request"].get("header") == [
        {
            "key": "X-Cook-Mantra-Runtime-Revision",
            "value": "{{runtime_revision}}",
            "type": "text",
            "description": (
                "Required for browser photo uploads. Origin-less direct API clients "
                "may omit it and remain responsible for their own disclosure."
            ),
        }
    ]
    create_description = create_session["request"]["description"]
    assert "not consent" in create_description
    assert "runtime_status_stale" in create_description
    create_script = _script(create_session, "test")
    assert "body.error.code" in create_script
    assert "runtime_status_stale" in create_script
    assert "pm.collectionVariables.set('session_id'" in create_script

    get_options = next(
        item for item in requests if item["name"].startswith("11 Get Options")
    )
    get_options_event = next(
        event for event in get_options["event"] if event["listen"] == "test"
    )
    get_options_script = "\n".join(get_options_event["script"]["exec"])
    assert "available_option_ids_json" in get_options_script
    assert "set('selected_option_ids_json'" not in get_options_script

    refresh_options = next(
        item for item in requests if item["name"].startswith("15 Refresh Options")
    )
    refresh_event = next(
        event for event in refresh_options["event"] if event["listen"] == "test"
    )
    refresh_script = "\n".join(refresh_event["script"]["exec"])
    assert "set('selected_option_ids_json'" not in refresh_script

    option_requests = [
        item
        for item in requests
        if item["name"].startswith(("09 Generate Recipe", "13 Generate More"))
    ]
    assert len(option_requests) == 2
    for option_request in option_requests:
        preferences = json.loads(option_request["request"]["body"]["raw"])
        assert preferences["spice_level"] == "medium"
        assert preferences["special_instructions"]

    generate_recipes = next(
        item for item in requests if item["name"].startswith("16 Generate Complete")
    )
    prerequest = next(
        event for event in generate_recipes["event"] if event["listen"] == "prerequest"
    )
    prerequest_script = "\n".join(prerequest["script"]["exec"])
    assert "pm.execution.skipRequest()" in prerequest_script
    assert generate_recipes["request"]["body"]["raw"] == (
        '{\n  "option_ids": {{selected_option_ids_json}}\n}'
    )


def test_postman_runtime_status_is_a_read_only_no_store_request() -> None:
    collection = json.loads(COLLECTION_PATH.read_text())
    requests = _requests(collection["item"])
    runtime_status = next(
        item
        for item in requests
        if item["request"]["url"] == "{{base_url}}/api/v1/runtime-status"
    )

    assert runtime_status["request"]["method"] == "GET"
    assert "body" not in runtime_status["request"]
    assert "auth" not in runtime_status["request"]
    assert "header" not in runtime_status["request"]

    script = _script(runtime_status, "test")
    assert "pm.response.to.have.status(200)" in script
    assert "pm.response.headers.get('Cache-Control')" in script
    assert "to.eql('no-store')" in script
    assert "['ok', 'attention']" in script
    assert "body.runtime_revision" in script
    assert "pm.collectionVariables.set('runtime_revision'" in script


def test_postman_readiness_is_described_as_provider_neutral() -> None:
    collection = json.loads(COLLECTION_PATH.read_text())
    requests = _requests(collection["item"])
    readiness = next(
        item
        for item in requests
        if item["request"]["url"] == "{{base_url}}/api/v1/ready"
    )

    service_checks = collection["item"][0]
    assert service_checks["name"] == "01 - Service checks"
    assert "required model roles" in service_checks["description"].casefold()
    assert "ollama has every configured model" not in (
        service_checks["description"].casefold()
    )

    assert readiness["name"] == "03 Required Model Readiness"
    description = readiness["request"]["description"].casefold()
    assert "every required model role" in description
    assert "deprecated safe ollama inventory" in description

    script = _script(readiness, "test")
    assert "[200, 401, 402, 429, 502, 503, 504]" in script
    assert "pm.response.code !== 200" in script
    assert (
        "console.warn('One or more required model roles are unavailable:', "
        "pm.response.json());"
    ) in script
    assert not any(
        provider in script.casefold() for provider in ("ollama", "openrouter", "codex")
    )

    ingredient_poll = next(
        item for item in requests if item["name"] == "05 Poll Ingredient Extraction Job"
    )
    poll_description = ingredient_poll["request"]["description"].casefold()
    assert "local vision inference" not in poll_description
    assert "selected vision provider" in poll_description


def test_postman_stale_runtime_status_clears_prior_workflow_state() -> None:
    collection = json.loads(COLLECTION_PATH.read_text())
    requests = _requests(collection["item"])
    create_session = next(
        item for item in requests if item["name"].startswith("04 Create Session")
    )
    script = _script(create_session, "test")
    stale_branch = script.split("if (pm.response.code === 409) {", maxsplit=1)[1].split(
        "} else {", maxsplit=1
    )[0]

    assert "pm.collectionVariables.set('session_id', '');" in stale_branch
    assert "pm.collectionVariables.set('job_id', '');" in stale_branch
    assert "pm.collectionVariables.set('job_status', '');" in stale_branch


def test_postman_request_step_numbers_are_unique() -> None:
    collection = json.loads(COLLECTION_PATH.read_text())
    requests = _requests(collection["item"])
    step_numbers = [int(item["name"].split(maxsplit=1)[0]) for item in requests]

    assert len(step_numbers) == len(set(step_numbers))


def test_postman_guidance_references_the_current_workflow_steps() -> None:
    collection = json.loads(COLLECTION_PATH.read_text())
    requests = _requests(collection["item"])
    document = json.dumps(collection)
    steps = {
        item["name"].split(maxsplit=1)[1]: item["name"].split(maxsplit=1)[0]
        for item in requests
    }

    assert (
        f"In request {steps['Create Session - Select Image First']}, select an "
        "ingredient image"
    ) in collection["info"]["description"]
    assert (f"prepared by request {steps['Get Extracted Ingredients']}") in document
    assert (f"If you sent request {steps['Generate More Recipe Options']}") in document
    assert (f"Before request {steps['Generate Complete Recipes']}") in document
    assert (f"before request {steps['Generate Complete Recipes']}") in document
    option_sources = (
        f"request {steps['Get Options and Capture IDs']} or "
        f"{steps['Refresh Options and Selected IDs']}"
    )
    assert document.casefold().count(option_sources.casefold()) == 2


def test_postman_preview_belongs_only_to_completed_recipes() -> None:
    collection = json.loads(COLLECTION_PATH.read_text())
    option_group = next(
        item for item in collection["item"] if item["name"].startswith("03 -")
    )
    complete_group = next(
        item for item in collection["item"] if item["name"].startswith("04 -")
    )

    option_contract = json.dumps(option_group).casefold()
    assert "preview" not in option_contract
    assert '"warnings"' not in option_contract

    final_session = next(
        item
        for item in complete_group["item"]
        if item["request"]["method"] == "GET"
        and item["request"]["url"] == "{{base_url}}/api/v1/sessions/{{session_id}}"
    )
    final_script = _script(final_session, "test")
    clear_artifact = "pm.collectionVariables.set('artifact_id', '');"
    inspect_recipes = "Object.values(body.complete_recipes)"
    capture_artifact = (
        "pm.collectionVariables.set('artifact_id', firstPreview.preview.artifact_id);"
    )
    assert clear_artifact in final_script
    assert inspect_recipes in final_script
    assert capture_artifact in final_script
    assert final_script.index(clear_artifact) < final_script.index(inspect_recipes)
    assert final_script.index(inspect_recipes) < final_script.index(capture_artifact)
    assert "body.recipe_options" not in final_script

    artifact_request = next(
        item
        for item in complete_group["item"]
        if item["request"]["url"] == "{{base_url}}/api/v1/artifacts/{{artifact_id}}"
    )
    assert complete_group["item"].index(final_session) < complete_group["item"].index(
        artifact_request
    )
    prerequest_script = _script(artifact_request, "prerequest")
    assert "pm.execution.skipRequest()" in prerequest_script
    assert "!artifactId || !artifactId.trim()" in prerequest_script

    assert all(
        item["request"]["url"] != "{{base_url}}/api/v1/artifacts/{{artifact_id}}"
        for item in option_group["item"]
    )
