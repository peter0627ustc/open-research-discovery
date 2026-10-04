from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from open_research_discovery.common import dump_yaml, load_yaml
from open_research_discovery.validation import (
    validate_agentgitlab_snapshot,
    validate_problem,
    validate_registry,
    validate_registry_pool_consistency,
)


def test_draft_fixture_validates_against_v1_schema(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    problem = load_yaml(root / "tests" / "fixtures" / "problem-draft.yaml")
    problem["problem_id"] = "ORP-0001"
    path = tmp_path / "problem.yaml"
    dump_yaml(path, problem)

    assert validate_problem(path, root / "schemas" / "problem.schema.json") == []
    assert problem["schema_version"] == "1.0"
    assert problem["repository"] == {"kind": "solution", "slug": "pending"}


def test_ready_problem_requires_references_and_previous_progress(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[1]
    problem = load_yaml(root / "tests" / "fixtures" / "problem-draft.yaml")
    problem["problem_id"] = "ORP-0001"
    problem["status"] = "ready"
    path = tmp_path / "problem.yaml"
    dump_yaml(path, problem)

    errors = validate_problem(path, root / "schemas" / "problem.schema.json")

    assert "open problem requires previous_progress" in errors
    assert "open problem requires references" in errors


def test_ready_problem_with_progress_and_references_validates(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[1]
    problem = load_yaml(root / "tests" / "fixtures" / "problem-draft.yaml")
    problem["problem_id"] = "ORP-0001"
    problem["status"] = "ready"
    problem["references"] = [
        "Later status review. https://example.test/later, "
        "2026. doi:10.0000/later."
    ]
    problem["previous_progress"] = [
        "Later work treats adjacent regimes but leaves this target open."
    ]
    problem["verification_difficulty"] = {
        "score": 0,
        "rationale": "Every load-bearing check is mechanical.",
    }
    path = tmp_path / "problem.yaml"
    dump_yaml(path, problem)

    assert validate_problem(path, root / "schemas" / "problem.schema.json") == []


def test_closed_external_status_does_not_require_progress_fields(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[1]
    problem = load_yaml(root / "tests" / "fixtures" / "problem-draft.yaml")
    problem["problem_id"] = "ORP-0001"
    problem["status"] = "resolved-externally"
    path = tmp_path / "problem.yaml"
    dump_yaml(path, problem)

    assert validate_problem(path, root / "schemas" / "problem.schema.json") == []


@pytest.mark.parametrize("null_fields", [False, True])
def test_verification_fields_are_optional_in_ready_and_research_records(
    tmp_path: Path, null_fields: bool,
) -> None:
    root = Path(__file__).resolve().parents[1]
    problem = load_yaml(root / "tests/fixtures/problem-draft.yaml")
    problem.update(
        problem_id="ORP-0001", status="ready",
        references=["Later status review. https://example.test/later, 2026."],
        previous_progress=["The inspected later work leaves this target open."],
    )
    for field in ("verification_contract", "verification_difficulty"):
        if null_fields:
            problem[field] = None
        else:
            problem.pop(field, None)
    path = tmp_path / "problem.yaml"
    dump_yaml(path, problem)
    assert validate_problem(path, root / "schemas/problem.schema.json") == []

    schema = load_yaml(root / "schemas/stages/research.schema.json")
    research = {
        key: value for key, value in problem.items()
        if key in schema["properties"]
    }
    research["audit_outcome"] = "open"
    assert list(Draft202012Validator(schema).iter_errors(research)) == []


@pytest.mark.parametrize("difficulty", [
    {"score": -1, "rationale": "Below range."},
    {"score": 11, "rationale": "Above range."},
    {"score": True, "rationale": "Not an integer score."},
    {"score": 3},
])
def test_optional_difficulty_still_validates_when_present(
    tmp_path: Path, difficulty: dict,
) -> None:
    root = Path(__file__).resolve().parents[1]
    problem = load_yaml(root / "tests/fixtures/problem-draft.yaml")
    problem["problem_id"] = "ORP-0001"
    problem["verification_difficulty"] = difficulty
    path = tmp_path / "problem.yaml"
    dump_yaml(path, problem)
    assert any(
        "verification_difficulty" in error
        for error in validate_problem(path, root / "schemas/problem.schema.json")
    )
    schema = load_yaml(root / "schemas/stages/research.schema.json")
    research = {
        key: value for key, value in problem.items()
        if key in schema["properties"]
    }
    research["audit_outcome"] = "open"
    assert list(Draft202012Validator(schema).iter_errors(research))


def test_registry_rejects_duplicate_ids_and_repositories(tmp_path: Path) -> None:
    path = tmp_path / "repos.yaml"
    dump_yaml(
        path,
        {
            "schema_version": 1,
            "repos": [
                {"id": "OMP-0001", "repo": "https://example.test/one"},
                {"id": "OMP-0001", "repo": "https://example.test/one"},
            ],
        },
    )

    errors = validate_registry(path)

    assert "duplicate problem id: OMP-0001" in errors
    assert "duplicate repo: https://example.test/one" in errors


def _catalog_record() -> dict:
    return {
        "id": "ORP-0001",
        "status": "ready",
        "importance_level": "high",
        "verification_difficulty": 2,
    }


def test_registry_pool_consistency_accepts_matching_rows() -> None:
    rows = [
        {
            "id": "ORP-0001",
            "repo": "https://example.test/problem",
            "status": "ready",
            "importance_level": "high",
            "verification_difficulty": 2,
        }
    ]

    assert validate_registry_pool_consistency(rows, [_catalog_record()]) == []


def test_registry_pool_consistency_reports_unknown_ids() -> None:
    rows = [{"id": "ORP-0002", "repo": "https://example.test/other"}]

    errors = validate_registry_pool_consistency(rows, [_catalog_record()])

    assert errors == ["registry id not in pool catalog: ORP-0002"]


def test_registry_pool_consistency_reports_field_drift() -> None:
    rows = [
        {
            "id": "ORP-0001",
            "repo": "https://example.test/problem",
            "status": "draft",
            "verification_difficulty": 5,
        }
    ]

    errors = validate_registry_pool_consistency(rows, [_catalog_record()])

    assert (
        "ORP-0001 registry.status=draft != catalog.status=ready" in errors
    )
    assert (
        "ORP-0001 registry.verification_difficulty=5 "
        "!= catalog.verification_difficulty=2" in errors
    )


def test_agentgitlab_snapshot_matches_external_registry(tmp_path: Path) -> None:
    registry = tmp_path / "repos.yaml"
    snapshot = tmp_path / "agentgitlab.yaml"
    dump_yaml(
        registry,
        {
            "schema_version": 1,
            "repos": [
                {"id": "ORP-0001", "repo": "https://example.test/problem"}
            ],
        },
    )
    dump_yaml(
        snapshot,
        {
            "schema_version": 1,
            "namespace": "research",
            "projects": [
                {
                    "id": "ORP-0001",
                    "project": "research/orp-0001",
                    "issue_iid": 1,
                    "baseline_commit": "a" * 40,
                }
            ],
        },
    )

    assert validate_agentgitlab_snapshot(snapshot, registry) == []
