#!/usr/bin/env python3
"""Copy existing compiled difficulty assessments into published GitLab metadata.

Only publication-ledger entries are in scope. This script never generates an
assessment, republishes a repository, changes a README, or modifies a ledger.
The default is a read-only dry run; --apply enables metadata/problem.json PUTs.
Durable JSONL receipts and remote equality checks make interrupted runs resumable.
"""

from __future__ import annotations

import argparse
import base64
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import threading
import time
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

import yaml


METADATA_FILE = "metadata/problem.json"
RETRYABLE_HTTP = {400, 409, 429, 500, 502, 503, 504}
SECRET_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\b(?:sk-|glpat-)[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"(?i)(?:api[_ -]?key|access[_ -]?token|password)\s*[:=]\s*\S+"),
)


class BackfillError(RuntimeError):
    """A diagnostic that contains no authentication or HTTP response content."""


class APIError(BackfillError):
    def __init__(self, status: int | None, method: str) -> None:
        self.status = status
        super().__init__(f"GitLab {method} failed: HTTP {status}" if status else
                         f"GitLab {method} failed: network error")


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def load_object(path: Path) -> dict[str, Any]:
    # Campaign state/ledger snapshots can briefly be incomplete during writes.
    for attempt in range(4):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                raise BackfillError(f"Expected JSON object: {path}")
            return value
        except json.JSONDecodeError:
            if attempt == 3:
                raise BackfillError(f"Invalid JSON: {path}") from None
            time.sleep(0.2 * (attempt + 1))
    raise AssertionError("unreachable")


def collect_entries(run_dirs: list[Path]) -> list[dict[str, Any]]:
    """Enumerate the current published mapping, never a hand-maintained ID list."""
    entries: list[dict[str, Any]] = []
    seen_projects: set[int] = set()
    for run_dir in sorted(set(path.resolve() for path in run_dirs)):
        ledger = load_object(run_dir / "gewulab-publication" / "ledger.json")
        state = load_object(run_dir / "state.json")
        published = ledger.get("published")
        if not isinstance(published, dict):
            raise BackfillError(f"Invalid published mapping: {run_dir}")
        for problem_id, publication in sorted(published.items()):
            entry = {"run_dir": str(run_dir), "problem_id": str(problem_id),
                     "publication": publication, "state": state}
            if isinstance(publication, dict):
                project_id = publication.get("project_id")
                if type(project_id) is int and project_id > 0:
                    if project_id in seen_projects:
                        raise BackfillError(f"Duplicate published project ID: {project_id}")
                    seen_projects.add(project_id)
            entries.append(entry)
    return entries


def source_assessment(entry: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    publication = entry["publication"]
    if not isinstance(publication, dict):
        raise BackfillError("Invalid publication record")
    candidate_id = publication.get("candidate_id")
    if (not isinstance(candidate_id, str) or candidate_id in {".", ".."}
            or not re.fullmatch(r"[A-Za-z0-9_.-]+", candidate_id)):
        raise BackfillError("Missing or invalid published candidate ID")
    state = entry["state"]
    candidate = (state.get("candidates") or {}).get(candidate_id)
    stage = (state.get("stages") or {}).get(f"candidate.{candidate_id}.compile")
    if not isinstance(candidate, dict) or candidate.get("problem_id") != entry["problem_id"]:
        raise BackfillError("Published candidate and state problem IDs disagree")
    if not isinstance(stage, dict) or stage.get("status") != "completed":
        raise BackfillError("Published candidate does not have a completed compile stage")
    source_path = Path(entry["run_dir"]) / "candidates" / candidate_id / "problem.yaml"
    if not source_path.resolve().is_relative_to((Path(entry["run_dir"]) / "candidates").resolve()):
        raise BackfillError("Compiled manifest escapes the candidate directory")
    fields: dict[str, Any] = {"candidate_id": candidate_id, "source_manifest": str(source_path)}
    if not source_path.is_file():
        return None, {**fields, "reason": "missing_compiled_manifest"}
    raw = source_path.read_bytes()
    fields["source_sha256"] = sha256(raw)
    try:
        manifest = yaml.safe_load(raw.decode("utf-8"))
    except (UnicodeError, yaml.YAMLError):
        raise BackfillError("Invalid compiled manifest") from None
    if not isinstance(manifest, dict) or manifest.get("problem_id") != entry["problem_id"]:
        raise BackfillError("Compiled manifest and published problem IDs disagree")
    difficulty = manifest.get("verification_difficulty")
    if difficulty is None:
        return None, {**fields, "reason": "unassessed"}
    if (not isinstance(difficulty, dict) or set(difficulty) != {"score", "rationale"}
            or type(difficulty.get("score")) is not int or not 0 <= difficulty["score"] <= 10
            or not isinstance(difficulty.get("rationale"), str)
            or not difficulty["rationale"].strip()):
        raise BackfillError("Invalid compiled verification_difficulty (score 0-10 and rationale required)")
    if any(pattern.search(difficulty["rationale"]) for pattern in SECRET_PATTERNS):
        raise BackfillError("Refusing possible secret material in verification difficulty rationale")
    return difficulty, fields


class GitLab:
    def __init__(self, base_url: str, token: str, attempts: int = 3) -> None:
        parsed = urllib.parse.urlparse(base_url)
        if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
            raise BackfillError("GitLab URL must be HTTPS without embedded credentials")
        self.api_url = base_url.rstrip("/") + "/api/v4"
        self.token = token
        self.attempts = attempts

    def request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        headers = {"Authorization": f"Bearer {self.token}",
                   "User-Agent": "gewulab-difficulty-backfill/1.0"}
        body = None
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.api_url + path, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=45) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            # Response bodies can include arbitrary server data: never log them.
            raise APIError(exc.code, method) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise APIError(None, method) from None
        try:
            return json.loads(raw.decode("utf-8")) if raw else None
        except (UnicodeError, json.JSONDecodeError):
            raise BackfillError("GitLab returned invalid JSON") from None

    def get(self, path: str) -> Any:
        for attempt in range(self.attempts):
            try:
                return self.request("GET", path)
            except APIError as exc:
                if exc.status not in RETRYABLE_HTTP | {None} or attempt + 1 == self.attempts:
                    raise
                retry_pause(attempt)
        raise AssertionError("unreachable")


def retry_pause(attempt: int) -> None:
    time.sleep(min(2 ** attempt, 8))


def file_path(project_id: int, filename: str, branch: str | None = None) -> str:
    path = f"/projects/{project_id}/repository/files/{urllib.parse.quote(filename, safe='')}"
    return path + "?" + urllib.parse.urlencode({"ref": branch}) if branch else path


def file_bytes(value: Any) -> bytes:
    if not isinstance(value, dict) or value.get("encoding") != "base64":
        raise BackfillError("GitLab file response has no base64 content")
    try:
        return base64.b64decode("".join(value["content"].split()), validate=True)
    except (KeyError, ValueError, TypeError):
        raise BackfillError("GitLab file response has invalid content") from None


def metadata_object(value: Any) -> tuple[dict[str, Any], bytes]:
    raw = file_bytes(value)
    try:
        metadata = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        raise BackfillError("Published metadata is not valid UTF-8 JSON") from None
    if not isinstance(metadata, dict):
        raise BackfillError("Published metadata must be an object")
    if not isinstance(value.get("last_commit_id"), str) or not value["last_commit_id"]:
        raise BackfillError("Published metadata has no last_commit_id")
    return metadata, raw


def assessment_matches(value: Any, difficulty: dict[str, Any]) -> bool:
    return (isinstance(value, dict) and type(value.get("score")) is int
            and value == difficulty)


def backfill_one(entry: dict[str, Any], gitlab: GitLab, *, apply: bool = False,
                 verify_readme: bool = False, attempts: int = 3) -> dict[str, Any]:
    result: dict[str, Any] = {"run_dir": entry["run_dir"], "problem_id": entry["problem_id"]}
    try:
        difficulty, fields = source_assessment(entry)
        result.update(fields)
        if difficulty is None:
            return {**result, "status": "skipped"}
        result["verification_difficulty"] = difficulty
        publication = entry["publication"]
        project_id, project_path = publication.get("project_id"), publication.get("project_path")
        if type(project_id) is not int or project_id <= 0:
            raise BackfillError("Missing or invalid published project ID")
        if not isinstance(project_path, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+", project_path):
            raise BackfillError("Missing or invalid published project path")
        result.update(project_id=project_id, project_path=project_path)
        project = gitlab.get(f"/projects/{project_id}")
        if (not isinstance(project, dict) or project.get("id") != project_id
                or project.get("path_with_namespace") != project_path):
            raise BackfillError("GitLab project ID/path disagrees with publication ledger")
        branch = project.get("default_branch")
        if not isinstance(branch, str) or not branch:
            raise BackfillError("Published project has no default branch")
        result["branch"] = branch
        metadata_get = file_path(project_id, METADATA_FILE, branch)
        readme_get = file_path(project_id, "README.md", branch)
        if verify_readme:
            result["readme_sha256_before"] = sha256(file_bytes(gitlab.get(readme_get)))
        wrote = False
        pending_content: bytes | None = None
        for attempt in range(attempts):
            try:
                current_file = gitlab.get(metadata_get)
            except APIError as exc:
                if exc.status == 404:
                    return {**result, "status": "skipped", "reason": "missing_remote_metadata"}
                raise
            current, raw = metadata_object(current_file)
            result.setdefault("metadata_sha256_before", sha256(raw))
            result["last_commit_id_before"] = current_file["last_commit_id"]
            result["attempts"] = attempt + 1
            if assessment_matches(current.get("verification_difficulty"), difficulty):
                if pending_content is not None and raw != pending_content:
                    raise BackfillError("Uncertain update did not match the exact written document")
                result.update(status="updated" if wrote else "unchanged",
                              metadata_sha256_after=sha256(raw),
                              last_commit_id_after=current_file["last_commit_id"])
                break
            # Preserve all remote values, including arbitrary extension fields.
            desired = {**current, "verification_difficulty": difficulty}
            content = json.dumps(desired, ensure_ascii=False, indent=2) + "\n"
            result["metadata_sha256_expected"] = sha256(content.encode("utf-8"))
            if not apply:
                result["status"] = "would_update"
                break
            try:
                pending_content = content.encode("utf-8")
                gitlab.request("PUT", file_path(project_id, METADATA_FILE), {
                    "branch": branch, "content": content, "encoding": "text",
                    "commit_message": f"Add assessed verification difficulty for {entry['problem_id']}",
                    "last_commit_id": current_file["last_commit_id"],
                })
                wrote = True
            except APIError as exc:
                # A timed-out PUT may have succeeded. Re-read before another PUT;
                # conflicts also re-merge all newly added remote metadata fields.
                if exc.status not in RETRYABLE_HTTP | {None}:
                    raise
                if exc.status in {400, 409}:
                    pending_content = None
                else:
                    wrote = True
                result["last_retry_http_status"] = exc.status
                retry_pause(attempt)
                continue
            verified_file = gitlab.get(metadata_get)
            verified, verified_raw = metadata_object(verified_file)
            if verified != desired or verified_raw != content.encode("utf-8"):
                raise BackfillError("Updated metadata did not match the exact written document")
            result.update(status="updated", metadata_sha256_after=sha256(verified_raw),
                          last_commit_id_after=verified_file["last_commit_id"])
            break
        else:
            # Include a final read to recover success after the last uncertain PUT.
            final_file = gitlab.get(metadata_get)
            final, final_raw = metadata_object(final_file)
            if not assessment_matches(final.get("verification_difficulty"), difficulty):
                raise BackfillError("Metadata update exhausted bounded retries")
            if pending_content is not None and final_raw != pending_content:
                raise BackfillError("Uncertain update did not match the exact written document")
            result.update(status="updated", metadata_sha256_after=sha256(final_raw),
                          last_commit_id_after=final_file["last_commit_id"])
        if verify_readme:
            result["readme_sha256_after"] = sha256(file_bytes(gitlab.get(readme_get)))
            if result["readme_sha256_before"] != result["readme_sha256_after"]:
                raise BackfillError("README changed during metadata backfill")
        return result
    except Exception as exc:
        # Only our deliberately sanitized diagnostics enter logs/receipts.
        error = str(exc) if isinstance(exc, BackfillError) else type(exc).__name__
        return {**result, "status": "failed", "error": error}


class Receipts:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, result: dict[str, Any]) -> None:
        record = {"schema_version": 1, "at": datetime.now(timezone.utc).isoformat(), **result}
        line = json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
        with self.lock:
            # A partial last record remains visible for forensics after a crash.
            # Ensure the next complete receipt starts on its own line.
            if self.path.exists() and self.path.stat().st_size:
                with self.path.open("rb") as handle:
                    handle.seek(-1, os.SEEK_END)
                    needs_newline = handle.read(1) != b"\n"
            else:
                needs_newline = False
            with self.path.open("a", encoding="utf-8", newline="\n") as handle:
                if needs_newline:
                    handle.write("\n")
                handle.write(line)
                handle.flush()
                os.fsync(handle.fileno())


def token_from_file(path: Path) -> str:
    if os.name != "nt" and stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise BackfillError("GitLab token file must have mode 0600")
    token = path.read_text(encoding="utf-8").strip()
    if not token:
        raise BackfillError("GitLab token file is empty")
    return token


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, action="append", nargs="+", required=True)
    parser.add_argument("--gitlab-url", default="https://git.gewu-lab.ai")
    parser.add_argument("--token-file", type=Path,
                        default=Path("/root/.config/opr-author-fleet/gitlab-token"))
    parser.add_argument("--receipts", type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--dry-run", action="store_true", help="default: GET requests only")
    parser.add_argument("--verify-readme", action="store_true", help="compare remote README hashes before/after")
    parser.add_argument("--workers", type=int, choices=range(1, 5), default=4)
    parser.add_argument("--max-items", type=int)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.max_items is not None and args.max_items < 1:
            raise BackfillError("--max-items must be positive")
        runs = [path for group in args.run_dir for path in group]
        entries = collect_entries(runs)
        if args.max_items is not None:
            entries = entries[:args.max_items]
        receipts = Receipts(args.receipts or runs[0] / "gewulab-publication" /
                            "verification-difficulty-backfill.receipts.jsonl")
        gitlab = GitLab(args.gitlab_url, token_from_file(args.token_file))
        counts: dict[str, int] = {}
        print(json.dumps({"event": "scan", "selected": len(entries), "apply": args.apply}), flush=True)
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = [executor.submit(backfill_one, entry, gitlab, apply=args.apply,
                                       verify_readme=args.verify_readme) for entry in entries]
            for future in as_completed(futures):
                result = future.result()
                receipts.append(result)
                counts[result["status"]] = counts.get(result["status"], 0) + 1
                print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
        print(json.dumps({"event": "summary", "selected": len(entries), "counts": counts}), flush=True)
        return 1 if counts.get("failed") else 0
    except Exception as exc:
        error = str(exc) if isinstance(exc, BackfillError) else type(exc).__name__
        print(json.dumps({"event": "fatal", "error": error}), flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
