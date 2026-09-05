from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from open_research_discovery.agent import AgentRun
from open_research_discovery.author_campaign import (
    build_author_config, normalize_doi, records_from_graph, validate_author_config,
)
from open_research_discovery.campaign import CampaignError, CampaignPipeline
from open_research_discovery.common import dump_json, dump_yaml
from open_research_discovery.lkm import LKMResponseError
from test_topic_campaign import TopicAgentRunner, _start_pipeline, _unresolvable_fetch

ROOT = Path(__file__).resolve().parents[1]


def source_file(tmp_path: Path) -> Path:
    path = tmp_path / "literature.json"
    dump_json(path, {
        "people": [
            {"person_id": "P1", "name": {"canonical": "Author One"}, "literature_ids": ["L1", "L2"], "awards": [{"type": "nobel_physics"}]},
            {"person_id": "P2", "name": {"canonical": "Author Two"}, "literature_ids": ["L1"], "awards": [{"type": "nobel_physics"}]},
        ],
        "literature": [
            {"literature_id": "L1", "doi": "https://doi.org/10.1234/ONE", "title": "First paper"},
            {"literature_id": "L2", "doi": "10.1234/two", "title": "Second paper"},
        ],
    })
    return path


def config_file(tmp_path: Path) -> tuple[Path, dict]:
    config = build_author_config(source_file(tmp_path), tmp_path, workers=2, paper_workers=2)
    path = tmp_path / "campaign.yaml"
    dump_yaml(path, config)
    return path, config


def graph(doi: str, questions: list | None = None) -> dict:
    return {"code": 0, "trace_id": "offline-trace", "data": {"papers": [{
        "paper": {"id": "paper-id", "doi": doi, "title": "Source paper"},
        "open_questions": questions if questions is not None else [
            {"id": "q1", "global_id": "g1", "content": "Determine whether the finite lattice admits the stated witness."},
        ],
    }]}}


class Collector:
    def __init__(self, payloads: dict | None = None):
        self.payloads = payloads or {}
        self.calls = []

    def __call__(self, **kwargs):
        doi = kwargs["doi"]
        self.calls.append(doi)
        payload = self.payloads.get(doi, graph(doi))
        if isinstance(payload, Exception):
            raise payload
        dump_json(kwargs["raw_out"], payload)
        # The production collector also rejects nonzero codes. The DOI-bound
        # adapter must validate independently of a permissive collector.
        return {}


class Runner(TopicAgentRunner):
    def run(self, **kwargs):
        if kwargs["role"] != "author-dedup":
            return super().run(**kwargs)
        self.calls.append("author-dedup")
        data = json.loads(kwargs["prompt"].split("\n", 1)[1])
        self.dedup_inputs = getattr(self, "dedup_inputs", [])
        self.dedup_inputs.append(data)
        output = {"duplicate_of": next(iter(data["existing_titles"])), "reason": "Titles clearly denote the same problem."}
        dump_json(kwargs["output_path"], output)
        dump_json(kwargs["events_path"], {"role": "author-dedup"})
        return AgentRun(output=output, metadata={"exit_code": 0})


def test_import_doi_dedup_and_schema(tmp_path):
    _, config = config_file(tmp_path)
    assert len(config["topics"]) == 2
    assert config["topics"][0]["author_ids"] == ["P1", "P2"]
    assert config["topics"][0]["seed_papers"][0]["doi"] == "10.1234/one"
    assert "questions_per_domain" not in config["limits"]
    schema = json.loads((ROOT / "schemas/campaign.schema.json").read_text())
    Draft202012Validator(schema).validate(config)
    config["agents"]["timeout_seconds"] = None
    Draft202012Validator(schema).validate(config)
    config["limits"]["questions_per_domain"] = 1
    assert list(Draft202012Validator(schema).iter_errors(config))


def test_author_selection_and_bad_input(tmp_path):
    source = source_file(tmp_path)
    config = build_author_config(source, tmp_path, author_ids=["P2"])
    assert len(config["topics"]) == 1
    assert config["topics"][0]["author_ids"] == ["P2"]
    with pytest.raises(ValueError, match="unknown"):
        build_author_config(source, tmp_path, author_ids=["missing"])
    config["topics"][0]["seed_papers"][0]["doi"] = ""
    with pytest.raises(ValueError, match="DOI"):
        validate_author_config(config)


def test_input_issues_are_reported_not_silently_discarded(tmp_path):
    source = source_file(tmp_path)
    payload = json.loads(source.read_text())
    payload["literature"][0]["doi"] = "10.1234/not a normalized DOI"
    dump_json(source, payload)
    config = build_author_config(source, tmp_path)
    assert len(config["topics"]) == 1
    assert config["input_issues"][0]["author_ids"] == ["P1", "P2"]
    path = tmp_path / "campaign.yaml"
    dump_yaml(path, config)
    pipeline = _start_pipeline(path, repository_root=ROOT, agent_runner=Runner(), paper_collector=Collector())
    assert len(pipeline.run()["input_failed"]) == 1
    assert pipeline.state["status"] == "partial_failed"


def test_graph_binds_only_requested_doi_and_has_no_cap(tmp_path):
    _, config = config_file(tmp_path)
    task = config["topics"][0]
    questions = [{"global_id": str(i), "content": f"Question {i}"} for i in range(35)]
    payload = graph("10.1234/one", questions)
    payload["data"]["papers"].extend(graph("10.1234/unrelated")["data"]["papers"])
    records = records_from_graph(payload, task, "papers/graph.json")
    assert len(records) == 35
    assert {r["source_doi"] for r in records} == {"10.1234/one"}
    assert {r["domain_id"] for r in records} == {"physics"}
    assert all(not r["author_attribution_verified"] for r in records)
    assert normalize_doi("DOI: 10.1234/ONE") == "10.1234/one"


@pytest.mark.parametrize("kind", ["7002", "missing", "null", "mismatch", "empty_graph", "empty_content"])
def test_graph_failures_are_not_empty_questions(tmp_path, kind):
    _, config = config_file(tmp_path)
    payload = graph("10.1234/one")
    paper = payload["data"]["papers"][0]
    if kind == "7002":
        payload.update(code=7002, msg="wallet insufficient balance")
    elif kind == "missing":
        del paper["open_questions"]
    elif kind == "null":
        paper["open_questions"] = None
    elif kind == "mismatch":
        paper["paper"]["doi"] = "10.1234/other"
    elif kind == "empty_graph":
        payload["data"]["papers"] = []
    else:
        paper["open_questions"][0]["content"] = ""
    with pytest.raises(LKMResponseError):
        records_from_graph(payload, config["topics"][0], "graph.json")


def test_empty_success_is_no_questions(tmp_path):
    path, _ = config_file(tmp_path)
    collector = Collector({doi: graph(doi, []) for doi in ["10.1234/one", "10.1234/two"]})
    runner = Runner()
    pipeline = _start_pipeline(path, repository_root=ROOT, agent_runner=runner, paper_collector=collector)
    result = pipeline.run()
    assert result["paper_status_counts"] == {"no_questions": 2}
    assert pipeline.state["status"] == "completed"
    assert runner.calls == []


def test_end_to_end_same_author_dedup_resume_and_increment(tmp_path):
    path, _ = config_file(tmp_path)
    collector = Collector()
    runner = Runner()
    pipeline = _start_pipeline(path, repository_root=ROOT, agent_runner=runner, paper_collector=collector)
    result = pipeline.run()
    assert pipeline.state["status"] == "completed", result
    assert len(result["canonical_problem_ids"]) == 1
    assert len(result["accepted_problem_ids"]) == 1
    assert sorted(collector.calls) == ["10.1234/one", "10.1234/two"]
    assert runner.calls.count("research") == 2
    assert "discovery" not in runner.calls
    states = Counter(s["status"] for s in pipeline.state["candidates"].values())
    assert states == {"accepted": 1, "duplicate_author": 1}
    index = json.loads(Path(result["author_index"]).read_text())
    entry = next(iter(index["problems"].values()))
    assert len(entry["members"]) == 2
    assert entry["problem"]["problem_id"] == result["canonical_problem_ids"][0]
    assert entry["author_ids"] == ["P1", "P2"]
    assert (Path(entry["compiled"]["solution_repo"]) / "README.md").is_file()
    with pytest.raises(CampaignError, match="dedup"):
        pipeline.compile_pending()
    count = len(runner.calls)
    resumed = CampaignPipeline.resume(pipeline.run_dir, repository_root=ROOT, agent_runner=runner,
                                      paper_collector=collector, citation_fetcher=_unresolvable_fetch)
    assert resumed.run()["canonical_problem_ids"] == result["canonical_problem_ids"]
    assert len(runner.calls) == count
    next_run = _start_pipeline(path, repository_root=ROOT, run_id="increment", agent_runner=runner, paper_collector=collector)
    assert next_run.run()["canonical_problem_ids"] == result["canonical_problem_ids"]
    assert len(runner.calls) == count
    assert len(collector.calls) == 2


def test_semantic_dedup_records_reason(tmp_path):
    path, _ = config_file(tmp_path)
    payload = graph("10.1234/two", [{"global_id": "different-id", "content": "Another phrasing of the same finite-lattice witness."}])
    runner = Runner()
    pipeline = _start_pipeline(path, repository_root=ROOT, agent_runner=runner,
                               paper_collector=Collector({"10.1234/two": payload}))
    result = pipeline.run()
    assert pipeline.state["status"] == "completed", result
    assert "author-dedup" in runner.calls
    assert set(runner.dedup_inputs[0]) == {"incoming_title", "existing_titles"}
    assert all(isinstance(title, str) for title in runner.dedup_inputs[0]["existing_titles"].values())
    assert len(result["canonical_problem_ids"]) == 1
    assert list(pipeline.run_dir.glob("candidates/*/dedup-*.json"))


def test_balance_failure_retries_without_replaying_completed_paper(tmp_path):
    path, _ = config_file(tmp_path)
    collector = Collector({"10.1234/one": {"code": 7002, "msg": "insufficient balance"}})
    runner = Runner()
    pipeline = _start_pipeline(path, repository_root=ROOT, agent_runner=runner, paper_collector=collector)
    result = pipeline.run()
    assert result["paper_status_counts"] == {"completed": 1, "discovery_failed": 1}
    assert pipeline.state["status"] == "partial_failed"
    assert list(pipeline.run_dir.glob("papers/*/graph-0001.json"))
    collector.payloads.clear()
    resumed = CampaignPipeline.resume(pipeline.run_dir, repository_root=ROOT, agent_runner=runner,
                                      paper_collector=collector, citation_fetcher=_unresolvable_fetch)
    result = resumed.run()
    assert result["paper_status_counts"] == {"completed": 2}
    assert collector.calls.count("10.1234/two") == 1
    assert collector.calls.count("10.1234/one") == 2
    assert runner.calls.count("research") == 2
    assert list(pipeline.run_dir.glob("papers/*/graph-0002.json"))


def test_compile_failure_resumes_without_research_or_graph_replay(tmp_path, monkeypatch):
    path, config = config_file(tmp_path)
    config["topics"] = config["topics"][:1]
    dump_yaml(path, config)
    runner, collector = Runner(), Collector()
    pipeline = _start_pipeline(path, repository_root=ROOT, agent_runner=runner, paper_collector=collector)
    def unavailable(*args):
        raise OSError("temporary compile failure")
    monkeypatch.setattr(pipeline, "_compile", unavailable)
    assert pipeline.run()["paper_status_counts"] == {"candidate_failed": 1}
    calls = list(runner.calls)
    resumed = CampaignPipeline.resume(pipeline.run_dir, repository_root=ROOT, agent_runner=runner,
                                      paper_collector=collector, citation_fetcher=_unresolvable_fetch)
    assert len(resumed.run()["accepted_problem_ids"]) == 1
    assert runner.calls == calls
    assert len(collector.calls) == 1
    accepted = next(iter(resumed.state["candidates"].values()))
    assert accepted["status"] == "accepted"
    assert accepted["error"] == ""


def test_unknown_semantic_merge_is_quarantined(tmp_path):
    path, _ = config_file(tmp_path)
    class InvalidRunner(Runner):
        def run(self, **kwargs):
            if kwargs["role"] != "author-dedup":
                return super().run(**kwargs)
            output = {"duplicate_of": "ORP-NOT-IN-INDEX", "reason": "invalid target"}
            dump_json(kwargs["output_path"], output)
            return AgentRun(output=output, metadata={"exit_code": 0})
    collector = Collector({"10.1234/two": graph("10.1234/two", [{"global_id": "g2", "content": "different text"}])})
    pipeline = _start_pipeline(path, repository_root=ROOT, agent_runner=InvalidRunner(), paper_collector=collector)
    result = pipeline.run()
    assert result["paper_status_counts"] == {"completed": 1, "candidate_failed": 1}
    assert len(result["accepted_problem_ids"]) == 1


def test_related_but_distinct_problems_are_kept(tmp_path):
    path, _ = config_file(tmp_path)
    class DistinctRunner(Runner):
        def run(self, **kwargs):
            if kwargs["role"] != "author-dedup":
                return super().run(**kwargs)
            output = {"duplicate_of": None, "reason": "Different objective and regime; not equivalent."}
            dump_json(kwargs["output_path"], output)
            return AgentRun(output=output, metadata={"exit_code": 0})
    collector = Collector({"10.1234/two": graph("10.1234/two", [{"global_id": "g2", "content": "different target"}])})
    pipeline = _start_pipeline(path, repository_root=ROOT, agent_runner=DistinctRunner(), paper_collector=collector)
    result = pipeline.run()
    assert result["paper_status_counts"] == {"completed": 2}
    assert len(result["accepted_problem_ids"]) == 2


def test_transport_failure_history_survives_retry(tmp_path):
    path, config = config_file(tmp_path)
    config["topics"] = config["topics"][:1]
    dump_yaml(path, config)
    collector = Collector({"10.1234/one": TimeoutError("offline timeout")})
    runner = Runner()
    pipeline = _start_pipeline(path, repository_root=ROOT, agent_runner=runner, paper_collector=collector)
    assert pipeline.run()["paper_status_counts"] == {"discovery_failed": 1}
    collector.payloads.clear()
    resumed = CampaignPipeline.resume(pipeline.run_dir, repository_root=ROOT, agent_runner=runner,
                                      paper_collector=collector, citation_fetcher=_unresolvable_fetch)
    assert resumed.run()["paper_status_counts"] == {"completed": 1}
    history = sorted(pipeline.run_dir.glob("papers/*/attempt-*.json"))
    assert [json.loads(p.read_text())["status"] for p in history] == ["failed", "completed"]


def test_cli_initialization_preserves_original_topic_command(tmp_path, capsys):
    from open_research_discovery.cli import main
    source = source_file(tmp_path)
    config_path = tmp_path / "authors.yaml"
    assert main(["campaign", "init-authors", "--literature-list", str(source),
                 "--author-id", "P2", "--out", str(config_path)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["papers"] == 1
    assert result["candidate_limit"] is None
    assert result["input_issue_count"] == 0
    topic_path = tmp_path / "topics.yaml"
    assert main(["campaign", "init", "--topic", "quantum systems", "--out", str(topic_path)]) == 0
    from open_research_discovery.common import load_yaml
    assert load_yaml(topic_path)["limits"]["questions_per_domain"] == 20


def test_author_limits_validate_as_a_pair(tmp_path):
    _, config = config_file(tmp_path)
    config["agents"]["author_workers"] = 4
    with pytest.raises(ValueError, match="together"):
        validate_author_config(config)
    config["agents"]["agent_workers_per_author"] = 8
    validate_author_config(config)
    schema = json.loads((ROOT / "schemas/campaign.schema.json").read_text())
    Draft202012Validator(schema).validate(config)
    config["agents"]["candidate_workers_per_topic"] = 9
    with pytest.raises(ValueError, match="per-author"):
        validate_author_config(config)


@pytest.mark.parametrize("candidate_workers", [1, 2])
def test_four_author_scheduler_bounds_and_preserves_shared_papers(tmp_path, candidate_workers):
    import threading
    import time
    from types import SimpleNamespace
    from open_research_discovery.author_campaign import AuthorCampaign

    tasks = [{"id": f"P{a}-{p}", "author_ids": [f"P{a}"]}
             for a in range(6) for p in range(12)]
    tasks[0]["author_ids"].append("P5")  # One task, all attribution retained.
    pipeline = SimpleNamespace(
        problem_root=tmp_path,
        topic_workers=32 // candidate_workers,
        candidate_workers_per_topic=candidate_workers,
        config={"agents": {"author_workers": 4, "agent_workers_per_author": 8},
                "authors": [{"id": f"P{a}"} for a in range(6)], "topics": tasks},
    )
    campaign = AuthorCampaign(pipeline)
    lock = threading.Lock()
    active, seen = Counter(), []
    peak_authors = peak_lanes = 0
    def paper(task):
        nonlocal peak_authors, peak_lanes
        owner = task["author_ids"][0]
        with lock:
            active[owner] += candidate_workers
            assert active[owner] <= 8
            assert sum(value > 0 for value in active.values()) <= 4
            assert sum(active.values()) <= 32
            peak_authors = max(peak_authors, sum(value > 0 for value in active.values()))
            peak_lanes = max(peak_lanes, sum(active.values()))
            seen.append(task["id"])
        time.sleep(0.03)
        with lock:
            active[owner] -= candidate_workers
    campaign.paper = paper
    campaign.schedule()
    assert Counter(seen) == Counter(task["id"] for task in tasks)
    assert peak_authors == 4
    assert peak_lanes > 8
    assert tasks[0]["author_ids"] == ["P0", "P5"]
