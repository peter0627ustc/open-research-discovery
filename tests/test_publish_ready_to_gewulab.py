from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

import yaml


SCRIPT = Path(__file__).parents[1] / "scripts" / "publish_ready_to_gewulab.py"
SPEC = importlib.util.spec_from_file_location("publish_ready_to_gewulab", SCRIPT)
assert SPEC and SPEC.loader
publisher = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = publisher
SPEC.loader.exec_module(publisher)


def readme(status: str = "ready") -> str:
    return f"""# A precise open problem

Abstract with inline math $x^2$.

## Background

Background.

## Problem Statement

Prove or refute the statement.

## Current Progress

- Status: `{status}`

## Scientific Significance

High.

## Answer Types

- proof

## Verification Standard

### proof

Provide a complete proof.

## Suggested CI

### proof

No CI.

## References

1. A reference.
"""


def manifest(problem_id: str, status: str = "ready") -> dict:
    return {
        "problem_id": problem_id,
        "status": status,
        "title": "A precise open problem",
        "problem_statement": "Prove or refute the statement for every input.",
        "verification_contract": {
            "proof": {
                "contract": "Submit a complete derivation covering every input.",
                "ci_contract": "Replay the supplied finite checks.",
            }
        },
        "verification_difficulty": {"score": 3, "rationale": "Local expert checks."},
    }


def make_candidate(tmp_path: Path, *, status: str = "ready", accepted: bool = True) -> Path:
    run = tmp_path / "run"
    repo = tmp_path / "solutions" / "ORP-0001-precise-open-problem"
    candidate = run / "candidates" / "CAN-1"
    repo.mkdir(parents=True)
    candidate.mkdir(parents=True)
    (repo / "README.md").write_text(readme(status), encoding="utf-8")
    (candidate / "problem.yaml").write_text(
        yaml.safe_dump(manifest("ORP-0001", status), sort_keys=False),
        encoding="utf-8",
    )
    state = {
        "candidates": {
            "CAN-1": {
                "status": "accepted" if accepted else "compile_pending",
                "problem_id": "ORP-0001",
                "topic_id": "math-0001",
                "problem_repo": str(repo),
            }
        },
        "stages": {"candidate.CAN-1.compile": {"status": "completed"}},
    }
    (run / "state.json").write_text(json.dumps(state), encoding="utf-8")
    return run


def test_discovers_only_accepted_completed_ready_problem(tmp_path: Path) -> None:
    run = make_candidate(tmp_path)
    found = publisher.discover_ready(run)
    assert [item.problem_id for item in found] == ["ORP-0001"]
    assert found[0].publication_id == "MATH-ORP-0001"
    assert found[0].project_path == "math-orp-0001-precise-open-problem"
    assert found[0].legacy_project_path == "orp-0001-precise-open-problem"


def test_publication_identity_uses_stable_discipline_prefix() -> None:
    assert publisher._publication_identity(
        "ORP-0040", "astr-0001", "ORP-0040-solar-dynamo"
    ) == ("ASTR-ORP-0040", "ASTR-ORP-0040-solar-dynamo")


def test_skips_non_ready_problem(tmp_path: Path) -> None:
    run = make_candidate(tmp_path, status="resolved-externally")
    assert publisher.discover_ready(run) == []


def test_skips_unaccepted_candidate(tmp_path: Path) -> None:
    run = make_candidate(tmp_path, accepted=False)
    assert publisher.discover_ready(run) == []


def test_canonical_problem_yaml_has_exact_schema() -> None:
    assert yaml.safe_load(publisher.render_problem_yaml(123)) == {
        "schema_version": 1,
        "owner_user_id": 123,
        "allowed_solution_types": ["general"],
    }


def test_rubric_includes_contract_ci_and_cross_cutting_checks() -> None:
    text = publisher.render_evaluation_rubric(manifest("ORP-0001"))
    assert "Submit a complete derivation" in text
    assert "Replay the supplied finite checks" in text
    assert "limitations, uncertainty" in text


def test_math_expectations_count_inline_display_and_fenced() -> None:
    source = "$x$\n\n$$y$$\n\n```math\nz\n```\n"
    assert publisher._math_expectations(source) == (3, 2)


def test_rendered_math_must_match_source_nodes() -> None:
    source = "$x$\n\n```math\ny\n```\n"
    html = '<span class="js-render-math"></span><div class="js-render-math" data-math-style="display"></div>'
    assert publisher.validate_rendered_math(source, html)["rendered_math_nodes"] == 2


def test_math_render_block_records_limit_without_changing_source(tmp_path: Path) -> None:
    run = make_candidate(tmp_path)
    problem = publisher.discover_ready(run)[0]
    source = "$x$\n" * 51
    block = publisher._math_render_block(problem, source, 50)
    assert block is not None
    assert block["reason"] == "gitlab_markdown_math_node_limit"
    assert block["expected_math_nodes"] == 51
    assert block["max_rendered_math_nodes"] == 50


def test_math_render_block_is_retried_after_source_or_limit_change(tmp_path: Path) -> None:
    run = make_candidate(tmp_path)
    problem = publisher.discover_ready(run)[0]
    previous = publisher._math_render_block(problem, "$x$\n" * 51, 50)
    assert previous is not None
    assert publisher._same_math_render_block(previous, dict(previous))
    changed_source = publisher._math_render_block(problem, "$y$\n" * 51, 50)
    changed_limit = publisher._math_render_block(problem, "$x$\n" * 51, 49)
    assert not publisher._same_math_render_block(previous, changed_source)
    assert not publisher._same_math_render_block(previous, changed_limit)
