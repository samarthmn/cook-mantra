from pathlib import Path

from fastapi.testclient import TestClient

from api.app import create_app
from core.config import Settings
from domain.model_runtime import AgentRole
from schemas.health import ReadinessResponse, RuntimeStatusResponse

EXPECTED_OPERATIONS = {
    ("GET", "/api/v1/health"): "getHealth",
    ("GET", "/api/v1/runtime-status"): "getRuntimeStatus",
    ("GET", "/api/v1/ready"): "getReadiness",
    ("GET", "/api/v1/jobs/{job_id}"): "getJob",
    ("POST", "/api/v1/sessions"): "createSession",
    ("POST", "/api/v1/sessions/manual"): "createManualSession",
    ("GET", "/api/v1/sessions/{session_id}"): "getSession",
    ("PUT", "/api/v1/sessions/{session_id}/ingredients"): "updateIngredients",
    (
        "POST",
        "/api/v1/sessions/{session_id}/ingredients/confirm",
    ): "confirmIngredients",
    (
        "POST",
        "/api/v1/sessions/{session_id}/recipe-options",
    ): "generateRecipeOptions",
    (
        "POST",
        "/api/v1/sessions/{session_id}/recipe-options/more",
    ): "generateMoreRecipeOptions",
    ("POST", "/api/v1/sessions/{session_id}/recipes"): "generateCompleteRecipes",
    ("GET", "/api/v1/artifacts/{artifact_id}"): "getArtifact",
}

EXPECTED_ERROR_RESPONSES = {
    ("GET", "/api/v1/ready"): {"401", "402", "429", "502", "503", "504"},
    ("GET", "/api/v1/jobs/{job_id}"): {"404"},
    ("POST", "/api/v1/sessions"): {"409", "422", "503"},
    ("POST", "/api/v1/sessions/manual"): {"422"},
    ("GET", "/api/v1/sessions/{session_id}"): {"404"},
    ("PUT", "/api/v1/sessions/{session_id}/ingredients"): {"404", "409", "422"},
    (
        "POST",
        "/api/v1/sessions/{session_id}/ingredients/confirm",
    ): {"404", "409", "422"},
    (
        "POST",
        "/api/v1/sessions/{session_id}/recipe-options",
    ): {"404", "409", "422", "503"},
    (
        "POST",
        "/api/v1/sessions/{session_id}/recipe-options/more",
    ): {"404", "409", "422", "503"},
    (
        "POST",
        "/api/v1/sessions/{session_id}/recipes",
    ): {"404", "409", "422", "503"},
    ("GET", "/api/v1/artifacts/{artifact_id}"): {"404"},
}


def _document(project_tmp_path: Path) -> dict[str, object]:
    app = create_app(Settings(_env_file=None, artifact_root=project_tmp_path))
    return TestClient(app).get("/openapi.json").json()


def _walk_references(value: object) -> list[str]:
    references: list[str] = []
    if isinstance(value, dict):
        reference = value.get("$ref")
        if isinstance(reference, str):
            references.append(reference)
        for child in value.values():
            references.extend(_walk_references(child))
    elif isinstance(value, list):
        for child in value:
            references.extend(_walk_references(child))
    return references


def test_openapi_contains_the_complete_stable_backend_contract(
    project_tmp_path: Path,
) -> None:
    document = _document(project_tmp_path)

    assert document["info"] == {
        "title": "Cook Mantra API",
        "description": "Local backend for Cook Mantra's image-to-recipe workflow.",
        "version": "0.1.0",
    }
    assert (
        document["components"]["schemas"]["RecipePreferences"]["additionalProperties"]
        is False
    )

    operation_ids = []
    for (method, path), expected_operation_id in EXPECTED_OPERATIONS.items():
        operation = document["paths"][path][method.lower()]
        assert operation["operationId"] == expected_operation_id
        assert operation["summary"]
        assert operation["description"]
        assert operation["tags"]
        operation_ids.append(operation["operationId"])
        success = next(
            response
            for code, response in operation["responses"].items()
            if code.startswith("2")
        )
        assert success["content"]

    assert len(operation_ids) == len(set(operation_ids))
    assert document["paths"].keys() == {path for _, path in EXPECTED_OPERATIONS}

    schemas = document["components"]["schemas"]
    for reference in _walk_references(document):
        prefix = "#/components/schemas/"
        assert reference.startswith(prefix)
        assert reference.removeprefix(prefix) in schemas


def test_openapi_documents_error_envelopes_examples_and_binary_artifacts(
    project_tmp_path: Path,
) -> None:
    document = _document(project_tmp_path)
    schemas = document["components"]["schemas"]

    for (method, path), status_codes in EXPECTED_ERROR_RESPONSES.items():
        responses = document["paths"][path][method.lower()]["responses"]
        for status_code in status_codes:
            assert responses[status_code]["content"]["application/json"]["schema"] == {
                "$ref": "#/components/schemas/ErrorResponse"
            }

    public_examples = {
        "ErrorDetail",
        "ErrorResponse",
        "HealthResponse",
        "OllamaStatus",
        "ReadinessResponse",
        "RuntimeStatusResponse",
        "JobErrorResponse",
        "JobResponse",
        "IngredientResponse",
        "IngredientReviewRequest",
        "RecipePreferences",
        "RecipeOptionResponse",
        "RecipeSelectionRequest",
        "CompleteRecipeResponse",
        "RecipeFailureResponse",
        "QueuedJobResponse",
        "SessionResponse",
        "DishPreview",
    }
    for schema_name in public_examples:
        assert schemas[schema_name]["examples"]

    artifact_response = document["paths"]["/api/v1/artifacts/{artifact_id}"]["get"][
        "responses"
    ]["200"]
    for media_type in {"image/jpeg", "image/png", "image/webp"}:
        assert artifact_response["content"][media_type]["schema"] == {
            "type": "string",
            "format": "binary",
        }


def test_openapi_documents_the_safe_read_only_runtime_status_contract(
    project_tmp_path: Path,
) -> None:
    document = _document(project_tmp_path)
    operation = document["paths"]["/api/v1/runtime-status"]["get"]

    assert "parameters" not in operation
    assert "requestBody" not in operation
    assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/RuntimeStatusResponse"
    }
    assert operation["responses"]["200"]["headers"] == {
        "Cache-Control": {
            "description": "Prevents caching of the process-local runtime snapshot.",
            "schema": {"const": "no-store", "type": "string"},
        }
    }

    schemas = document["components"]["schemas"]
    runtime_status = schemas["RuntimeStatusResponse"]
    assert runtime_status["additionalProperties"] is False
    assert runtime_status["required"] == [
        "status",
        "runtime_revision",
        "model_runtime",
    ]
    assert runtime_status["properties"].keys() == {
        "status",
        "runtime_revision",
        "model_runtime",
    }
    assert runtime_status["properties"]["status"] == {
        "enum": ["ok", "attention"],
        "title": "Status",
        "type": "string",
    }
    assert runtime_status["properties"]["runtime_revision"] == {
        "maxLength": 128,
        "minLength": 32,
        "pattern": "^[A-Za-z0-9_-]+$",
        "title": "Runtime Revision",
        "type": "string",
    }
    assert runtime_status["properties"]["model_runtime"] == {
        "$ref": "#/components/schemas/ModelRuntimeReadiness"
    }
    runtime_example = runtime_status["examples"][0]
    RuntimeStatusResponse.model_validate(runtime_example)
    enabled_role_needs_attention = any(
        role["enabled"] and not role["ready"]
        for role in runtime_example["model_runtime"]["roles"].values()
    )
    expected_status = (
        "attention"
        if not runtime_example["model_runtime"]["ready"] or enabled_role_needs_attention
        else "ok"
    )
    assert runtime_example["status"] == expected_status
    for example in schemas["ReadinessResponse"].get("examples", []):
        ReadinessResponse.model_validate(example)

    role_readiness = schemas["RoleReadiness"]
    assert role_readiness["additionalProperties"] is False
    assert role_readiness["required"] == [
        "role",
        "provider",
        "model",
        "enabled",
        "ready",
        "required_capabilities",
        "available_capabilities",
    ]
    assert role_readiness["properties"].keys() == {
        "role",
        "provider",
        "model",
        "enabled",
        "ready",
        "required_capabilities",
        "available_capabilities",
        "error",
    }

    runtime_readiness = schemas["ModelRuntimeReadiness"]
    roles = runtime_readiness["properties"]["roles"]
    assert roles["minProperties"] == len(AgentRole)
    assert roles["maxProperties"] == len(AgentRole)
    assert roles["propertyNames"] == {"$ref": "#/components/schemas/AgentRole"}


def test_openapi_assigns_preview_only_to_complete_recipes(
    project_tmp_path: Path,
) -> None:
    schemas = _document(project_tmp_path)["components"]["schemas"]

    option_schema = schemas["RecipeOptionResponse"]
    assert "preview" not in option_schema["properties"]
    assert "warnings" not in option_schema["properties"]
    assert option_schema["additionalProperties"] is False

    complete_recipe_schema = schemas["CompleteRecipeResponse"]
    assert "preview" in complete_recipe_schema["properties"]
    assert "preview" in complete_recipe_schema["required"]
    assert complete_recipe_schema["properties"]["preview"] == {
        "anyOf": [
            {"$ref": "#/components/schemas/DishPreview"},
            {"type": "null"},
        ]
    }

    label_schema = schemas["DishPreview"]["properties"]["label"]
    assert label_schema["const"] == "AI-generated image"


def test_openapi_documents_the_conditional_browser_revision_header(
    project_tmp_path: Path,
) -> None:
    document = _document(project_tmp_path)
    operation = document["paths"]["/api/v1/sessions"]["post"]
    revision_parameters = [
        parameter
        for parameter in operation.get("parameters", [])
        if parameter["name"] == "X-Cook-Mantra-Runtime-Revision"
    ]

    assert len(revision_parameters) == 1
    parameter = revision_parameters[0]
    assert parameter["in"] == "header"
    assert parameter["required"] is False
    assert "Origin" in parameter["description"]
    assert "direct API clients" in parameter["description"]
    assert "not consent" in parameter["description"]
    assert operation["responses"]["409"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ErrorResponse"
    }
    error_codes = document["components"]["schemas"]["ErrorCode"]["enum"]
    assert "runtime_status_stale" in error_codes


def test_docs_are_enabled_without_redoc(project_tmp_path: Path) -> None:
    app = create_app(Settings(_env_file=None, artifact_root=project_tmp_path))
    client = TestClient(app)

    assert client.get("/docs").status_code == 200
    assert client.get("/openapi.json").status_code == 200
    assert client.get("/redoc").status_code == 404
