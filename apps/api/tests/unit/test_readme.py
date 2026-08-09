import json
import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[4]


def _read(relative_path: str) -> str:
    return (PROJECT_ROOT / relative_path).read_text()


def _normalized(text: str) -> str:
    return " ".join(text.casefold().split())


def _assert_completed_recipe_preview_timing(path: str, text: str) -> None:
    timing_pattern = re.compile(
        r"(?:complete|completed)[ -]recipes?[^.]{0,180}(?:preview|image)"
        r"|(?:preview|image)[^.]{0,120}\bafter\b[^.]{0,120}\brecipe"
        r"|(?:preview|image)[^.]{0,120}"
        r"(?:\bon(?:ly)?\b|\bfor\b|belongs to|owned by|attached to)"
        r"[^.]{0,120}(?:complete|completed)[ -]recipes?"
    )
    completed_relation = (
        r"(?:contains?|displays?|features?|has|have|includes?|owns?|receives?|shows?)"
    )
    completed_media = (
        r"(?:(?:a|an|the|any|generated|dish)\s+)*(?:previews?|images?|photos?)"
    )
    completed_denial = re.compile(
        rf"\b(?:complete|completed)[ -]recipes?\b[^.;]{{0,60}}(?:"
        rf"\bnever\s+(?:ever\s+)?{completed_relation}"
        rf"|\b(?:do(?:es)? not|don['’]t|doesn['’]t)\s+"
        rf"(?!always\b)(?:ever\s+)?{completed_relation}"
        rf"|\b(?:cannot|can['’]t|will not|won['’]t|must not|mustn['’]t|"
        rf"should not|shouldn['’]t|may not)\s+(?:ever\s+)?{completed_relation}"
        rf"|\b(?:(?:is|are)\s+not|isn['’]t|aren['’]t)\s+"
        rf"(?:allowed|permitted)\s+to\s+"
        rf"{completed_relation})\s+{completed_media}\b"
        rf"|\b(?:complete|completed)[ -]recipes?\b[^.;]{{0,100}}"
        rf"{completed_relation}\s+no\s+{completed_media}\b"
        rf"|^no\s+(?:complete|completed)[ -]recipes?\b[^.;]{{0,100}}"
        rf"{completed_relation}\s+{completed_media}\b"
        rf"|\b(?:complete|completed)[ -]recipes?\b[^.;]{{0,100}}"
        rf"\bwithout\s+{completed_media}\b"
    )
    sentences = [
        " ".join(sentence.split())
        for sentence in re.split(r"(?<=[.!?])\s+|\n+", text.casefold())
        if sentence.strip()
    ]
    assert any(
        timing_pattern.search(sentence) and not completed_denial.search(sentence)
        for sentence in sentences
    ), f"{path} must place optional previews after completed-recipe writing"

    option_subject = r"(?:recipe[ -]?)?(?:options?|cards?|suggestions?)"
    media_noun = r"(?:previews?|images?|photos?)"
    ownership_verb = (
        r"(?:adds?|attach(?:es|ed|ing)?|contains?|displays?|features?|"
        r"generates?|has|have|includes?|invokes?|owns?|receives?|shows?)"
    )
    ownership_or_omit = rf"(?:{ownership_verb}|omits?)"
    direct_media = (
        rf"(?:(?:a|all|an|any|dish|every|generated|ai-generated|the)\s+)*"
        rf"{media_noun}"
    )

    for sentence in sentences:
        affirmative_ownership = re.search(
            rf"\b{option_subject}\b[^.]{{0,120}}\b{ownership_or_omit}\b"
            rf"[^.]{{0,120}}\b{media_noun}\b"
            rf"|\b{media_noun}\b[^.]{{0,100}}\b(?:for|on|to)\b"
            rf"[^.]{{0,60}}\b{option_subject}\b"
            r"|\bsuggest(?:s|ed|ing)?\s+dishes\b[^.]{0,120}"
            rf"\b{media_noun}\b",
            sentence,
        )
        if not affirmative_ownership:
            continue
        direct_denial = re.search(
            rf"(?:\bnever\b|\bdo(?:es)? not\b|\bdon['’]t\b|\bdoesn['’]t\b|"
            rf"\bcannot\b|\bcan['’]t\b)\s+"
            rf"{ownership_verb}\s+{direct_media}\b"
            rf"|^no\s+{option_subject}\b[^.;:]{{0,100}}"
            rf"{ownership_verb}\s+{direct_media}\b"
            rf"|\b{ownership_verb}\b\s+no\s+{direct_media}\b"
            rf"|(?<!never )(?<!not )(?<!no )\bomits?\b"
            rf"(?!\s+(?:no|not)\b)\s+{direct_media}\b"
            rf"|\b{media_noun}\b[^.;:]{{0,100}}\bnever\b[^.;:]{{0,60}}"
            rf"\b(?:for|on|to)\b[^.;:]{{0,60}}\b{option_subject}\b",
            sentence,
        )
        remaining = ""
        if direct_denial:
            remaining = (
                sentence[: direct_denial.start()]
                + " "
                + sentence[direct_denial.end() :]
            )
        remaining_ownership = re.search(
            rf"\b{ownership_or_omit}\b\s+{direct_media}\b",
            remaining,
        )
        assert direct_denial and not remaining_ownership, (
            f"{path} still assigns generated previews to recipe options: {sentence}"
        )


def test_api_readme_documents_required_local_commands() -> None:
    readme = _read("apps/api/README.md")

    for command in {
        "uv sync",
        "uv run uvicorn main:app",
        "uv run pytest",
        "COOK_MANTRA_RUN_LIVE=1",
    }:
        assert command in readme


def test_root_package_exposes_one_exact_parallel_development_entrypoint() -> None:
    package = json.loads(_read("package.json"))
    scripts = package["scripts"]

    assert scripts["dev"] == 'pnpm run "/^(api:dev|web:dev)$/"'
    assert (
        scripts["api:dev"]
        == "cd apps/api && uv run uvicorn main:app --host 127.0.0.1 --port 8000"
    )
    assert scripts["web:dev"] == "pnpm --filter @cook-mantra/web dev"


def test_root_readme_leads_with_combined_dev_and_retains_diagnostic_commands() -> None:
    readme = _read("README.md")

    for command in ("pnpm dev", "pnpm api:dev", "pnpm web:dev"):
        assert command in readme
    assert readme.index("pnpm dev") < readme.index("pnpm api:dev")
    assert readme.index("pnpm dev") < readme.index("pnpm web:dev")


def test_product_docs_keep_generated_previews_on_completed_recipes_only() -> None:
    docs = {
        path: _read(path)
        for path in (
            "README.md",
            "apps/api/README.md",
            "docs/prd.md",
            "docs/architecture-doc.md",
            "docs/ui-style-guide.md",
        )
    }

    for path, text in docs.items():
        _assert_completed_recipe_preview_timing(path, text)


@pytest.mark.parametrize(
    "contradictory_claim",
    [
        "Recipe option cards do not always include generated images.",
        "Recipe option cards include no fewer than two generated images.",
        "Recipe option cards omit metadata but include generated images.",
        "Recipe option cards omit no generated images.",
        "Recipe option cards never omit generated images.",
        "Recipe option cards never contain fewer than two generated images.",
    ],
)
def test_preview_timing_guard_rejects_contradictory_option_image_ownership(
    contradictory_claim: str,
) -> None:
    with pytest.raises(AssertionError):
        _assert_completed_recipe_preview_timing(
            "regression fixture",
            f"A completed recipe may receive a preview. {contradictory_claim}",
        )


@pytest.mark.parametrize(
    "valid_claim",
    [
        "Recipe option cards omit generated images.",
        "Recipe option cards omit all generated images.",
        "Recipe option cards do not include generated images.",
        "No recipe option cards include generated images.",
    ],
)
def test_preview_timing_guard_accepts_direct_option_image_denials(
    valid_claim: str,
) -> None:
    _assert_completed_recipe_preview_timing(
        "regression fixture",
        f"A completed recipe may receive a preview. {valid_claim}",
    )


@pytest.mark.parametrize(
    "contradictory_claim",
    [
        "Completed recipes never receive preview images.",
        "Completed recipes must not include preview images.",
        "Completed recipes are not allowed to receive preview images.",
        "Completed recipes are written without preview images.",
        "Completed recipes don't include preview images.",
        "Completed recipes won't receive preview images.",
        "Completed recipes aren't allowed to receive images.",
        "Completed recipes do not ever include images.",
    ],
)
def test_preview_timing_guard_rejects_negated_completed_recipe_ownership(
    contradictory_claim: str,
) -> None:
    with pytest.raises(AssertionError):
        _assert_completed_recipe_preview_timing(
            "regression fixture",
            contradictory_claim,
        )


def test_preview_timing_guard_accepts_explicit_completed_recipe_optionality() -> None:
    _assert_completed_recipe_preview_timing(
        "regression fixture",
        "Completed recipes do not always receive preview images.",
    )


@pytest.mark.parametrize(
    "joiner",
    ["but", "and", "; they"],
)
def test_preview_timing_guard_rejects_affirmative_option_clause_after_denial(
    joiner: str,
) -> None:
    with pytest.raises(AssertionError):
        _assert_completed_recipe_preview_timing(
            "regression fixture",
            (
                "A completed recipe may receive a preview. "
                "Recipe option cards do not include generated images "
                f"{joiner} show dish photos."
            ),
        )


def test_preview_timing_guard_rejects_affirmative_option_clause_before_denial() -> None:
    with pytest.raises(AssertionError):
        _assert_completed_recipe_preview_timing(
            "regression fixture",
            (
                "A completed recipe may receive a preview. "
                "Recipe option cards include generated images "
                "but do not show dish photos."
            ),
        )


def test_runtime_status_and_remote_media_disclosure_are_documented_as_shipped() -> None:
    root_readme = _normalized(_read("README.md"))
    api_readme = _normalized(_read("apps/api/README.md"))
    architecture = _normalized(_read("docs/architecture-doc.md"))
    prd = _normalized(_read("docs/prd.md"))
    ui_guide = _normalized(_read("docs/ui-style-guide.md"))

    for path, text in {
        "README.md": root_readme,
        "apps/api/README.md": api_readme,
        "docs/architecture-doc.md": architecture,
    }.items():
        assert "runtime-status" in text, f"{path} must document the safe endpoint"
        assert "disclosure" in text or (
            "provider/model" in text and "acknowledgement" in text
        ), f"{path} must document remote ingredient-media acknowledgement"

    assert not re.search(
        r"disclosure[^.]{0,180}(?:follow-up|not implemented)", api_readme
    )
    assert "this photo will leave your device" in prd or (
        "remote" in prd and "disclosure" in prd
    )
    for shared_contract in (
        ".runtime-ledger",
        ".privacy-dialog-provider",
        "this photo will leave your device",
    ):
        assert shared_contract in ui_guide


def test_ollama_loopback_rule_is_scoped_to_ingredient_media() -> None:
    prd = _normalized(_read("docs/prd.md"))
    architecture = _normalized(_read("docs/architecture-doc.md"))

    assert "ollama endpoints must be local loopback addresses" not in prd
    assert "ollama endpoints are validated as loopback-only" not in architecture
    for path, text in {
        "docs/prd.md": prd,
        "docs/architecture-doc.md": architecture,
    }.items():
        assert re.search(
            r"ollama[^.]{0,180}ingredient[^.]{0,180}loopback"
            r"|ingredient[^.]{0,180}ollama[^.]{0,180}loopback",
            text,
        ), f"{path} must bind only Ollama ingredient media to loopback"


def test_langsmith_docs_match_the_content_free_trace_boundary() -> None:
    docs = {
        "example.env": _normalized(_read("example.env")),
        "apps/api/README.md": _normalized(_read("apps/api/README.md")),
        "docs/architecture-doc.md": _normalized(_read("docs/architecture-doc.md")),
    }

    for path, text in docs.items():
        assert "prompts and outputs leave" not in text, (
            f"{path} must not claim LangSmith receives private model content"
        )
        for contract in (
            "role/provider/model",
            "token",
            "media type",
            "byte count",
            "detected count",
            "warning count",
        ):
            assert contract in text, f"{path} must document safe {contract} metadata"
        assert re.search(r"langsmith[^.]{0,240}(?:never|no)[^.]{0,160}prompts", text)
        assert re.search(r"langsmith[^.]{0,260}(?:never|no)[^.]{0,180}outputs", text)


def test_codex_image_limit_and_removed_beast_runtime_are_current() -> None:
    docs = {
        path: _normalized(_read(path))
        for path in (
            "README.md",
            "apps/api/README.md",
            "docs/prd.md",
            "docs/architecture-doc.md",
            "docs/ui-style-guide.md",
        )
    }

    for path in ("README.md", "apps/api/README.md", "docs/architecture-doc.md"):
        sentences = re.split(r"(?<=[.!?])\s+", docs[path])
        assert any(
            "codex" in sentence
            and re.search(r"\b(?:image|raster)", sentence)
            and re.search(
                r"unavailable|unsupported|not (?:available|supported)|cannot|"
                r"(?:lacks|no) (?:a )?documented",
                sentence,
            )
            for sentence in sentences
        ), f"{path} must state that Codex image generation is unavailable"

    stale_surfaces = {
        **docs,
        "example.env": _normalized(_read("example.env")),
        "config/cook-mantra.example.yaml": _normalized(
            _read("config/cook-mantra.example.yaml")
        ),
        "Postman collection": _normalized(
            _read("apps/api/postman/Cook-Mantra.postman_collection.json")
        ),
    }
    for path, text in stale_surfaces.items():
        assert "beast" not in text, f"{path} still documents the removed Beast runtime"


def test_codex_functional_preview_documents_the_explicit_risk_boundary() -> None:
    docs = {
        path: _normalized(_read(path))
        for path in (
            "README.md",
            "apps/api/README.md",
            "docs/architecture-doc.md",
        )
    }
    example = _normalized(_read("config/cook-mantra.example.yaml"))

    assert "allow_unverified_tool_boundary: false" in example
    for path, text in docs.items():
        assert "allow_unverified_tool_boundary" in text, (
            f"{path} must name the explicit Codex opt-in"
        )
        assert re.search(r"(?:default|normally)[^.]{0,100}(?:false|off|disabled)", text)
        assert re.search(
            r"restart[^.]{0,120}(?:the )?api|api[^.]{0,120}restart",
            text,
        )
        assert re.search(r"codex-managed[^.]{0,220}sign-in", text)
        assert re.search(
            r"codex[^.]{0,220}(?:content|prompt|photo)[^.]{0,180}openai"
            r"|(?:content|prompt|photo)[^.]{0,220}codex[^.]{0,180}openai",
            text,
        )
        assert re.search(r"event rejection[^.]{0,120}not[^.]{0,120}prevent", text), (
            f"{path} must disclose that event rejection is not prevention"
        )


def test_provider_live_checks_are_explicitly_opt_in_and_recorded_as_not_run() -> None:
    readme = _normalized(_read("apps/api/README.md"))

    assert "opt-in live checks" in readme
    assert "cook_mantra_run_live=1" in readme
    assert "cook_mantra_run_langsmith_live=1" in readme
    assert any(
        "live" in sentence and re.search(r"\bnot (?:run|executed)\b", sentence)
        for sentence in re.split(r"(?<=[.!?])\s+", readme)
    )


def test_contributor_docs_describe_the_current_tree_and_archive_old_specs() -> None:
    structure = _normalized(_read("docs/folder-structure.md"))
    for stale_path in ("packages/api-client", "scripts/"):
        assert stale_path not in structure
    assert "database access" not in structure
    assert "recipe writer" in structure
    for current_path in (
        "apps/web/src/features",
        "apps/web/tests/setup.ts",
        "apps/api/tests/e2e",
        "apps/api/tests/live",
    ):
        assert current_path in structure

    archived_docs = {
        "design_handoff_cook_mantra_ui/README.md": _read(
            "design_handoff_cook_mantra_ui/README.md"
        ),
        "docs/superpowers/specs/2026-07-30-cook-mantra-backend-design.md": _read(
            "docs/superpowers/specs/2026-07-30-cook-mantra-backend-design.md"
        ),
    }
    for path, text in archived_docs.items():
        opening = _normalized("\n".join(text.splitlines()[:12]))
        assert "superseded" in opening, f"{path} must be marked as historical"
        assert "docs/prd.md" in opening
        assert "docs/architecture-doc.md" in opening


def test_architecture_verification_is_deterministic_and_tradeoffs_are_current() -> None:
    architecture = _normalized(_read("docs/architecture-doc.md"))

    assert 'cd apps/api && uv run pytest -m "not live" -w error' in architecture
    assert "cd apps/api && uv run langgraph validate" in architecture
    assert 'cd apps/api && uv run python -c "from main import app; app.openapi()"' in (
        architecture
    )
    assert "codex and image adapters implement the same boundary" not in architecture
