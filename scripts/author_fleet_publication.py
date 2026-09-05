#!/usr/bin/env python3
"""Publish only compiled, audited ready author problems using the existing publisher.

Run from a dedicated systemd timer. Never compiles, resumes, or edits campaign state.
The production publisher supplies authentication, README/math validation, and push.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys


def load_publisher(path: Path):
    spec = importlib.util.spec_from_file_location("author_fleet_base_publisher", path)
    if spec is None or spec.loader is None:
        raise ValueError("cannot load publisher")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def author_identity(candidate: dict, topic: dict) -> tuple[str, dict]:
    domain = str(topic.get("domain", "")).lower()
    discipline = {"mathematics": "MATH", "math": "MATH", "physics": "PHYS"}.get(domain)
    if discipline is None:
        raise ValueError(f"author paper has unsupported domain: {domain!r}")
    seeds = topic.get("seed_papers") or []
    if len(seeds) != 1 or not seeds[0].get("doi") or not topic.get("author_ids"):
        raise ValueError("author publication requires source DOI and author IDs")
    source_keys = sorted(candidate.get("source_keys") or [])
    if not source_keys:
        raise ValueError("author publication requires source keys")
    doi = str(seeds[0]["doi"]).strip().lower()
    identity = json.dumps([doi, source_keys], ensure_ascii=False, separators=(",", ":"))
    digest = hashlib.sha256(identity.encode()).hexdigest()[:20].upper()
    return f"{discipline}-AUTH-{digest}", {
        "mode": "author_literature", "discipline": discipline,
        "source_doi": doi, "source_keys": source_keys,
        "author_ids": topic["author_ids"],
        "literature_ids": topic.get("literature_ids", []),
        "source_topic_id": topic["id"],
    }


def install_author_adapter(base, run_dir: Path) -> None:
    config = base._yaml_load(run_dir / "campaign.yaml")
    if config.get("mode") != "author_literature":
        raise ValueError("refusing non-author campaign")
    topics = {topic["id"]: topic for topic in config["topics"]}
    provenance: dict[str, dict] = {}

    def discover_ready(directory: Path):
        state = base._json_load_retry(directory / "state.json")
        ready = []
        for candidate_id, candidate in (state.get("candidates") or {}).items():
            if candidate.get("status") != "accepted":
                continue
            stage = (state.get("stages") or {}).get(f"candidate.{candidate_id}.compile", {})
            if stage.get("status") != "completed":
                continue
            if not candidate.get("problem_repo") or not candidate.get("problem_id"):
                continue
            repo = Path(candidate["problem_repo"]).resolve()
            manifest_path = directory / "candidates" / candidate_id / "problem.yaml"
            if not manifest_path.is_file() or not (repo / "README.md").is_file():
                continue
            manifest = base._yaml_load(manifest_path)
            readme = (repo / "README.md").read_text(encoding="utf-8")
            if manifest.get("status") != "ready" or base._readme_status(readme) != "ready":
                continue
            if manifest.get("problem_id") != candidate["problem_id"]:
                raise ValueError("candidate and manifest problem IDs disagree")
            topic = topics[candidate["topic_id"]]
            discovery = base._json_load_retry(directory / "candidates" / candidate_id / "discovery.json")
            publication_id, source = author_identity(discovery, topic)
            provenance[candidate_id] = source
            # No legacy ORP fallback: local ORP numbers repeat across eight hosts.
            project_path = publication_id.lower()
            ready.append(base.ReadyProblem(
                candidate_id=candidate_id, problem_id=candidate["problem_id"],
                publication_id=publication_id, topic_id=candidate["topic_id"],
                repo_dir=repo, internal_manifest=manifest_path,
                title=str(manifest.get("title") or publication_id),
                project_name=publication_id, project_path=project_path,
                legacy_project_path=project_path,
            ))
        return sorted(ready, key=lambda item: item.problem_id)

    base.discover_ready = discover_ready
    save_ledger = base._save_ledger

    def save_with_provenance(path, ledger):
        for entry in ledger.get("published", {}).values():
            entry.update(provenance.get(entry.get("candidate_id"), {}))
        save_ledger(path, ledger)

    base._save_ledger = save_with_provenance
    ensure_project = base.GitLab.ensure_project

    def ensure_without_overwrite(self, **kwargs):
        problem = kwargs["problem"]
        full_path = f"{kwargs['namespace']}/{problem.project_path}"
        existing = self.project(full_path)
        if existing and not existing.get("empty_repo", False):
            remote = self.raw_readme(int(existing["id"]))
            local = (problem.repo_dir / "README.md").read_text(encoding="utf-8")
            if remote != local:
                raise base.PublishError("existing author publication differs; refusing overwrite")
        return ensure_project(self, **kwargs)

    base.GitLab.ensure_project = ensure_without_overwrite


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--publisher", type=Path, required=True)
    parser.add_argument("--runs-root", type=Path, required=True)
    parser.add_argument("--max-items", type=int, default=80, help="maximum per campaign per scan")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.max_items < 1 or args.workers < 1:
        parser.error("max-items and workers must be positive")
    failures = 0
    for run in sorted(args.runs_root.glob("*")):
        if not (run / "campaign.yaml").is_file() or not (run / "state.json").is_file():
            continue
        base = load_publisher(args.publisher)
        try:
            config = base._yaml_load(run / "campaign.yaml")
            if config.get("mode") != "author_literature":
                continue
            install_author_adapter(base, run)
            options = base.build_parser().parse_args([
                "--run-dir", str(run), "--namespace",
                os.environ.get("GEWU_GITLAB_NAMESPACE", "peter0627ustc"),
                "--max-items", str(args.max_items), "--workers", str(args.workers),
                *(["--dry-run"] if args.dry_run else []),
            ])
            failures += int(base.publish(options) != 0)
        except BlockingIOError:
            print(json.dumps({"event": "author_publish_locked", "run": run.name}))
        except Exception as error:
            # Do not expose arbitrary HTTP response bodies or credential-bearing exceptions.
            print(json.dumps({"event": "author_publish_failed", "run": run.name,
                              "error_type": type(error).__name__}))
            failures += 1
    return int(failures > 0)


if __name__ == "__main__":
    raise SystemExit(main())
