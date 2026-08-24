from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

import pytest
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


def test_math_validation_chunks_are_lossless_and_bounded() -> None:
    source = "".join(f"Paragraph {index}: $x_{{{index}}}$.\n\n" for index in range(85))
    chunks = publisher.split_math_validation_chunks(source, 40)
    assert "".join(chunks) == source
    assert [publisher._math_expectations(chunk)[0] for chunk in chunks] == [40, 40, 5]


def test_math_validation_chunks_do_not_split_fenced_blocks() -> None:
    source = "Before $x$.\n\n```math\na + b = c\n\nd + e = f\n```\n\nAfter $y$.\n"
    chunks = publisher.split_math_validation_chunks(source, 2)
    assert "".join(chunks) == source
    assert "```math\na + b = c\n\nd + e = f\n```" in chunks[0]
    assert all(publisher._math_expectations(chunk)[0] <= 2 for chunk in chunks)


def test_math_validation_splits_one_oversized_paragraph_between_formulas() -> None:
    source = " ".join(f"$x_{{{index}}}$" for index in range(41)) + "\n"
    chunks = publisher.split_math_validation_chunks(source, 40)
    assert "".join(chunks) == source
    assert [publisher._math_expectations(chunk)[0] for chunk in chunks] == [40, 1]


class FakeGitLab:
    def __init__(self, invalid_formula: str | None = None) -> None:
        self.invalid_formula = invalid_formula
        self.calls: list[str] = []

    def render_markdown(self, source: str, project_path: str) -> str:
        self.calls.append(source)
        total, display = publisher._math_expectations(source)
        if self.invalid_formula and self.invalid_formula in source:
            total -= 1
        inline = total - display
        return (
            '<span class="js-render-math"></span>' * inline
            + '<div class="js-render-math" data-math-style="display"></div>' * display
        )


def test_math_validation_renders_every_bounded_chunk() -> None:
    source = "".join(f"Paragraph {index}: $x_{{{index}}}$.\n\n" for index in range(85))
    gitlab = FakeGitLab()
    result = publisher.validate_math_in_chunks(gitlab, source, "group/project", 40)
    assert result["rendered_math_nodes"] == 85
    assert result["validation_chunks"] == 3
    assert result["validation_requests"] == 3
    assert [publisher._math_expectations(chunk)[0] for chunk in gitlab.calls] == [40, 40, 5]


def test_math_validation_recursively_isolates_a_bad_formula() -> None:
    source = "Good $x$.\n\nBad $bad$.\n\nGood $y$.\n"
    gitlab = FakeGitLab(invalid_formula="$bad$")
    with pytest.raises(publisher.MathValidationError, match="isolated") as captured:
        publisher.validate_math_in_chunks(gitlab, source, "group/project", 40)
    assert captured.value.details["expected_math_nodes"] == 1
    assert len(gitlab.calls) > 1


def test_only_unchanged_genuine_validation_failures_are_skipped() -> None:
    source = "$x$\n"
    current = {
        "reason": "gitlab_markdown_validation_failed",
        "readme_sha256": publisher._sha256_text(source),
    }
    old_limit_block = {
        "reason": "gitlab_markdown_math_node_limit",
        "readme_sha256": publisher._sha256_text(source),
    }
    assert publisher._same_math_validation_block(current, source)
    assert not publisher._same_math_validation_block(current, "$y$\n")
    assert not publisher._same_math_validation_block(old_limit_block, source)
