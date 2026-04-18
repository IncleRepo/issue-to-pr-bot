from __future__ import annotations

"""Workspace 안 작업용 입력/출력 경로와 레거시 fallback을 관리한다."""

import os
import re
import shutil
from pathlib import Path

from app.domain.models import IssueRequest


OUTPUT_ARTIFACT_ROOT_ENV = "BOT_OUTPUT_ARTIFACT_ROOT"
BOT_WORKSPACE_ROOT_ENV = "BOT_WORKSPACE_ROOT"
LEGACY_DEFAULT_OUTPUT_ARTIFACT_ROOT = Path.home() / ".issue-to-pr-bot" / "runtime-output"
WORKSPACE_BOT_DIRNAME = ".issue-to-pr-bot"
WORKSPACE_INPUT_DIRNAME = "input"
WORKSPACE_OUTPUT_DIRNAME = "output"
WORKSPACE_ATTACHMENTS_DIRNAME = "attachments"
LEGACY_WORKSPACE_OUTPUT_ARTIFACT_DIRNAME = ".runtime-output"
NON_PUBLISHABLE_WORKSPACE_DIRS = (
    f"{WORKSPACE_BOT_DIRNAME}/{WORKSPACE_INPUT_DIRNAME}",
    f"{WORKSPACE_BOT_DIRNAME}/{WORKSPACE_OUTPUT_DIRNAME}",
    LEGACY_WORKSPACE_OUTPUT_ARTIFACT_DIRNAME,
)
NON_PUBLISHABLE_WORKSPACE_PATHS = (
    "Microsoft/Windows/PowerShell/ModuleAnalysisCache",
)


def get_output_artifact_root() -> Path:
    """레거시 출력 산출물 루트를 반환한다."""

    configured = get_configured_legacy_output_root()
    if configured is not None:
        return configured
    return LEGACY_DEFAULT_OUTPUT_ARTIFACT_ROOT


def get_configured_legacy_output_root() -> Path | None:
    configured = os.getenv(OUTPUT_ARTIFACT_ROOT_ENV, "").strip()
    if not configured:
        return None

    candidate = Path(configured)
    if candidate.name == WORKSPACE_OUTPUT_DIRNAME and candidate.parent.name == WORKSPACE_BOT_DIRNAME:
        return None
    return candidate


def resolve_workspace_root(workspace: Path | None = None) -> Path:
    if workspace is not None:
        return workspace

    configured_workspace = os.getenv(BOT_WORKSPACE_ROOT_ENV, "").strip()
    if configured_workspace:
        return Path(configured_workspace)

    configured_output_root = os.getenv(OUTPUT_ARTIFACT_ROOT_ENV, "").strip()
    if configured_output_root:
        inferred = infer_workspace_root_from_output_root(Path(configured_output_root))
        if inferred is not None:
            return inferred

    return Path.cwd()


def infer_workspace_root_from_output_root(output_root: Path) -> Path | None:
    if output_root.name == WORKSPACE_OUTPUT_DIRNAME and output_root.parent.name == WORKSPACE_BOT_DIRNAME:
        return output_root.parent.parent
    if output_root.name == LEGACY_WORKSPACE_OUTPUT_ARTIFACT_DIRNAME:
        return output_root.parent
    return None


def get_workspace_bot_root(workspace: Path) -> Path:
    return workspace / WORKSPACE_BOT_DIRNAME


def get_workspace_input_root(workspace: Path) -> Path:
    return get_workspace_bot_root(workspace) / WORKSPACE_INPUT_DIRNAME


def get_workspace_output_root(workspace: Path) -> Path:
    return get_workspace_bot_root(workspace) / WORKSPACE_OUTPUT_DIRNAME


def get_workspace_attachment_root(workspace: Path) -> Path:
    return get_workspace_input_root(workspace) / WORKSPACE_ATTACHMENTS_DIRNAME


def get_repository_output_root(repository: str) -> Path:
    return get_output_artifact_root() / sanitize_repository_name(repository)


def get_task_output_root(request: IssueRequest, workspace: Path | None = None) -> Path:
    del request
    return get_workspace_output_root(resolve_workspace_root(workspace))


def get_workspace_output_artifact_root(workspace: Path) -> Path:
    return get_workspace_output_root(workspace)


def get_legacy_workspace_output_artifact_root(workspace: Path) -> Path:
    return workspace / LEGACY_WORKSPACE_OUTPUT_ARTIFACT_DIRNAME


def get_legacy_task_output_root(request: IssueRequest, workspace: Path | None = None) -> Path:
    workspace_root = resolve_workspace_root(workspace)
    return get_legacy_workspace_output_artifact_root(workspace_root) / sanitize_repository_name(request.repository) / build_task_output_slug(request)


def iter_output_artifact_paths(filename: str, request: IssueRequest, workspace: Path | None = None) -> list[Path]:
    workspace_root = resolve_workspace_root(workspace)
    primary = get_task_output_root(request, workspace_root) / filename
    candidates = [primary]
    legacy_roots = [get_legacy_task_output_root(request, workspace_root)]
    configured_legacy_root = get_configured_legacy_output_root()
    if configured_legacy_root is not None:
        legacy_roots.append(configured_legacy_root / sanitize_repository_name(request.repository) / build_task_output_slug(request))
    legacy_roots.append(LEGACY_DEFAULT_OUTPUT_ARTIFACT_ROOT / sanitize_repository_name(request.repository) / build_task_output_slug(request))
    for legacy_root in legacy_roots:
        candidate = legacy_root / filename
        if candidate not in candidates:
            candidates.append(candidate)
    return candidates


def find_existing_output_artifact_path(
    filename: str,
    request: IssueRequest,
    workspace: Path | None = None,
) -> Path | None:
    for candidate in iter_output_artifact_paths(filename, request, workspace):
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def get_pr_body_draft_path(request: IssueRequest, workspace: Path | None = None) -> Path:
    return get_task_output_root(request, workspace) / "pr-body.md"


def get_pr_title_draft_path(request: IssueRequest, workspace: Path | None = None) -> Path:
    return get_task_output_root(request, workspace) / "pr-title.txt"


def get_pr_summary_draft_path(request: IssueRequest, workspace: Path | None = None) -> Path:
    return get_task_output_root(request, workspace) / "pr-summary.md"


def get_commit_message_draft_path(request: IssueRequest, workspace: Path | None = None) -> Path:
    return get_task_output_root(request, workspace) / "commit-message.txt"


def ensure_task_output_root(request: IssueRequest, workspace: Path | None = None) -> Path:
    output_root = get_task_output_root(request, workspace)
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
    for blocked_dir in NON_PUBLISHABLE_WORKSPACE_DIRS:
        normalized_blocked_dir = blocked_dir.replace("\\", "/").strip("/")
        if normalized_path == normalized_blocked_dir:
            return True
        if normalized_path.startswith(f"{normalized_blocked_dir}/"):
            return True
        if normalized_blocked_dir.startswith(f"{normalized_path}/"):
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
