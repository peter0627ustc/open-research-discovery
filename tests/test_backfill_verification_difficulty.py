from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
import copy
import importlib.util
import json
from pathlib import Path
import sys

import pytest
import yaml


SCRIPT = Path(__file__).parents[1] / "scripts" / "backfill_verification_difficulty.py"
SPEC = importlib.util.spec_from_file_location("backfill_verification_difficulty", SCRIPT)
assert SPEC and SPEC.loader
backfill = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = backfill
SPEC.loader.exec_module(backfill)


def make_entry(tmp_path: Path, difficulty: object = None, *, omitted: bool = False) -> dict:
    run = tmp_path / "run"
    candidate = run / "candidates" / "CAN-1"
    candidate.mkdir(parents=True)
    record = {"problem_id": "ORP-1", "status": "ready"}
    if not omitted:
        record["verification_difficulty"] = difficulty
    (candidate / "problem.yaml").write_text(yaml.safe_dump(record), encoding="utf-8")
    publication = {"candidate_id": "CAN-1", "project_id": 101,
                   "project_path": "owner/phys-orp-1", "publication_id": "PHYS-ORP-1"}
    state = {"candidates": {"CAN-1": {"problem_id": "ORP-1", "status": "accepted"}},
             "stages": {"candidate.CAN-1.compile": {"status": "completed"}}}
    (run / "gewulab-publication").mkdir()
    (run / "gewulab-publication" / "ledger.json").write_text(
        json.dumps({"published": {"ORP-1": publication}, "blocked": {"ORP-2": {}}}),
        encoding="utf-8",
    )
    (run / "state.json").write_text(json.dumps(state), encoding="utf-8")
    return {"run_dir": str(run), "problem_id": "ORP-1", "publication": publication, "state": state}


ASSESSMENT = {"score": 0, "rationale": "Completed answers admit sound mechanical checks."}


class FakeGitLab:
    def __init__(self, metadata: object = None, *, missing: bool = False) -> None:
        self.project = {"id": 101, "path_with_namespace": "owner/phys-orp-1", "default_branch": "master"}
        self.metadata = copy.deepcopy(metadata if metadata is not None else {"custom": {"x": [1, 2]}})
        self.raw = (json.dumps(self.metadata) + "\n").encode("utf-8")
        self.commit = "original-commit"
        self.readme = b"# Original README\n"
        self.missing = missing
        self.calls: list[tuple[str, str, dict | None]] = []
        self.failures: list[int | None] = []
        self.concurrent_field: dict | None = None
        self.succeed_then_timeout = False

    def get(self, path: str) -> dict:
        return self.request("GET", path)

    def request(self, method: str, path: str, payload: dict | None = None) -> dict:
        self.calls.append((method, path, copy.deepcopy(payload)))
        if path == "/projects/101":
            assert method == "GET"
            return copy.deepcopy(self.project)
        if "README.md" in path:
            assert method == "GET", "README writes are forbidden"
            return {"encoding": "base64", "content": base64.b64encode(self.readme).decode(),
                    "last_commit_id": "readme-commit"}
        assert "metadata%2Fproblem.json" in path
        if self.missing:
            raise backfill.APIError(404, method)
        if method == "GET":
            assert "ref=master" in path
            return {"encoding": "base64", "content": base64.b64encode(self.raw).decode(),
                    "last_commit_id": self.commit}
        assert method == "PUT"
        assert payload is not None and payload["last_commit_id"] == self.commit
        assert payload["branch"] == "master"
        if self.failures:
            status = self.failures.pop(0)
            if self.concurrent_field:
                self.metadata.update(self.concurrent_field)
                self.raw = (json.dumps(self.metadata) + "\n").encode()
                self.commit = "concurrent-commit"
                self.concurrent_field = None
            raise backfill.APIError(status, method)
        self.metadata = json.loads(payload["content"])
        self.raw = payload["content"].encode("utf-8")
        self.commit = "backfill-commit"
        if self.succeed_then_timeout:
            self.succeed_then_timeout = False
            raise backfill.APIError(None, method)
        return {"file_path": "metadata/problem.json", "branch": "master"}


@pytest.fixture(autouse=True)
def no_retry_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(backfill, "retry_pause", lambda _: None)


def test_enumerates_only_dynamic_published_mapping(tmp_path: Path) -> None:
    entry = make_entry(tmp_path, ASSESSMENT)
    entries = backfill.collect_entries([Path(entry["run_dir"])])
    assert [item["problem_id"] for item in entries] == ["ORP-1"]
    assert entries[0]["publication"]["project_id"] == 101


def test_preserves_arbitrary_metadata_and_only_writes_metadata(tmp_path: Path) -> None:
    entry = make_entry(tmp_path, ASSESSMENT)
    original = {"solution_difficulty": ["obstacle"], "scientific_significance": {"level": "high"},
                "extension": {"nested": [1, {"unicode": "中文"}]}, "unknown": None}
    api = FakeGitLab(original)
    result = backfill.backfill_one(entry, api, apply=True, verify_readme=True)
    assert result["status"] == "updated"
    assert api.metadata == {**original, "verification_difficulty": ASSESSMENT}
    puts = [call for call in api.calls if call[0] == "PUT"]
    assert len(puts) == 1 and puts[0][1].endswith("/metadata%2Fproblem.json")
    assert puts[0][2]["last_commit_id"] == "original-commit"
    assert result["readme_sha256_before"] == result["readme_sha256_after"]
    assert result["metadata_sha256_expected"] == result["metadata_sha256_after"]


def test_default_dry_run_never_writes(tmp_path: Path) -> None:
    api = FakeGitLab()
    result = backfill.backfill_one(make_entry(tmp_path, ASSESSMENT), api)
    assert result["status"] == "would_update"
    assert all(method == "GET" for method, _, _ in api.calls)
    assert backfill.build_parser().parse_args(["--run-dir", str(tmp_path)]).apply is False


@pytest.mark.parametrize("omitted", [False, True])
def test_missing_or_null_score_is_skipped_without_api_calls(tmp_path: Path, omitted: bool) -> None:
    api = FakeGitLab()
    result = backfill.backfill_one(make_entry(tmp_path, omitted=omitted), api, apply=True)
    assert result["status"] == "skipped" and result["reason"] == "unassessed"
    assert api.calls == []


@pytest.mark.parametrize("difficulty", [
    {"score": True, "rationale": "text"}, {"score": -1, "rationale": "text"},
    {"score": 11, "rationale": "text"}, {"score": 2.5, "rationale": "text"},
    {"score": 0, "rationale": " "}, {"score": 0},
    {"score": 0, "rationale": "text", "extra": 1},
])
def test_invalid_existing_assessment_fails_without_fabrication(tmp_path: Path, difficulty: object) -> None:
    api = FakeGitLab()
    result = backfill.backfill_one(make_entry(tmp_path, difficulty), api, apply=True)
    assert result["status"] == "failed" and "Invalid compiled" in result["error"]
    assert api.calls == []


def test_idempotent_rerun_has_no_second_put(tmp_path: Path) -> None:
    entry, api = make_entry(tmp_path, ASSESSMENT), FakeGitLab()
    assert backfill.backfill_one(entry, api, apply=True)["status"] == "updated"
    assert backfill.backfill_one(entry, api, apply=True)["status"] == "unchanged"
    assert len([call for call in api.calls if call[0] == "PUT"]) == 1


def test_missing_remote_metadata_is_skipped_without_creation(tmp_path: Path) -> None:
    api = FakeGitLab(missing=True)
    result = backfill.backfill_one(make_entry(tmp_path, ASSESSMENT), api, apply=True)
    assert result["status"] == "skipped" and result["reason"] == "missing_remote_metadata"
    assert all(method == "GET" for method, _, _ in api.calls)


def test_identity_mismatch_prevents_mutation(tmp_path: Path) -> None:
    api = FakeGitLab()
    api.project["path_with_namespace"] = "other/unrelated"
    result = backfill.backfill_one(make_entry(tmp_path, ASSESSMENT), api, apply=True)
    assert result["status"] == "failed" and "ID/path" in result["error"]
    assert len(api.calls) == 1


def test_conflict_reloads_and_preserves_concurrent_keys(tmp_path: Path) -> None:
    api = FakeGitLab({"original": 1})
    api.failures = [400]
    api.concurrent_field = {"concurrent": {"must": "survive"}}
    result = backfill.backfill_one(make_entry(tmp_path, ASSESSMENT), api, apply=True)
    assert result["status"] == "updated" and result["attempts"] == 2
    assert api.metadata == {"original": 1, "concurrent": {"must": "survive"},
                            "verification_difficulty": ASSESSMENT}
    puts = [call for call in api.calls if call[0] == "PUT"]
    assert [call[2]["last_commit_id"] for call in puts] == ["original-commit", "concurrent-commit"]


@pytest.mark.parametrize("status", [429, 503, None])
def test_transient_put_failure_is_bounded_and_reloads(tmp_path: Path, status: int | None) -> None:
    api = FakeGitLab()
    api.failures = [status] * 3
    result = backfill.backfill_one(make_entry(tmp_path, ASSESSMENT), api, apply=True)
    assert result["status"] == "failed" and "bounded retries" in result["error"]
    assert len([call for call in api.calls if call[0] == "PUT"]) == 3


def test_uncertain_put_that_succeeded_is_not_repeated(tmp_path: Path) -> None:
    api = FakeGitLab()
    api.succeed_then_timeout = True
    result = backfill.backfill_one(make_entry(tmp_path, ASSESSMENT), api, apply=True)
    assert result["status"] == "updated"
    assert api.metadata["verification_difficulty"] == ASSESSMENT
    assert len([call for call in api.calls if call[0] == "PUT"]) == 1


def test_boolean_remote_score_is_replaced_with_assessed_integer(tmp_path: Path) -> None:
    api = FakeGitLab({"verification_difficulty": {**ASSESSMENT, "score": False}})
    result = backfill.backfill_one(make_entry(tmp_path, ASSESSMENT), api, apply=True)
    assert result["status"] == "updated"
    assert type(api.metadata["verification_difficulty"]["score"]) is int


def test_possible_secrets_never_reach_api_or_receipts(tmp_path: Path) -> None:
    difficulty = {"score": 1, "rationale": "glpat-notarealcredential123456789"}
    api = FakeGitLab()
    result = backfill.backfill_one(make_entry(tmp_path, difficulty), api, apply=True)
    assert result["status"] == "failed" and "secret material" in result["error"]
    assert "glpat-" not in json.dumps(result)
    assert api.calls == []


def test_auth_failure_does_not_retry(tmp_path: Path) -> None:
    api = FakeGitLab()
    api.failures = [403]
    result = backfill.backfill_one(make_entry(tmp_path, ASSESSMENT), api, apply=True)
    assert result["status"] == "failed" and "HTTP 403" in result["error"]
    assert len([call for call in api.calls if call[0] == "PUT"]) == 1


def test_receipts_survive_threaded_append_and_partial_previous_line(tmp_path: Path) -> None:
    path = tmp_path / "receipts.jsonl"
    path.write_text('{"interrupted":', encoding="utf-8")
    receipts = backfill.Receipts(path)
    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(lambda i: receipts.append({"status": "unchanged", "problem_id": str(i)}), range(20)))
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == '{"interrupted":'
    records = [json.loads(line) for line in lines[1:]]
    assert len(records) == 20 and {row["problem_id"] for row in records} == {str(i) for i in range(20)}


def test_source_must_match_compiled_published_candidate(tmp_path: Path) -> None:
    entry = make_entry(tmp_path, ASSESSMENT)
    entry["state"]["stages"]["candidate.CAN-1.compile"]["status"] = "pending"
    api = FakeGitLab()
    result = backfill.backfill_one(entry, api, apply=True)
    assert result["status"] == "failed" and "completed compile" in result["error"]
    assert api.calls == []
