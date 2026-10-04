from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .common import iter_text_files, slugify, today


README_SECTIONS = (
    "Background",
    "Problem Statement",
    "Current Progress",
    "References",
)

README_ZH_SECTIONS = (
    "背景",
    "题面",
    "现有进展",
    "相关文献引用",
)


def _text(value: object, fallback: str = "To be completed.") -> str:
    rendered = str(value or "").strip()
    return rendered or fallback


def _public_text(value: object, fallback: str = "To be completed.") -> str:
    rendered = _text(value, "")
    replacements = {
        (
            "This repository tracks the primary canonical target. "
            "Related source-paper questions are preserved in OPEN_QUESTIONS.md."
        ): (
            "This repository tracks only the canonical target stated above; "
            "neighboring questions in the source LKM record are outside its scope."
        ),
        "submission/candidate.json": "`candidate.json`",
        "submission/solution.md": "`solution.md`",
        "under submission/": "included in the final submission",
    }
    for old, new in replacements.items():
        rendered = rendered.replace(old, new)
    return rendered or fallback


def render_problem_metadata(problem: dict[str, Any]) -> str:
    """Keep non-statement scientific assessments outside the README."""

    metadata = {
        "solution_difficulty": problem.get("solution_difficulty") or [],
        "scientific_significance": problem.get("scientific_significance") or {},
    }
    difficulty = problem.get("verification_difficulty")
    if difficulty is not None:
        metadata["verification_difficulty"] = difficulty
    return json.dumps(metadata, ensure_ascii=False, indent=2) + "\n"


def normalize_github_math(text: str) -> str:
    """Normalize common LaTeX delimiters to GitHub Flavored Markdown."""

    text = re.sub(r"(?<!\\)\\\[", "$$", text)
    text = re.sub(r"(?<!\\)\\\]", "$$", text)
    text = re.sub(r"(?<!\\)\\\(", "$", text)
    return re.sub(r"(?<!\\)\\\)", "$", text)


def render_problem_readme(problem: dict[str, Any]) -> str:
    """Render the human-facing projection of a Problem Schema v1.0 record."""

    background = _public_text(problem.get("background"), "")
    background_lines = (
        background.splitlines()
        if background
        else [
            "The scientific context and the relation to the source formulation "
            "remain to be completed."
        ]
    )
    progress = [
        str(item).strip()
        for item in problem.get("previous_progress") or []
        if str(item).strip()
    ]
    references = [
        str(item).strip()
        for item in problem.get("references") or []
        if str(item).strip()
    ]

    lines = [
        f"# {_text(problem.get('title'), 'Open Research Problem')}",
        "",
        _public_text(problem.get("abstract")),
        "",
        "## Background",
        "",
        *background_lines,
        "",
        "## Problem Statement",
        "",
        _public_text(problem.get("problem_statement")),
        "",
    ]
    lines.extend(
        [
            "## Current Progress",
            "",
            f"- Status: `{_text(problem.get('status'))}`",
            "",
        ]
    )
    if progress:
        for item in progress:
            lines.extend(_public_text(item).splitlines())
            lines.append("")
    else:
        lines.extend(["No prior progress is recorded yet.", ""])
    lines.extend(["## References", ""])
    if references:
        lines.extend(
            f"{index}. {_public_text(reference)}"
            for index, reference in enumerate(references, start=1)
        )
    else:
        lines.append("1. Verified primary references remain to be added.")
    lines.append("")
    return normalize_github_math("\n".join(lines).rstrip() + "\n")


def validate_problem_readme(path: Path) -> list[str]:
    if not path.is_file():
        return ["missing README.md"]
    text = path.read_text(encoding="utf-8")
    errors: list[str] = []
    if not text.startswith("# "):
        errors.append("README.md must start with a problem title")
    scientific_text = text.split("## References", maxsplit=1)[0]
    if re.search(r"[\u3400-\u4dbf\u4e00-\u9fff]", scientific_text):
        errors.append(
            "README.md scientific sections must be English; source-language "
            "bibliographic text is allowed only under References"
        )
    positions = []
    for section in README_SECTIONS:
        marker = f"## {section}"
        position = text.find(marker)
        if position < 0:
            errors.append(f"README.md is missing section: {section}")
        positions.append(position)
    present = [position for position in positions if position >= 0]
    if present != sorted(present):
        errors.append("README.md sections are out of order")
    actual_sections = tuple(
        match.group(1).strip()
        for match in re.finditer(r"^## (.+)$", text, flags=re.MULTILINE)
    )
    if actual_sections != README_SECTIONS:
        errors.append(
            "README.md must contain exactly the four canonical top-level sections"
        )
    for section in README_ZH_SECTIONS:
        if f"## {section}" in text:
            errors.append(
                f"README.md uses a Chinese translation heading: {section}; "
                "put Chinese content in README.zh-CN.md"
            )
    if re.search(r"(?<!\\)\\\(|(?<!\\)\\\)", text):
        errors.append(r"README.md uses \( ... \); GitHub inline math must use $ ... $")
    if re.search(r"(?<!\\)\\\[|(?<!\\)\\\]", text):
        errors.append(
            r"README.md uses \[ ... \]; GitHub display math must use $$ ... $$"
        )
    unresolved = (
        "{{TITLE}}",
        "{{PROBLEM_ID}}",
        "{{SLUG}}",
        "{{CREATED_DATE}}",
        "<!-- LKM_ENTRIES -->",
    )
    if any(marker in text for marker in unresolved):
        errors.append("README.md contains unresolved template placeholders")
    return errors


def validate_problem_translation(path: Path) -> list[str]:
    """Validate an optional faithful Chinese translation of the canonical README."""

    if not path.is_file():
        return ["missing README.zh-CN.md"]
    text = path.read_text(encoding="utf-8")
    errors: list[str] = []
    if not text.startswith("# "):
        errors.append("README.zh-CN.md must start with a problem title")
    if "(README.md)" not in text:
        errors.append("README.zh-CN.md must link to the canonical README.md")
    positions = []
    for section in README_ZH_SECTIONS:
        marker = f"## {section}"
        position = text.find(marker)
        if position < 0:
            errors.append(f"README.zh-CN.md is missing section: {section}")
        positions.append(position)
    present = [position for position in positions if position >= 0]
    if present != sorted(present):
        errors.append("README.zh-CN.md sections are out of order")
    actual_sections = tuple(
        match.group(1).strip()
        for match in re.finditer(r"^## (.+)$", text, flags=re.MULTILINE)
    )
    if actual_sections != README_ZH_SECTIONS:
        errors.append(
            "README.zh-CN.md must contain exactly the four canonical top-level sections"
        )
    unresolved = (
        "{{TITLE}}",
        "{{PROBLEM_ID}}",
        "{{SLUG}}",
        "{{CREATED_DATE}}",
        "<!-- LKM_ENTRIES_ZH -->",
    )
    if any(marker in text for marker in unresolved):
        errors.append("README.zh-CN.md contains unresolved template placeholders")
    if re.search(r"(?<!\\)\\\(|(?<!\\)\\\)", text):
        errors.append(
            r"README.zh-CN.md uses \( ... \); GitHub inline math must use $ ... $"
        )
    if re.search(r"(?<!\\)\\\[|(?<!\\)\\\]", text):
        errors.append(
            r"README.zh-CN.md uses \[ ... \]; GitHub display math must use $$ ... $$"
        )
    return errors


def create_problem_repo(
    template_dir: Path,
    out_dir: Path,
    *,
    schema_path: Path | None = None,
    problem_id: str,
    title: str,
    slug: str,
    source_node: str | None = None,
    include_zh_translation: bool = False,
    git_init: bool = False,
) -> Path:
    normalized_slug = slugify(slug)
    if out_dir.exists():
        raise FileExistsError(f"output path already exists: {out_dir}")
    out_dir.mkdir(parents=True)
    shutil.copy2(template_dir / "README.md", out_dir / "README.md")
    if include_zh_translation:
        shutil.copy2(
            template_dir / "README.zh-CN.md",
            out_dir / "README.zh-CN.md",
        )

    replacements = {
        "{{PROBLEM_ID}}": problem_id,
        "{{TITLE}}": title,
        "{{SLUG}}": normalized_slug,
        "{{CREATED_DATE}}": today(),
    }
    for path in iter_text_files(out_dir):
        text = path.read_text(encoding="utf-8")
        for old, new in replacements.items():
            text = text.replace(old, new)
        path.write_text(text, encoding="utf-8")
    readme = out_dir / "README.md"
    text = readme.read_text(encoding="utf-8")
    lkm_entry = (
        f"- LKM open question `{source_node}` — explain its relationship to this problem."
        if source_node
        else "- Add the LKM open-question source and explain its relationship to this problem."
    )
    readme.write_text(
        text.replace("<!-- LKM_ENTRIES -->", lkm_entry),
        encoding="utf-8",
    )
    translation = out_dir / "README.zh-CN.md"
    if translation.is_file():
        translated_text = translation.read_text(encoding="utf-8")
        translated_lkm_entry = (
            f"- LKM open question `{source_node}` — 待补充它与本问题的关系。"
            if source_node
            else "- 待补充 LKM open-question 来源及其与本问题的关系。"
        )
        translation.write_text(
            translated_text.replace(
                "<!-- LKM_ENTRIES_ZH -->",
                translated_lkm_entry,
            ),
            encoding="utf-8",
        )

    if git_init:
        subprocess.run(["git", "init", "-b", "main"], cwd=out_dir, check=True)
    return out_dir
