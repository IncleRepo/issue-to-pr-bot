"""Workspace 런타임 메타와 Codex 세션 수명주기를 관리한다."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable


WORKSPACE_RUNTIME_DIRNAME = ".issue-to-pr-bot-runtime"
WORKSPACE_META_FILENAME = "workspace-meta.json"
CODEX_HOME_ROOT_DIRNAME = "codex-home-root"
CODEX_HOME_DIRNAME = ".codex"
DEFAULT_WORKSPACE_TTL = timedelta(days=14)
ISSUE_AFTER_PR_TTL = timedelta(days=3)
CODEX_SESSION_REUSE_TTL = timedelta(days=3)


@dataclass(frozen=True)
class WorkspaceCleanupResult:
    workspace_path: Path
    runtime_path: Path
    reason: str


def resolve_workspace_runtime_root(workspace: Path) -> Path:
    return workspace.parent.parent / WORKSPACE_RUNTIME_DIRNAME / workspace.parent.name / workspace.name


def resolve_workspace_meta_path(workspace: Path) -> Path:
    return resolve_workspace_runtime_root(workspace) / WORKSPACE_META_FILENAME


def resolve_workspace_codex_home_root(workspace: Path) -> Path:
    return resolve_workspace_runtime_root(workspace) / CODEX_HOME_ROOT_DIRNAME


def resolve_workspace_codex_home_dir(workspace: Path) -> Path:
    return resolve_workspace_codex_home_root(workspace) / CODEX_HOME_DIRNAME


def read_workspace_metadata(workspace: Path) -> dict[str, Any]:
    path = resolve_workspace_meta_path(workspace)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def write_workspace_metadata(workspace: Path, metadata: dict[str, Any]) -> None:
    path = resolve_workspace_meta_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def touch_workspace_metadata(
    workspace: Path,
    *,
    repository: str | None = None,
    scope_type: str | None = None,
    scope_number: int | None = None,
) -> dict[str, Any]:
    metadata = read_workspace_metadata(workspace)
    now = now_utc().isoformat()
    metadata.setdefault("created_at", now)
    metadata["last_used_at"] = now
    if repository:
        metadata["repository"] = repository
    if scope_type:
        metadata["scope_type"] = scope_type
    if scope_number is not None:
        metadata["scope_number"] = int(scope_number)
    write_workspace_metadata(workspace, metadata)
    return metadata


def mark_workspace_linked_pull_request(workspace: Path, pull_request_number: int | None) -> None:
    if pull_request_number is None:
        return
    metadata = read_workspace_metadata(workspace)
    metadata["linked_pr_number"] = int(pull_request_number)
    metadata["last_published_at"] = now_utc().isoformat()
    write_workspace_metadata(workspace, metadata)


def mark_codex_session_ready(workspace: Path, *, resumed: bool) -> None:
    metadata = read_workspace_metadata(workspace)
    now = now_utc().isoformat()
    metadata["codex_session_ready"] = True
    metadata["codex_last_run_at"] = now
    metadata["last_used_at"] = now
    if resumed:
        metadata["codex_last_resumed_at"] = now
    write_workspace_metadata(workspace, metadata)


def invalidate_codex_session(workspace: Path) -> None:
    metadata = read_workspace_metadata(workspace)
    metadata["codex_session_ready"] = False
    metadata["codex_last_invalidated_at"] = now_utc().isoformat()
    write_workspace_metadata(workspace, metadata)


def should_resume_codex_session(workspace: Path) -> bool:
    metadata = read_workspace_metadata(workspace)
    if not metadata.get("codex_session_ready"):
        return False
    if not resolve_workspace_codex_home_dir(workspace).exists():
        return False
    last_run = parse_iso_datetime(metadata.get("codex_last_run_at"))
    if last_run is None:
        return False
    return now_utc() - last_run <= CODEX_SESSION_REUSE_TTL


def cleanup_stale_workspaces(
    workspace_root: Path,
    *,
    active_workspaces: Iterable[Path] = (),
) -> list[WorkspaceCleanupResult]:
    active = {path.resolve() for path in active_workspaces}
    removed: list[WorkspaceCleanupResult] = []
    for workspace in iter_candidate_workspaces(workspace_root):
        resolved_workspace = workspace.resolve()
        if resolved_workspace in active:
            continue
        runtime_root = resolve_workspace_runtime_root(workspace)
        ttl, reason = determine_workspace_ttl(workspace)
        reference_time = resolve_workspace_last_used_at(workspace)
        if reference_time is None or now_utc() - reference_time < ttl:
            continue
        shutil.rmtree(workspace, ignore_errors=True)
        shutil.rmtree(runtime_root, ignore_errors=True)
        removed.append(
            WorkspaceCleanupResult(
                workspace_path=workspace,
                runtime_path=runtime_root,
                reason=reason,
            )
        )
    return removed


def iter_candidate_workspaces(workspace_root: Path) -> Iterable[Path]:
    if not workspace_root.exists():
        return []
    candidates: list[Path] = []
    for repository_dir in workspace_root.iterdir():
        if not repository_dir.is_dir() or repository_dir.name == WORKSPACE_RUNTIME_DIRNAME:
            continue
        for scope_dir in repository_dir.iterdir():
            if scope_dir.is_dir():
                candidates.append(scope_dir)
    return candidates


def determine_workspace_ttl(workspace: Path) -> tuple[timedelta, str]:
    metadata = read_workspace_metadata(workspace)
    if metadata.get("scope_type") == "issue" and metadata.get("linked_pr_number"):
        return ISSUE_AFTER_PR_TTL, "issue-linked-pr"
    return DEFAULT_WORKSPACE_TTL, "default"


def resolve_workspace_last_used_at(workspace: Path) -> datetime | None:
    metadata = read_workspace_metadata(workspace)
    last_used = parse_iso_datetime(metadata.get("last_used_at"))
    if last_used is not None:
        return last_used

    candidates: list[datetime] = []
    for path in (workspace, resolve_workspace_runtime_root(workspace)):
        if path.exists():
            candidates.append(datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc))
    if not candidates:
        return None
    return max(candidates)


def infer_scope_from_workspace(workspace: Path) -> tuple[str, int | None]:
    name = str(workspace).replace("\\", "/").rstrip("/").split("/")[-1]
    if name.startswith("pr-"):
        return "pr", parse_scope_number(name[3:])
    if name.startswith("issue-"):
        return "issue", parse_scope_number(name[6:])
    return "task", None


def parse_scope_number(raw: str) -> int | None:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def parse_iso_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
