from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

from app.domain.models import IssueRequest


OUTPUT_ARTIFACT_ROOT_ENV = "BOT_OUTPUT_ARTIFACT_ROOT"
DEFAULT_OUTPUT_ARTIFACT_ROOT = Path.home() / ".issue-to-pr-bot" / "runtime-output"
WORKSPACE_OUTPUT_ARTIFACT_DIRNAME = ".runtime-output"
NON_PUBLISHABLE_WORKSPACE_PATHS = (
    "Microsoft/Windows/PowerShell/ModuleAnalysisCache",
)


def get_output_artifact_root() -> Path:
    configured = os.getenv(OUTPUT_ARTIFACT_ROOT_ENV, "").strip()
    if configured:
        return Path(configured)
    return DEFAULT_OUTPUT_ARTIFACT_ROOT


def get_repository_output_root(repository: str) -> Path:
    return get_output_artifact_root() / sanitize_repository_name(repository)


def get_task_output_root(request: IssueRequest) -> Path:
    return get_repository_output_root(request.repository) / build_task_output_slug(request)


def get_workspace_output_artifact_root(workspace: Path) -> Path:
    return workspace / WORKSPACE_OUTPUT_ARTIFACT_DIRNAME


def get_pr_body_draft_path(request: IssueRequest) -> Path:
    return get_task_output_root(request) / "pr-body.md"


def get_pr_title_draft_path(request: IssueRequest) -> Path:
    return get_task_output_root(request) / "pr-title.txt"


def get_pr_summary_draft_path(request: IssueRequest) -> Path:
    return get_task_output_root(request) / "pr-summary.md"


def get_commit_message_draft_path(request: IssueRequest) -> Path:
    return get_task_output_root(request) / "commit-message.txt"


def ensure_task_output_root(request: IssueRequest) -> Path:
    output_root = get_task_output_root(request)
    output_root.mkdir(parents=True, exist_ok=True)
    return output_root


def cleanup_repository_output_artifacts(repository: str) -> None:
    output_root = get_repository_output_root(repository)
    if output_root.exists():
        shutil.rmtree(output_root, ignore_errors=True)


def is_non_publishable_workspace_path(path: str, output_dir: str) -> bool:
    normalized_path = path.replace("\\", "/").strip("/")
    normalized_output_dir = output_dir.replace("\\", "/").strip("/")
    if normalized_output_dir and (
        normalized_path == normalized_output_dir
        or normalized_path.startswith(f"{normalized_output_dir}/")
    ):
        return True
    if normalized_path == WORKSPACE_OUTPUT_ARTIFACT_DIRNAME or normalized_path.startswith(
        f"{WORKSPACE_OUTPUT_ARTIFACT_DIRNAME}/"
    ):
        return True
    for blocked_path in NON_PUBLISHABLE_WORKSPACE_PATHS:
        normalized_blocked = blocked_path.replace("\\", "/").strip("/")
        if normalized_path == normalized_blocked:
            return True
        if normalized_path.startswith(f"{normalized_blocked}/"):
            return True
        if normalized_blocked.startswith(f"{normalized_path}/"):
            return True
    return False


def sanitize_repository_name(repository: str) -> str:
    sanitized = repository.replace("\\", "/").strip("/")
    sanitized = sanitized.replace("/", "__")
    sanitized = re.sub(r"[^A-Za-z0-9._-]+", "-", sanitized)
    return sanitized.strip("-") or "unknown-repository"


def build_task_output_slug(request: IssueRequest) -> str:
    comment_id = request.comment_id or 0
    suffix = f"-comment-{comment_id}" if comment_id else ""
    if request.is_pull_request and request.pull_request_number:
        return f"pr-{request.pull_request_number}{suffix}"
    return f"issue-{request.issue_number}{suffix}"
