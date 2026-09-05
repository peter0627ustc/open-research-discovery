"""Offline contract tests; no network or publication side effects."""
import dataclasses
import json
from pathlib import Path
import tempfile
import types
import unittest

import importlib.util
import sys

SCRIPT = Path(__file__).parents[1] / "scripts" / "author_fleet_publication.py"
SPEC = importlib.util.spec_from_file_location("author_fleet_publication", SCRIPT)
assert SPEC and SPEC.loader
publication = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = publication
SPEC.loader.exec_module(publication)
author_identity = publication.author_identity
install_author_adapter = publication.install_author_adapter


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.topic = {"id": "paper-123", "domain": "physics", "author_ids": ["PER-1"],
                      "seed_papers": [{"doi": "10.1000/test"}]}
        self.candidate = {"status": "accepted", "problem_id": "ORP-0001",
                          "problem_repo": str(self.root / "repo"), "topic_id": "paper-123"}
        fields = "candidate_id problem_id publication_id topic_id repo_dir internal_manifest title project_name project_path legacy_project_path".split()
        class GitLab:
            def ensure_project(self, **kwargs):
                return {"id": 123}
        self.base = types.SimpleNamespace(
            _yaml_load=self.read, _json_load_retry=self.read,
            _readme_status=lambda text: text.strip(),
            ReadyProblem=dataclasses.make_dataclass("ReadyProblem", fields),
            _save_ledger=lambda path, value: self.write(path, value),
            GitLab=GitLab, PublishError=ValueError,
        )
        self.write(self.root / "campaign.yaml", {"mode": "author_literature", "topics": [self.topic]})
        self.write(self.root / "candidates/CAN-123/discovery.json", {"source_keys": ["lkm:123"]})
        self.configure()

    def read(self, path):
        return json.loads(path.read_text())

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))

    def configure(self, status="ready", compile_status="completed", readme="ready"):
        self.write(self.root / "state.json", {"candidates": {"CAN-123": self.candidate},
                   "stages": {"candidate.CAN-123.compile": {"status": compile_status}}})
        self.write(self.root / "candidates/CAN-123/problem.yaml",
                   {"status": status, "problem_id": "ORP-0001", "title": "Example"})
        repo = self.root / "repo"
        repo.mkdir(exist_ok=True)
        (repo / "README.md").write_text(readme)

    def test_ready_and_provenance(self):
        install_author_adapter(self.base, self.root)
        item, = self.base.discover_ready(self.root)
        self.assertTrue(item.publication_id.startswith("PHYS-AUTH-"))
        self.assertEqual(item.project_path, item.legacy_project_path)
        ledger = {"published": {"ORP-0001": {"candidate_id": "CAN-123"}}}
        self.base._save_ledger(self.root / "ledger.json", ledger)
        self.assertEqual(ledger["published"]["ORP-0001"]["source_doi"], "10.1000/test")

    def test_uncertain_never_published(self):
        self.configure(status="uncertain")
        install_author_adapter(self.base, self.root)
        self.assertEqual(self.base.discover_ready(self.root), [])

    def test_both_readiness_signals_and_compile_required(self):
        install_author_adapter(self.base, self.root)
        for status, compile_status, readme in [("ready", "pending", "ready"), ("ready", "completed", "uncertain")]:
            self.configure(status, compile_status, readme)
            self.assertEqual(self.base.discover_ready(self.root), [])

    def test_identity_ignores_local_number_and_changes_for_doi(self):
        a = {"source_keys": ["lkm:123"], "problem_id": "ORP-1"}
        b = {**a, "problem_id": "ORP-2"}
        self.assertEqual(author_identity(a, self.topic)[0], author_identity(b, self.topic)[0])
        other = {**self.topic, "seed_papers": [{"doi": "10.1000/other"}]}
        self.assertNotEqual(author_identity(a, self.topic)[0], author_identity(a, other)[0])

    def test_refuse_existing_different_readme(self):
        install_author_adapter(self.base, self.root)
        item, = self.base.discover_ready(self.root)
        client = self.base.GitLab()
        client.project = lambda _: {"id": 4, "empty_repo": False}
        client.raw_readme = lambda _: "different"
        with self.assertRaisesRegex(ValueError, "refusing overwrite"):
            client.ensure_project(namespace="peter0627ustc", problem=item)

    def test_refuse_non_author(self):
        self.write(self.root / "campaign.yaml", {"mode": "topic"})
        with self.assertRaises(ValueError):
            install_author_adapter(self.base, self.root)


if __name__ == "__main__":
    unittest.main()
