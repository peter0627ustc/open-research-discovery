#!/usr/bin/env python3
"""Build nonoverlapping DOI/author-component shards; never starts remote work."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from open_research_discovery.author_campaign import build_author_config, normalize_doi
from open_research_discovery.common import dump_json, dump_yaml

HOSTS = ["8.147.68.214", "8.130.150.6", "8.130.157.189", "121.89.84.211",
         "8.130.121.60", "8.130.185.150", "8.130.40.180", "8.130.89.12"]
REMOTE_ROOT = "/root/open-research-discovery-author-fleet-20260905"


def prepare(summary_path: Path, accepted_path: Path, out: Path) -> dict:
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    accepted = json.loads(accepted_path.read_text(encoding="utf-8"))
    math = {row["person_id"] for row in summary["groups"]["mathematicians"]["people"]}
    physics = {row["person_id"] for row in summary["groups"]["theoretical_physicists"]["people"]}
    selected = math | physics
    people = {row["person_id"]: row for row in accepted["people"] if row["person_id"] in selected}
    works = {row["literature_id"]: row for row in accepted["literature"]}
    if set(people) != selected:
        raise ValueError(f"summary people missing from accepted source: {sorted(selected - set(people))}")
    parent = {author: author for author in selected}

    def find(author: str) -> str:
        if parent[author] != author:
            parent[author] = find(parent[author])
        return parent[author]

    doi_authors: dict[str, set[str]] = {}
    for author, person in people.items():
        for lid in person["accepted_literature_ids"]:
            try:
                doi = normalize_doi(str(works.get(lid, {}).get("doi") or ""))
            except ValueError:
                continue  # Kept in normalized source; importer reports input_issues.
            doi_authors.setdefault(doi, set()).add(author)
    for authors in doi_authors.values():
        first = min(authors)
        for author in authors:
            parent[find(author)] = find(first)
    components: dict[str, dict] = {}
    for author in sorted(selected):
        components.setdefault(find(author), {"authors": [], "dois": []})["authors"].append(author)
    for doi, authors in sorted(doi_authors.items()):
        components[find(min(authors))]["dois"].append(doi)
    shards = [{"host": host, "authors": [], "dois": [], "components": []} for host in HOSTS]
    for component in sorted(components.values(), key=lambda c: (-len(c["dois"]), c["authors"])):
        shard = min(shards, key=lambda s: (len(s["dois"]), len(s["authors"]), HOSTS.index(s["host"])))
        shard["authors"].extend(component["authors"])
        shard["dois"].extend(component["dois"])
        shard["components"].append(component["authors"])
    out.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": 1, "remote_root": REMOTE_ROOT,
        "source_files": [{"path": str(p), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
                         for p in (summary_path, accepted_path)],
        "selected_authors": len(selected), "unique_valid_dois": len(doi_authors),
        "author_components": len(components),
        "strategy": "Whole connected components of authors sharing normalized DOI; greedy DOI-load balance.",
        "concurrency_per_host": {"agents": 32, "active_primary_authors": 4, "agents_per_author": 8},
        "hosts": [],
    }
    assigned_dois: set[str] = set()
    assigned_authors: set[str] = set()
    for shard in shards:
        host = shard["host"]
        author_ids = set(shard["authors"])
        literature_ids = {lid for author in author_ids for lid in people[author]["accepted_literature_ids"]}
        # Order by source order, not set iteration; zero-paper authors retained.
        normalized = {
            "people": [{**person, "literature_ids": person["accepted_literature_ids"]}
                       for author, person in people.items() if author in author_ids],
            "literature": [work for lid, work in works.items() if lid in literature_ids],
        }
        folder = out / host
        folder.mkdir(exist_ok=True)
        source = folder / "literature.json"
        dump_json(source, normalized)
        config = build_author_config(source, Path(REMOTE_ROOT), workers=32, paper_workers=32, candidate_workers=1)
        for task in config["topics"]:
            task["domain"] = "physics" if set(task["author_ids"]) & physics else "mathematics"
        config["name"] = "author-literature-fleet-" + host.replace(".", "-")
        config["agents"].update({
            "author_workers": 4, "agent_workers_per_author": 8, "timeout_seconds": None,
            "claude_executable": "claude --permission-mode acceptEdits --allowedTools Bash,Read,Write,Edit,Glob,Grep,WebSearch,WebFetch,Skill --disallowedTools Agent,Task",
        })
        dump_yaml(folder / "campaign.yaml", config)
        from jsonschema import Draft202012Validator
        schema = json.loads((Path(__file__).resolve().parents[1] / "schemas/campaign.schema.json").read_text())
        Draft202012Validator(schema).validate(config)
        actual_dois = {normalize_doi(task["seed_papers"][0]["doi"]) for task in config["topics"]}
        if assigned_dois & actual_dois or assigned_authors & author_ids:
            raise ValueError("cross-host DOI or author overlap")
        if actual_dois != set(shard["dois"]):
            raise ValueError("shard DOI coverage mismatch")
        assigned_dois.update(actual_dois)
        assigned_authors.update(author_ids)
        manifest["hosts"].append({
            "host": host, "authors": len(author_ids), "papers": len(actual_dois),
            "input_issues": config["input_issues"], "components": shard["components"],
            "people": [{"id": a, "name": people[a]["name"]["canonical"],
                        "accepted_attributions": len(people[a]["accepted_literature_ids"])}
                       for a in sorted(author_ids)],
            "zero_accepted_work_authors": [a for a in sorted(author_ids) if not people[a]["accepted_literature_ids"]],
            "source": str(source), "config": str(folder / "campaign.yaml"),
            "config_sha256": hashlib.sha256((folder / "campaign.yaml").read_bytes()).hexdigest(),
        })
    if assigned_authors != selected or assigned_dois != set(doi_authors):
        raise ValueError("incomplete fleet coverage")
    dump_json(out / "deployment-manifest.json", manifest)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--accepted", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    manifest = prepare(args.summary, args.accepted, args.out_dir)
    print(json.dumps({"selected_authors": manifest["selected_authors"],
                      "unique_valid_dois": manifest["unique_valid_dois"],
                      "hosts": [{k: host[k] for k in ("host", "authors", "papers")}
                                for host in manifest["hosts"]]}, indent=2))


if __name__ == "__main__":
    main()
