#!/usr/bin/env python3
"""Publish compiled, ready research problems to a Gewu GitLab instance.

The publisher is deliberately separate from the discovery campaign.  It only
consumes completed compile outputs and never changes campaign state.  A problem
is eligible only when all three independent signals agree:

* the candidate is ``accepted``;
* the candidate compile stage is ``completed``;
* the compiled internal manifest and public README both say ``ready``.

Authentication is read from a mode-0600 token file.  The token is never placed
in a Git remote URL, command argument, ledger, or log message.
"""

from __future__ import annotations

import argparse
import base64
from concurrent.futures import ThreadPoolExecutor, as_completed
import contextlib
import dataclasses
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import time
from typing import Any, Iterable, Iterator
import urllib.error
import urllib.parse
import urllib.request

import yaml


CANONICAL_SECTIONS = (
    "Background",
    "Problem Statement",
    "Current Progress",
    "Scientific Significance",
    "Answer Types",
    "Verification Standard",
    "Suggested CI",
    "References",
)
SECRET_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)(?:api[_ -]?key|access[_ -]?token|password)\s*[:=]\s*\S+"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\bglpat-[A-Za-z0-9_-]{12,}\b"),
)
STATUS_RE = re.compile(
    r"^## Current Progress\s*$.*?^- Status:\s*`([^`]+)`\s*$",
    flags=re.MULTILINE | re.DOTALL,
)
DEFAULT_MATH_VALIDATION_CHUNK_SIZE = 40
DISCIPLINE_CODES = {
    "math": "MATH",
    "phys": "PHYS",
    "physics": "PHYS",
    "chem": "CHEM",
    "earth": "EARTH",
    "bio": "BIO",
    "biol": "BIO",
    "biology": "BIO",
    "med": "MED",
    "medicine": "MED",
    "info": "INFO",
    "cs": "INFO",
    "astr": "ASTR",
    "astro": "ASTR",
}


class PublishError(RuntimeError):
    """A safe, user-actionable publication failure."""


class MathValidationError(PublishError):
    """The README contains mathematics that GitLab did not render faithfully."""

    def __init__(self, message: str, details: dict[str, Any]) -> None:
        super().__init__(message)
        self.details = details


@dataclasses.dataclass(frozen=True)
class ReadyProblem:
    candidate_id: str
    problem_id: str
    publication_id: str
    topic_id: str
    repo_dir: Path
    internal_manifest: Path
    title: str
    project_name: str
    project_path: str
    legacy_project_path: str


def _json_load_retry(path: Path, attempts: int = 4) -> dict[str, Any]:
    for attempt in range(attempts):
        try:
            with path.open("r", encoding="utf-8") as handle:
                value = json.load(handle)
            if not isinstance(value, dict):
                raise PublishError(f"expected an object in {path}")
            return value
        except json.JSONDecodeError:
            if attempt + 1 == attempts:
                raise
            time.sleep(0.2 * (attempt + 1))
    raise AssertionError("unreachable")


def _yaml_load(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = yaml.safe_load(handle)
    if not isinstance(value, dict):
        raise PublishError(f"expected an object in {path}")
    return value


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _project_path(repo_name: str) -> str:
    value = repo_name.lower()
    value = re.sub(r"[^a-z0-9._-]+", "-", value)
    value = re.sub(r"[-_.]{2,}", "-", value).strip("-_.")
    if not value:
        raise PublishError(f"cannot derive a GitLab project path from {repo_name!r}")
    return value[:240].rstrip("-_.")


def _publication_identity(
    problem_id: str, topic_id: str, repo_name: str
) -> tuple[str, str]:
    topic_prefix = topic_id.partition("-")[0].strip().lower()
    discipline = DISCIPLINE_CODES.get(topic_prefix)
    if discipline is None:
        raise PublishError(f"unsupported discipline prefix in topic ID: {topic_id!r}")
    publication_id = f"{discipline}-{problem_id}"
    prefix = problem_id + "-"
    suffix = repo_name[len(problem_id) :] if repo_name.startswith(prefix) else f"-{repo_name}"
    return publication_id, publication_id + suffix


def _readme_status(text: str) -> str | None:
    match = STATUS_RE.search(text)
    return match.group(1).strip() if match else None


def _validate_readme(text: str, path: Path) -> list[str]:
    errors: list[str] = []
    if not text.startswith("# "):
        errors.append("README.md must start with a level-one title")
    found = tuple(
        match.group(1).strip()
        for match in re.finditer(r"^## (.+)$", text, flags=re.MULTILINE)
    )
    if found != CANONICAL_SECTIONS:
        errors.append("README.md does not have the canonical section order")
    if _readme_status(text) != "ready":
        errors.append("README.md Current Progress status is not ready")
    if re.search(r"(?<!\\)\\\(|(?<!\\)\\\)|(?<!\\)\\\[|(?<!\\)\\\]", text):
        errors.append("README.md contains unsupported \\( \\) or \\[ \\] math delimiters")
    for pattern in SECRET_PATTERNS:
        if pattern.search(text):
            errors.append("README.md may contain secret material")
            break
    if not text.endswith("\n"):
        errors.append("README.md must end with a newline")
    return [f"{path}: {error}" for error in errors]


def discover_ready(run_dir: Path) -> list[ReadyProblem]:
    state_path = run_dir / "state.json"
    state = _json_load_retry(state_path)
    candidates = state.get("candidates") or {}
    stages = state.get("stages") or {}
    if not isinstance(candidates, dict) or not isinstance(stages, dict):
        raise PublishError(f"invalid campaign state structure in {state_path}")

    ready: list[ReadyProblem] = []
    for candidate_id, candidate in candidates.items():
        if not isinstance(candidate, dict) or candidate.get("status") != "accepted":
            continue
        compile_stage = stages.get(f"candidate.{candidate_id}.compile") or {}
        if compile_stage.get("status") != "completed":
            continue
        repo_value = str(candidate.get("problem_repo") or "").strip()
        problem_id = str(candidate.get("problem_id") or "").strip()
        if not repo_value or not problem_id:
            continue
        repo_dir = Path(repo_value).resolve()
        readme_path = repo_dir / "README.md"
        manifest_path = run_dir / "candidates" / str(candidate_id) / "problem.yaml"
        if not readme_path.is_file() or not manifest_path.is_file():
            continue
        readme = readme_path.read_text(encoding="utf-8")
        manifest = _yaml_load(manifest_path)
        if manifest.get("status") != "ready" or _readme_status(readme) != "ready":
            continue
        if str(manifest.get("problem_id") or "") != problem_id:
            raise PublishError(
                f"problem ID mismatch for {candidate_id}: state={problem_id}, "
                f"manifest={manifest.get('problem_id')}"
            )
        publication_id, project_name = _publication_identity(
            problem_id, str(candidate.get("topic_id") or ""), repo_dir.name
        )
        ready.append(
            ReadyProblem(
                candidate_id=str(candidate_id),
                problem_id=problem_id,
                publication_id=publication_id,
                topic_id=str(candidate.get("topic_id") or ""),
                repo_dir=repo_dir,
                internal_manifest=manifest_path,
                title=str(manifest.get("title") or problem_id),
                project_name=project_name,
                project_path=_project_path(project_name),
                legacy_project_path=_project_path(repo_dir.name),
            )
        )
    return sorted(ready, key=lambda item: item.problem_id)


def render_problem_yaml(owner_user_id: int) -> str:
    if owner_user_id <= 0:
        raise PublishError("GitLab owner user ID must be a positive integer")
    return (
        "schema_version: 1\n"
        f"owner_user_id: {owner_user_id}\n"
        "allowed_solution_types:\n"
        "  - general\n"
    )


def _clean_public_text(value: Any, fallback: str) -> str:
    text = str(value or "").strip()
    return text if text else fallback


def render_evaluation_rubric(manifest: dict[str, Any]) -> str:
    problem_id = _clean_public_text(manifest.get("problem_id"), "unknown")
    title = _clean_public_text(manifest.get("title"), "Open Research Problem")
    statement = _clean_public_text(
        manifest.get("problem_statement"),
        "Refer to README.md for the complete problem statement.",
    )
    contract = manifest.get("verification_contract") or {}
    if not isinstance(contract, dict) or not contract:
        raise PublishError(f"{problem_id} has no verification_contract")
    difficulty = manifest.get("verification_difficulty") or {}

    lines = [
        "# Evaluation Rubric",
        "",
        "## Scope",
        "",
        f"This rubric evaluates a Solution to `{problem_id}`: **{title}**.",
        "The Solution must answer the full problem in `README.md`; a proxy, "
        "benchmark-only result, or silently narrowed special case does not pass.",
        "",
        "## Required output",
        "",
        statement,
        "",
        "The submission must be self-contained, identify its answer type, and "
        "provide the derivation, computation, experiment, dataset, or other "
        "durable evidence required by the applicable criterion below.",
        "",
        "## Acceptance criteria by answer type",
        "",
    ]
    for answer_type, entry_value in contract.items():
        entry = entry_value if isinstance(entry_value, dict) else {}
        lines.extend(
            [
                f"### {answer_type}",
                "",
                _clean_public_text(
                    entry.get("contract"),
                    "No complete acceptance contract was supplied; manual review is required.",
                ),
                "",
                "#### Reproducibility check",
                "",
                _clean_public_text(
                    entry.get("ci_contract"),
                    "No mechanical CI is prescribed. The reviewer must reproduce "
                    "or independently inspect all load-bearing evidence.",
                ),
                "",
            ]
        )

    score = difficulty.get("score")
    rationale = _clean_public_text(
        difficulty.get("rationale"),
        "The independent-review burden was not separately described.",
    )
    lines.extend(
        [
            "## Cross-cutting checks",
            "",
            "A Solution passes only if all applicable checks below pass:",
            "",
            "- every required output in the Problem is present;",
            "- assumptions, definitions, units, parameter ranges, and tolerances "
            "match the Problem;",
            "- material claims are linked to inspectable evidence and reproducible "
            "procedures;",
            "- stochastic work records seeds, repetition policy, and statistical "
            "criteria when relevant;",
            "- numerical work records units, tolerances, and sensitivity or error "
            "analysis when relevant;",
            "- the Solution reports limitations, uncertainty, and at least one "
            "relevant boundary or failure mode;",
            "- the result is not already implied by a cited prior result unless the "
            "submission clearly establishes a stronger result;",
            "- all referenced artifacts exist at stable relative paths and contain "
            "no credentials, private prompts, or hidden transcripts.",
            "",
            "## Independent review burden",
            "",
            f"Recorded verification difficulty: `{score}/10`.",
            "",
            rationale,
            "",
        ]
    )
    return "\n".join(lines)


def canonical_files(problem: ReadyProblem, owner_user_id: int) -> dict[Path, str]:
    manifest = _yaml_load(problem.internal_manifest)
    readme_path = problem.repo_dir / "README.md"
    readme = readme_path.read_text(encoding="utf-8")
    errors = _validate_readme(readme, readme_path)
    if errors:
        raise PublishError("; ".join(errors))
    return {
        Path("README.md"): readme,
        Path("problem.yaml"): render_problem_yaml(owner_user_id),
        Path("docs/evaluation-rubric.md"): render_evaluation_rubric(manifest),
    }


def _ensure_no_secret(paths: Iterable[tuple[Path, str]]) -> None:
    for path, text in paths:
        for pattern in SECRET_PATTERNS:
            if pattern.search(text):
                raise PublishError(f"refusing to publish possible secret material in {path}")


def prepare_repository(problem: ReadyProblem, owner_user_id: int) -> str:
    files = canonical_files(problem, owner_user_id)
    _ensure_no_secret(files.items())
    for relative, text in files.items():
        path = problem.repo_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.is_file() or path.read_text(encoding="utf-8") != text:
            path.write_text(text, encoding="utf-8")

    _git(problem.repo_dir, "add", "README.md", "problem.yaml", "docs/evaluation-rubric.md")
    diff = _git(problem.repo_dir, "diff", "--cached", "--quiet", check=False)
    if diff.returncode == 1:
        _git(
            problem.repo_dir,
            "-c",
            "user.name=Gewu Problem Publisher",
            "-c",
            "user.email=publisher@gewulab.local",
            "commit",
            "-m",
            f"Add canonical Gewu problem metadata for {problem.problem_id}",
        )
    elif diff.returncode != 0:
        raise PublishError(f"git staging check failed for {problem.problem_id}")
    return _git(problem.repo_dir, "rev-parse", "HEAD").stdout.strip()


def _git(cwd: Path, *args: str, check: bool = True, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise PublishError(f"git {' '.join(args[:2])} failed: {detail}")
    return result


def _token_from_file(path: Path) -> str:
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except FileNotFoundError as error:
        raise PublishError(f"GitLab token file does not exist: {path}") from error
    if mode & 0o077:
        raise PublishError(f"GitLab token file must be mode 0600: {path}")
    token = path.read_text(encoding="utf-8").strip()
    if not token:
        raise PublishError(f"GitLab token file is empty: {path}")
    return token


class GitLab:
    def __init__(self, base_url: str, token: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_url = self.base_url + "/api/v4"
        self.token = token

    def request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        expected: tuple[int, ...] = (200,),
    ) -> Any:
        body = None
        headers = {
            "Authorization": f"Bearer {self.token}",
            "User-Agent": "gewulab-ready-publisher/1.0",
        }
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            self.api_url + path,
            data=body,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                raw = response.read()
                if response.status not in expected:
                    raise PublishError(f"GitLab API returned HTTP {response.status} for {path}")
        except urllib.error.HTTPError as error:
            raw = error.read().decode("utf-8", "replace")[:1000]
            raise PublishError(
                f"GitLab API {method} {path} failed with HTTP {error.code}: {raw}"
            ) from error
        if not raw:
            return None
        return json.loads(raw.decode("utf-8"))

    def current_user(self) -> dict[str, Any]:
        value = self.request("GET", "/user")
        if not isinstance(value, dict):
            raise PublishError("GitLab /user did not return an object")
        return value

    def namespace(self, full_path: str) -> dict[str, Any]:
        query = urllib.parse.urlencode({"search": full_path, "per_page": 100})
        values = self.request("GET", f"/namespaces?{query}")
        for value in values or []:
            if str(value.get("full_path") or "") == full_path:
                return value
        raise PublishError(f"GitLab namespace is not visible or writable: {full_path}")

    def project(self, full_path: str) -> dict[str, Any] | None:
        encoded = urllib.parse.quote(full_path, safe="")
        try:
            value = self.request("GET", f"/projects/{encoded}")
        except PublishError as error:
            if "HTTP 404" in str(error):
                return None
            raise
        return value

    def ensure_project(
        self,
        *,
        namespace_id: int,
        namespace: str,
        problem: ReadyProblem,
        visibility: str,
    ) -> dict[str, Any]:
        full_path = f"{namespace}/{problem.project_path}"
        existing = self.project(full_path)
        if existing is not None:
            return existing
        legacy_full_path = f"{namespace}/{problem.legacy_project_path}"
        legacy = self.project(legacy_full_path)
        if legacy is not None:
            return self.update_project_identity(legacy, problem)
        value = self.request(
            "POST",
            "/projects",
            payload={
                "name": problem.project_name,
                "path": problem.project_path,
                "namespace_id": namespace_id,
                "visibility": visibility,
                "description": (
                    f"{problem.publication_id} (source {problem.problem_id}): {problem.title}"
                )[:255],
                "initialize_with_readme": False,
            },
            expected=(201,),
        )
        if not isinstance(value, dict):
            raise PublishError(f"GitLab did not return the created project for {problem.problem_id}")
        return value

    def update_project_identity(
        self, project: dict[str, Any], problem: ReadyProblem
    ) -> dict[str, Any]:
        value = self.request(
            "PUT",
            f"/projects/{int(project['id'])}",
            payload={
                "name": problem.project_name,
                "path": problem.project_path,
                "description": (
                    f"{problem.publication_id} (source {problem.problem_id}): {problem.title}"
                )[:255],
            },
            expected=(200,),
        )
        if not isinstance(value, dict):
            raise PublishError(
                f"GitLab did not return the renamed project for {problem.problem_id}"
            )
        return value

    def render_markdown(self, text: str, project_path: str) -> str:
        value = self.request(
            "POST",
            "/markdown",
            payload={"text": text, "gfm": True, "project": project_path},
            expected=(200, 201),
        )
        if not isinstance(value, dict) or not isinstance(value.get("html"), str):
            raise PublishError("GitLab markdown endpoint did not return rendered HTML")
        return value["html"]

    def raw_readme(self, project_id: int, ref: str = "main") -> str:
        encoded = urllib.parse.quote("README.md", safe="")
        query = urllib.parse.urlencode({"ref": ref})
        request = urllib.request.Request(
            f"{self.api_url}/projects/{project_id}/repository/files/{encoded}/raw?{query}",
            headers={
                "Authorization": f"Bearer {self.token}",
                "User-Agent": "gewulab-ready-publisher/1.0",
            },
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                return response.read().decode("utf-8")
        except urllib.error.HTTPError as error:
            raise PublishError(
                f"failed to verify published README for project {project_id}: HTTP {error.code}"
            ) from error


def _math_spans(text: str) -> list[tuple[int, int, bool]]:
    """Locate non-overlapping fenced, display, and inline math expressions."""

    spans: list[tuple[int, int, bool]] = []
    patterns = (
        (re.compile(r"^```math\s*$.*?^```\s*$", flags=re.MULTILINE | re.DOTALL), True),
        (re.compile(r"\$\$.*?\$\$", flags=re.DOTALL), True),
        (re.compile(r"(?<!\$)\$(?!\$)(?:\\.|[^$\n])+?(?<!\$)\$(?!\$)"), False),
    )
    for pattern, display in patterns:
        for match in pattern.finditer(text):
            if any(match.start() < end and match.end() > start for start, end, _ in spans):
                continue
            spans.append((match.start(), match.end(), display))
    return sorted(spans)


def _math_expectations(text: str) -> tuple[int, int]:
    spans = _math_spans(text)
    return len(spans), sum(display for _, _, display in spans)


def _markdown_validation_units(source: str) -> list[str]:
    """Split Markdown at paragraph and fenced-block boundaries, losslessly."""

    units: list[str] = []
    current: list[str] = []
    fence_character = ""
    fence_length = 0
    for line in source.splitlines(keepends=True):
        if fence_character:
            current.append(line)
            closing = re.match(
                rf"^ {{0,3}}{re.escape(fence_character)}{{{fence_length},}}\s*$",
                line,
            )
            if closing:
                units.append("".join(current))
                current = []
                fence_character = ""
                fence_length = 0
            continue

        opening = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if opening:
            if current:
                units.append("".join(current))
            marker = opening.group(1)
            current = [line]
            fence_character = marker[0]
            fence_length = len(marker)
            continue

        current.append(line)
        if not line.strip():
            units.append("".join(current))
            current = []

    if current:
        units.append("".join(current))
    if "".join(units) != source:
        raise PublishError("internal error: Markdown validation split was not lossless")
    return units


def split_math_validation_chunks(
    source: str, max_math_nodes: int = DEFAULT_MATH_VALIDATION_CHUNK_SIZE
) -> list[str]:
    """Return a lossless sequence of renderable chunks below GitLab's API cap."""

    if max_math_nodes < 1:
        raise PublishError("math validation chunk size must be at least 1")
    expanded_units: list[str] = []
    for unit in _markdown_validation_units(source):
        spans = _math_spans(unit)
        if len(spans) <= max_math_nodes:
            expanded_units.append(unit)
            continue
        start = 0
        for next_index in range(max_math_nodes, len(spans), max_math_nodes):
            cut = spans[next_index][0]
            expanded_units.append(unit[start:cut])
            start = cut
        expanded_units.append(unit[start:])

    chunks: list[str] = []
    current: list[str] = []
    current_math_nodes = 0
    for unit in expanded_units:
        unit_math_nodes, _ = _math_expectations(unit)
        if unit_math_nodes > max_math_nodes:
            raise PublishError("internal error: a validation chunk split a formula incorrectly")
        if current and current_math_nodes + unit_math_nodes > max_math_nodes:
            chunks.append("".join(current))
            current = []
            current_math_nodes = 0
        current.append(unit)
        current_math_nodes += unit_math_nodes
    if current or not chunks:
        chunks.append("".join(current))
    if "".join(chunks) != source:
        raise PublishError("internal error: Markdown validation chunks changed the source")
    return chunks


def _same_math_validation_block(previous: Any, source: str) -> bool:
    """Skip only unchanged, genuine syntax failures; old 50-node blocks retry."""

    return (
        isinstance(previous, dict)
        and previous.get("reason") == "gitlab_markdown_validation_failed"
        and previous.get("readme_sha256") == _sha256_text(source)
    )


def validate_rendered_math(source: str, html: str) -> dict[str, int]:
    expected_total, expected_display = _math_expectations(source)
    rendered_total = len(re.findall(r"\bjs-render-math\b", html))
    rendered_display = len(re.findall(r"\bdata-math-style=[\"']display[\"']", html))
    if expected_total != rendered_total or expected_display != rendered_display:
        raise PublishError(
            "GitLab math render mismatch: "
            f"expected {expected_total} math nodes/{expected_display} display, "
            f"rendered {rendered_total}/{rendered_display}"
        )
    return {
        "expected_math_nodes": expected_total,
        "rendered_math_nodes": rendered_total,
        "expected_display_nodes": expected_display,
        "rendered_display_nodes": rendered_display,
    }


def _sum_render_results(results: Iterable[dict[str, int]]) -> dict[str, int]:
    totals = {
        "expected_math_nodes": 0,
        "rendered_math_nodes": 0,
        "expected_display_nodes": 0,
        "rendered_display_nodes": 0,
    }
    for result in results:
        for key in totals:
            totals[key] += int(result[key])
    return totals


def _render_and_validate_math_chunk(
    gitlab: Any,
    source: str,
    project_path: str,
    *,
    label: str,
    max_math_nodes: int,
    request_counter: list[int],
) -> dict[str, int]:
    request_counter[0] += 1
    rendered = gitlab.render_markdown(source, project_path)
    try:
        return validate_rendered_math(source, rendered)
    except PublishError as error:
        expected_total, expected_display = _math_expectations(source)
        details: dict[str, Any] = {
            "validation_chunk": label,
            "expected_math_nodes": expected_total,
            "expected_display_nodes": expected_display,
        }
        if expected_total <= 1:
            raise MathValidationError(
                f"{error} (isolated in {label})",
                details,
            ) from error

        smaller_limit = max(1, min(max_math_nodes - 1, expected_total // 2))
        try:
            subchunks = split_math_validation_chunks(source, smaller_limit)
        except PublishError as split_error:
            raise MathValidationError(
                f"{error} ({label}; unable to isolate at a paragraph or fenced-block boundary: "
                f"{split_error})",
                details,
            ) from error
        if len(subchunks) <= 1:
            raise MathValidationError(
                f"{error} ({label}; unable to isolate below this Markdown block)",
                details,
            ) from error

        results = []
        for index, subchunk in enumerate(subchunks, start=1):
            results.append(
                _render_and_validate_math_chunk(
                    gitlab,
                    subchunk,
                    project_path,
                    label=f"{label}.{index}/{len(subchunks)}",
                    max_math_nodes=smaller_limit,
                    request_counter=request_counter,
                )
            )
        return _sum_render_results(results)


def validate_math_in_chunks(
    gitlab: Any,
    source: str,
    project_path: str,
    max_math_nodes: int = DEFAULT_MATH_VALIDATION_CHUNK_SIZE,
) -> dict[str, int]:
    """Validate an exact README copy through bounded, lossless render requests."""

    chunks = split_math_validation_chunks(source, max_math_nodes)
    request_counter = [0]
    results = []
    for index, chunk in enumerate(chunks, start=1):
        results.append(
            _render_and_validate_math_chunk(
                gitlab,
                chunk,
                project_path,
                label=f"{index}/{len(chunks)}",
                max_math_nodes=max_math_nodes,
                request_counter=request_counter,
            )
        )
    totals = _sum_render_results(results)
    totals.update(
        {
            "validation_chunks": len(chunks),
            "validation_requests": request_counter[0],
            "max_math_nodes_per_chunk": max_math_nodes,
        }
    )
    return totals


@contextlib.contextmanager
def _askpass(token: str) -> Iterator[dict[str, str]]:
    with tempfile.TemporaryDirectory(prefix="gewulab-askpass-") as directory:
        script = Path(directory) / "askpass.py"
        script.write_text(
            "#!/usr/bin/env python3\n"
            "import os, sys\n"
            "prompt = sys.argv[1].lower() if len(sys.argv) > 1 else ''\n"
            "print('oauth2' if 'username' in prompt else os.environ['GEWU_GITLAB_TOKEN'])\n",
            encoding="utf-8",
        )
        script.chmod(0o700)
        env = os.environ.copy()
        env.update(
            {
                "GIT_ASKPASS": str(script),
                "GIT_TERMINAL_PROMPT": "0",
                "GEWU_GITLAB_TOKEN": token,
            }
        )
        yield env


def push_repository(problem: ReadyProblem, http_url: str, token: str) -> str:
    remote_name = "gewulab"
    current = _git(problem.repo_dir, "remote", "get-url", remote_name, check=False)
    if current.returncode == 0:
        if current.stdout.strip() != http_url:
            _git(problem.repo_dir, "remote", "set-url", remote_name, http_url)
    else:
        _git(problem.repo_dir, "remote", "add", remote_name, http_url)
    with _askpass(token) as env:
        _git(
            problem.repo_dir,
            "push",
            "--set-upstream",
            remote_name,
            "HEAD:main",
            env=env,
        )
    return _git(problem.repo_dir, "rev-parse", "HEAD").stdout.strip()


def _load_ledger(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"schema_version": 1, "published": {}, "blocked": {}}
    value = _json_load_retry(path)
    value.setdefault("schema_version", 1)
    value.setdefault("published", {})
    value.setdefault("blocked", {})
    return value


def _save_ledger(path: Path, ledger: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(ledger, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(str(temporary), str(path))


def _print_event(event: str, **fields: Any) -> None:
    safe = {"event": event, **fields}
    print(json.dumps(safe, ensure_ascii=False, sort_keys=True), flush=True)


def publish(args: argparse.Namespace) -> int:
    run_dir = args.run_dir.resolve()
    ledger_path = (args.ledger or run_dir / "gewulab-publication" / "ledger.json").resolve()
    lock_path = ledger_path.with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        ready = discover_ready(run_dir)
        ledger = _load_ledger(ledger_path)
        published = ledger["published"]
        blocked = ledger["blocked"]
        pending: list[ReadyProblem] = []
        unchanged_blocked = 0
        for item in ready:
            if item.problem_id in published:
                continue
            source = (item.repo_dir / "README.md").read_text(encoding="utf-8")
            if (
                not args.retry_blocked
                and _same_math_validation_block(blocked.get(item.problem_id), source)
            ):
                unchanged_blocked += 1
                continue
            pending.append(item)
        if args.problem_id:
            pending = [item for item in pending if item.problem_id == args.problem_id]
        if args.max_items is not None:
            pending = pending[: args.max_items]

        _print_event(
            "scan",
            ready=len(ready),
            already_published=sum(item.problem_id in published for item in ready),
            unchanged_validation_blocked=unchanged_blocked,
            selected=len(pending),
        )
        if args.dry_run:
            for item in pending:
                source = (item.repo_dir / "README.md").read_text(encoding="utf-8")
                chunks = split_math_validation_chunks(
                    source, args.math_validation_chunk_size
                )
                expected_math_nodes, expected_display_nodes = _math_expectations(source)
                _print_event(
                    "would_publish",
                    problem_id=item.problem_id,
                    publication_id=item.publication_id,
                    candidate_id=item.candidate_id,
                    topic_id=item.topic_id,
                    project_path=item.project_path,
                    expected_math_nodes=expected_math_nodes,
                    expected_display_nodes=expected_display_nodes,
                    validation_chunks=len(chunks),
                )
            return 0
        if not pending:
            return 0

        token = _token_from_file(args.token_file.resolve())
        gitlab = GitLab(args.gitlab_url, token)
        user = gitlab.current_user()
        owner_user_id = int(user["id"])
        namespace = args.namespace or str(user.get("username") or "")
        if not namespace:
            raise PublishError("cannot determine the GitLab namespace")
        namespace_value = gitlab.namespace(namespace)
        namespace_id = int(namespace_value["id"])

        if args.workers < 1:
            raise PublishError("--workers must be at least 1")

        def publish_one(problem: ReadyProblem) -> dict[str, Any]:
            commit = prepare_repository(problem, owner_user_id)
            project = gitlab.ensure_project(
                namespace_id=namespace_id,
                namespace=namespace,
                problem=problem,
                visibility=args.visibility,
            )
            full_path = str(project["path_with_namespace"])
            readme = (problem.repo_dir / "README.md").read_text(encoding="utf-8")
            render_result = validate_math_in_chunks(
                gitlab,
                readme,
                full_path,
                args.math_validation_chunk_size,
            )
            pushed_commit = push_repository(
                problem, str(project["http_url_to_repo"]), token
            )
            remote_readme = gitlab.raw_readme(int(project["id"]))
            if _sha256_text(remote_readme) != _sha256_text(readme):
                raise PublishError(
                    "published README content does not match the local README"
                )
            return {
                "candidate_id": problem.candidate_id,
                "publication_id": problem.publication_id,
                "topic_id": problem.topic_id,
                "project_id": int(project["id"]),
                "project_path": full_path,
                "project_url": str(project["web_url"]),
                "commit": pushed_commit,
                "readme_sha256": _sha256_text(readme),
                "render_validation": render_result,
                "published_at": time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
                ),
            }

        failures = 0
        with ThreadPoolExecutor(
            max_workers=min(args.workers, len(pending)),
            thread_name_prefix="gewulab-publish",
        ) as executor:
            future_to_problem = {
                executor.submit(publish_one, problem): problem
                for problem in pending
            }
            for future in as_completed(future_to_problem):
                problem = future_to_problem[future]
                try:
                    publication = future.result()
                    published[problem.problem_id] = publication
                    blocked.pop(problem.problem_id, None)
                    _save_ledger(ledger_path, ledger)
                    _print_event(
                        "published",
                        problem_id=problem.problem_id,
                        publication_id=problem.publication_id,
                        project_path=publication["project_path"],
                        project_url=publication["project_url"],
                        commit=publication["commit"],
                    )
                except MathValidationError as error:
                    failures += 1
                    source = (problem.repo_dir / "README.md").read_text(
                        encoding="utf-8"
                    )
                    validation_block = {
                        "candidate_id": problem.candidate_id,
                        "publication_id": problem.publication_id,
                        "topic_id": problem.topic_id,
                        "reason": "gitlab_markdown_validation_failed",
                        "readme_sha256": _sha256_text(source),
                        "error": str(error),
                        **error.details,
                        "blocked_at": time.strftime(
                            "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
                        ),
                    }
                    blocked[problem.problem_id] = validation_block
                    _save_ledger(ledger_path, ledger)
                    _print_event(
                        "math_validation_failed",
                        problem_id=problem.problem_id,
                        **validation_block,
                    )
                except Exception as error:  # isolate one Problem from the batch
                    failures += 1
                    _print_event(
                        "failed",
                        problem_id=problem.problem_id,
                        error=f"{type(error).__name__}: {error}",
                    )
        return 1 if failures else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--gitlab-url", default=os.environ.get("GEWU_GITLAB_URL", "https://git.gewu-lab.ai"))
    parser.add_argument("--namespace", default=os.environ.get("GEWU_GITLAB_NAMESPACE"))
    parser.add_argument(
        "--token-file",
        type=Path,
        default=Path(os.environ.get("GEWU_GITLAB_TOKEN_FILE", "/root/.config/gewulab-publisher/token")),
    )
    parser.add_argument("--visibility", choices=("private", "internal", "public"), default="internal")
    parser.add_argument("--ledger", type=Path)
    parser.add_argument("--problem-id")
    parser.add_argument("--max-items", type=int)
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="number of Problems to publish concurrently (default: 1)",
    )
    parser.add_argument(
        "--math-validation-chunk-size",
        type=int,
        default=int(
            os.environ.get(
                "GEWU_GITLAB_MATH_VALIDATION_CHUNK_SIZE",
                str(DEFAULT_MATH_VALIDATION_CHUNK_SIZE),
            )
        ),
        help="maximum formulas per temporary GitLab Markdown validation chunk",
    )
    parser.add_argument(
        "--retry-blocked",
        action="store_true",
        help="re-evaluate unchanged render-blocked Problems",
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if not 1 <= args.math_validation_chunk_size <= 40:
            raise PublishError(
                "math validation chunk size must be between 1 and 40"
            )
        return publish(args)
    except BlockingIOError:
        _print_event("skipped", reason="another publisher process holds the lock")
        return 0
    except Exception as error:
        _print_event("fatal", error=f"{type(error).__name__}: {error}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
