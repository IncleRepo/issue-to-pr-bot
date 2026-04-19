"""Git 작업과 GitHub API 호출을 묶어 PR 흐름을 처리하는 서비스 모듈."""

import json
import os
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path

from app.bot import (
    IssueRequest,
    build_branch_name,
    build_pull_request_title,
    build_task_prompt,
    build_test_commit_message,
)
from app.config import BOT_MENTION, BotConfig, bot_slug_from_mention, get_check_commands, load_config
from app.domain.models import MetadataPlan
from app.metadata_rules import infer_issue_metadata, infer_pull_request_metadata
from app.output_artifacts import find_existing_output_artifact_path, is_non_publishable_workspace_path
from app.workspace_state import invalidate_codex_session, mark_workspace_linked_pull_request

BOT_PR_MARKER = "<!-- incle-issue-to-pr-bot -->"
BOT_AUTO_MERGE_MARKER = "<!-- incle-issue-to-pr-bot:auto-merge -->"
NON_PUBLISHABLE_WORKSPACE_CHANGES_ERROR_PREFIX = "Non-publishable workspace files are present in the publishable diff."


def build_hidden_windows_subprocess_kwargs() -> dict[str, object]:
    if os.name != "nt":
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0
    return {
        "creationflags": subprocess.CREATE_NO_WINDOW,
        "startupinfo": startupinfo,
    }


@dataclass(frozen=True)
class PullRequestResult:
    branch_name: str
    pull_request_url: str | None
    created: bool
    changed_files: list[str]
    verification_commands: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class CheckoutTarget:
    branch_name: str
    base_branch: str
    pull_request_number: int | None = None
    pull_request_url: str | None = None


@dataclass(frozen=True)
class BaseSyncResult:
    attempted: bool
    mode: str = "merge"
    up_to_date: bool = False
    has_conflicts: bool = False
    changed_tree: bool = False
    base_branch: str | None = None


@dataclass(frozen=True)
class WorktreeState:
    entries: tuple[str, ...] = ()
    staged_changes: bool = False
    unstaged_changes: bool = False
    untracked_files: bool = False

    @property
    def dirty(self) -> bool:
        return bool(self.entries)


@dataclass(frozen=True)
class MergeRequestResult:
    pull_request_url: str | None
    requested: bool
    merged: bool
    merge_sha: str | None = None


def create_test_pr(
    request: IssueRequest,
    workspace: Path,
    config: BotConfig | None = None,
) -> PullRequestResult:
    config = config or load_config(workspace)
    target = checkout_request_target(request, workspace, config)

    write_marker_file(request, workspace, config)
    return commit_push_and_open_pr(
        request=request,
        workspace=workspace,
        config=config,
        branch_name=target.branch_name,
        base_branch=target.base_branch,
        commit_message=build_test_commit_message(request, config),
        add_paths=[config.output_dir],
        verification_commands=[],
    )


def checkout_request_target(request: IssueRequest, workspace: Path, config: BotConfig) -> CheckoutTarget:
    if request.is_pull_request:
        return checkout_pull_request_branch(request, workspace)
    return checkout_issue_branch(request, workspace, config)


def checkout_issue_branch(request: IssueRequest, workspace: Path, config: BotConfig) -> CheckoutTarget:
    """이슈 기반 작업 브랜치를 준비한다.

    같은 이름의 원격 브랜치가 이미 있으면 그 위치에서 이어서 작업한다.
    """
    branch_name = build_branch_name(request, config)
    configure_git(workspace)
    if remote_branch_exists(branch_name, workspace):
        print(f"기존 원격 브랜치를 기준으로 작업을 이어갑니다: {branch_name}")
        run_git(["fetch", "origin", branch_name], workspace)
        run_git(["checkout", "-B", branch_name, "FETCH_HEAD"], workspace)
    else:
        run_git(["checkout", "-B", branch_name], workspace)
    reset_worktree_if_requested(workspace)
    return CheckoutTarget(
        branch_name=branch_name,
        base_branch=config.default_base_branch or (config.git_sync_rule.base_branch if config.git_sync_rule else None) or os.getenv("GITHUB_REF_NAME") or "main",
    )


def checkout_pull_request_branch(request: IssueRequest, workspace: Path) -> CheckoutTarget:
    repository = os.getenv("GITHUB_REPOSITORY") or request.repository
    token = os.getenv("BOT_GITHUB_TOKEN") or os.getenv("GITHUB_TOKEN")
    if not token:
        raise RuntimeError("PR 브랜치를 조회하려면 GitHub token이 필요합니다.")
    if not request.pull_request_number:
        raise RuntimeError("PR 번호를 찾을 수 없어 PR 브랜치를 체크아웃할 수 없습니다.")

    pull_request = get_pull_request(repository, request.pull_request_number, token)
    head_ref = pull_request["head"]["ref"]
    head_repo = (pull_request["head"].get("repo") or {}).get("full_name") or repository
    base_ref = pull_request["base"]["ref"]
    html_url = pull_request["html_url"]

    if head_repo != repository:
        raise RuntimeError("fork PR 브랜치 자동 수정은 아직 지원하지 않습니다.")

    configure_git(workspace)
    run_git(["fetch", "origin", head_ref], workspace)
    run_git(["checkout", "-B", head_ref, "FETCH_HEAD"], workspace)
    reset_worktree_if_requested(workspace)
    return CheckoutTarget(
        branch_name=head_ref,
        base_branch=base_ref,
        pull_request_number=int(pull_request["number"]),
        pull_request_url=html_url,
    )


def sync_pull_request_branch_with_base(workspace: Path, base_branch: str) -> BaseSyncResult:
    return sync_branch_with_base(workspace, base_branch, "merge")


def apply_base_sync_strategy(
    workspace: Path,
    base_branch: str,
    mode: str,
    *,
    allow_autostash: bool,
) -> BaseSyncResult:
    """현재 worktree 상태에 맞춰 안전한 base sync 전략을 수행한다.

    깨끗한 상태면 merge/rebase를 바로 적용하고, dirty 상태면 autostash를
    사용해 임시 보호 후 sync를 수행한다. stash 복원 뒤 추가 정리는 Codex가
    처리하도록 남기고, 충돌이 나면 즉시 예외를 반환한다.
    """

    state = inspect_worktree_state(workspace)
    if not state.dirty:
        return sync_branch_with_base(workspace, base_branch, mode)

    if not allow_autostash:
        raise RuntimeError(
            f"Base branch sync cannot run on a dirty worktree without autostash: mode={mode}, origin/{base_branch}"
        )

    stash_label = build_autostash_label(base_branch, mode)
    stashed = create_temporary_stash(workspace, stash_label)
    if not stashed:
        return sync_branch_with_base(workspace, base_branch, mode)

    try:
        sync_result = sync_branch_with_base(workspace, base_branch, mode)
    except Exception:
        abort_base_sync(workspace, mode)
        restore_temporary_stash(workspace, stash_label)
        raise

    if sync_result.has_conflicts:
        abort_base_sync(workspace, mode)
        restore_temporary_stash(workspace, stash_label)
        raise RuntimeError(
            f"Base branch sync could not be applied safely on top of local changes: mode={mode}, origin/{base_branch}"
        )

    restore_result = restore_temporary_stash(workspace, stash_label)
    if restore_result == "conflicted":
        return BaseSyncResult(
            attempted=True,
            mode=mode,
            up_to_date=sync_result.up_to_date,
            has_conflicts=True,
            changed_tree=True,
            base_branch=base_branch,
        )

    return BaseSyncResult(
        attempted=True,
        mode=mode,
        up_to_date=sync_result.up_to_date,
        has_conflicts=False,
        changed_tree=sync_result.changed_tree,
        base_branch=base_branch,
    )


def sync_branch_with_base(workspace: Path, base_branch: str, mode: str) -> BaseSyncResult:
    configure_git(workspace)
    run_git(["fetch", "origin", base_branch], workspace)
    result = subprocess.run(
        build_base_sync_command(workspace, base_branch, mode),
        cwd=workspace,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        **build_hidden_windows_subprocess_kwargs(),
    )
    if (result.stdout or "").strip():
        print((result.stdout or "").rstrip())
    output = (result.stdout or "").lower()

    if result.returncode == 0:
        return BaseSyncResult(
            attempted=True,
            mode=mode,
            up_to_date="already up to date" in output,
            has_conflicts=False,
            changed_tree="already up to date" not in output and "up to date" not in output,
            base_branch=base_branch,
        )

    if has_unmerged_paths(workspace):
        print("Base branch sync produced merge conflicts. Leaving the worktree in conflict state for Codex.")
        return BaseSyncResult(attempted=True, mode=mode, has_conflicts=True, changed_tree=True, base_branch=base_branch)

    raise RuntimeError(
        f"Base branch sync failed before Codex could continue: mode={mode}, origin/{base_branch}"
    )


def inspect_worktree_state(workspace: Path) -> WorktreeState:
    result = subprocess.run(
        ["git", "-c", f"safe.directory={workspace}", "-c", "core.autocrlf=false", "status", "--porcelain"],
        cwd=workspace,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        **build_hidden_windows_subprocess_kwargs(),
    )
    if result.returncode != 0:
        raise RuntimeError(f"Unable to inspect worktree state before sync:\n{(result.stdout or '').strip()}")

    entries = tuple(line for line in (result.stdout or "").splitlines() if line.strip())
    return WorktreeState(
        entries=entries,
        staged_changes=any(not line.startswith(("??", " ")) for line in entries),
        unstaged_changes=any(len(line) >= 2 and line[1] not in {" ", "?"} for line in entries),
        untracked_files=any(line.startswith("??") for line in entries),
    )


def build_autostash_label(base_branch: str, mode: str) -> str:
    suffix = os.urandom(4).hex()
    return f"issue-to-pr-bot/{mode}/{base_branch}/{suffix}"


def create_temporary_stash(workspace: Path, label: str) -> bool:
    result = subprocess.run(
        [
            "git",
            "-c",
            f"safe.directory={workspace}",
            "-c",
            "core.autocrlf=false",
            "stash",
            "push",
            "--include-untracked",
            "-m",
            label,
        ],
        cwd=workspace,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        **build_hidden_windows_subprocess_kwargs(),
    )
    output = result.stdout or ""
    if output.strip():
        print(output.rstrip())
    if result.returncode != 0:
        raise RuntimeError(f"Failed to protect local changes before base sync:\n{output.strip()}")
    return "No local changes to save" not in output


def restore_temporary_stash(workspace: Path, label: str) -> str:
    stash_ref = find_temporary_stash_reference(workspace, label)
    if not stash_ref:
        return "missing"

    result = subprocess.run(
        [
            "git",
            "-c",
            f"safe.directory={workspace}",
            "-c",
            "core.autocrlf=false",
            "stash",
            "pop",
            stash_ref,
        ],
        cwd=workspace,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        **build_hidden_windows_subprocess_kwargs(),
    )
    output = result.stdout or ""
    if output.strip():
        print(output.rstrip())
    if result.returncode == 0:
        return "restored"
    if has_unmerged_paths(workspace):
        return "conflicted"
    raise RuntimeError(f"Failed to restore local changes after base sync:\n{output.strip()}")


def find_temporary_stash_reference(workspace: Path, label: str) -> str | None:
    result = subprocess.run(
        [
            "git",
            "-c",
            f"safe.directory={workspace}",
            "-c",
            "core.autocrlf=false",
            "stash",
            "list",
            "--format=%gd %gs",
        ],
        cwd=workspace,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        **build_hidden_windows_subprocess_kwargs(),
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to inspect temporary stash list:\n{(result.stdout or '').strip()}")

    for line in (result.stdout or "").splitlines():
        if label not in line:
            continue
        parts = line.split(" ", 1)
        if parts:
            return parts[0].strip()
    return None


def abort_base_sync(workspace: Path, mode: str) -> None:
    normalized_mode = mode.strip().lower()
    command = ["rebase", "--abort"] if normalized_mode == "rebase" else ["merge", "--abort"]
    subprocess.run(
        ["git", "-c", f"safe.directory={workspace}", "-c", "core.autocrlf=false", *command],
        cwd=workspace,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        **build_hidden_windows_subprocess_kwargs(),
    )


def build_base_sync_command(workspace: Path, base_branch: str, mode: str) -> list[str]:
    common_prefix = [
        "git",
        "-c",
        f"safe.directory={workspace}",
        "-c",
        "core.autocrlf=false",
    ]
    normalized_mode = mode.strip().lower()
    if normalized_mode == "rebase":
        return [*common_prefix, "rebase", f"origin/{base_branch}"]
    return [*common_prefix, "merge", "--no-ff", "--no-commit", f"origin/{base_branch}"]


def reset_worktree_if_requested(workspace: Path) -> None:
    if os.getenv("BOT_RESET_WORKTREE") != "1":
        return

    print("작업 브랜치 초기 상태를 HEAD 기준으로 정리합니다.")
    run_git(["reset", "--hard", "HEAD"], workspace)
    run_git(["clean", "-fd"], workspace)


def commit_push_and_open_pr(
    request: IssueRequest,
    workspace: Path,
    config: BotConfig,
    branch_name: str,
    base_branch: str,
    commit_message: str | None = None,
    add_paths: list[str] | None = None,
    verification_commands: list[str] | None = None,
) -> PullRequestResult:
    """변경 내용을 원격에 반영하고 PR을 생성하거나 갱신한다."""
    token = os.getenv("BOT_GITHUB_TOKEN") or os.getenv("GITHUB_TOKEN")
    if not token:
        raise RuntimeError("BOT_GITHUB_TOKEN 또는 GITHUB_TOKEN이 없어 PR을 생성할 수 없습니다.")

    repository = os.getenv("GITHUB_REPOSITORY") or request.repository

    print("변경 파일 확인:")
    run_git(["status", "--short"], workspace)
    effective_branch_name = resolve_publish_branch_name(workspace, branch_name, base_branch)

    for path in add_paths or ["--all"]:
        run_git(["add", path], workspace)
    if add_paths is None:
        unstage_output_artifacts(workspace, config.output_dir)

    if not has_staged_changes(workspace):
        if not branch_has_publishable_commits(workspace, effective_branch_name, base_branch):
            print("로컬 커밋이나 staged 변경이 없어 PR 생성을 건너뜁니다.")
            return PullRequestResult(
                branch_name=effective_branch_name,
                pull_request_url=None,
                created=False,
                changed_files=[],
                verification_commands=verification_commands or [],
            )
        raw_changed_files = get_raw_branch_changed_files(workspace, base_branch)
        ensure_no_non_publishable_workspace_changes(raw_changed_files, config.output_dir)
        changed_files = filter_output_artifact_paths(raw_changed_files, config.output_dir)
        ensure_no_protected_changes(changed_files, config)
    else:
        raw_staged_files = get_staged_files(workspace)
        ensure_no_non_publishable_workspace_changes(raw_staged_files, config.output_dir)
        changed_files = filter_output_artifact_paths(raw_staged_files, config.output_dir)
        ensure_no_protected_changes(changed_files, config)
        if not commit_message:
            invalidate_codex_session(workspace)
            raise RuntimeError(
                "Codex finished with local changes but no local commit. "
                "Create the publishable commit inside the workspace before the wrapper pushes."
            )
        run_git(["commit", "-m", commit_message], workspace)

    push_branch(repository, effective_branch_name, token, workspace)
    pr_url = ensure_pull_request(
        repository,
        effective_branch_name,
        base_branch,
        request,
        token,
        config,
        workspace,
        changed_files,
        verification_commands or [],
    )
    mark_workspace_linked_pull_request(workspace, parse_pull_request_number(pr_url))
    apply_pull_request_metadata_if_possible(
        repository=repository,
        pull_request_url=pr_url,
        request=request,
        token=token,
        workspace=workspace,
        changed_files=changed_files,
    )

    return PullRequestResult(
        branch_name=effective_branch_name,
        pull_request_url=pr_url,
        created=True,
        changed_files=changed_files,
        verification_commands=verification_commands or [],
    )


def write_marker_file(
    request: IssueRequest,
    workspace: Path,
    config: BotConfig | None = None,
) -> Path:
    config = config or BotConfig()
    output_dir = workspace / config.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    output_file = output_dir / f"issue-{request.issue_number}.md"
    output_file.write_text(
        "\n".join(
            [
                f"# Issue #{request.issue_number}",
                "",
                f"- Repository: {request.repository}",
                f"- Title: {request.issue_title}",
                f"- Comment author: {request.comment_author}",
                f"- Comment id: {request.comment_id}",
                f"- Branch: {build_branch_name(request, config)}",
                "",
                "## Issue Body",
                "",
                request.issue_body.strip() or "(empty)",
                "",
                "## Trigger Comment",
                "",
                request.comment_body.strip(),
                "",
                "## Generated Task Prompt",
                "",
                "```text",
                build_task_prompt(request, config),
                "```",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return output_file


def configure_git(workspace: Path) -> None:
    bot_slug = bot_identity_slug()
    run_git(["config", "user.name", f"{bot_slug}[bot]"], workspace)
    run_git(["config", "user.email", f"{bot_slug}@users.noreply.github.com"], workspace)


def bot_identity_slug() -> str:
    return bot_slug_from_mention(os.getenv("BOT_MENTION") or BOT_MENTION)


def has_staged_changes(workspace: Path) -> bool:
    result = subprocess.run(
        [
            "git",
            "-c",
            f"safe.directory={workspace}",
            "-c",
            "core.autocrlf=false",
            "diff",
            "--cached",
            "--quiet",
        ],
        cwd=workspace,
        check=False,
        **build_hidden_windows_subprocess_kwargs(),
    )
    return result.returncode != 0


def get_current_branch(workspace: Path) -> str | None:
    result = subprocess.run(
        [
            "git",
            "-c",
            f"safe.directory={workspace}",
            "-c",
            "core.autocrlf=false",
            "branch",
            "--show-current",
        ],
        cwd=workspace,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        **build_hidden_windows_subprocess_kwargs(),
    )
    if result.returncode != 0:
        raise RuntimeError(f"현재 브랜치 확인 실패: {(result.stdout or '').strip()}")
    branch_name = (result.stdout or "").strip()
    return branch_name or None


def resolve_publish_branch_name(workspace: Path, suggested_branch_name: str, base_branch: str) -> str:
    current_branch = get_current_branch(workspace)
    if current_branch and current_branch != base_branch:
        return current_branch
    if suggested_branch_name and suggested_branch_name != base_branch:
        return suggested_branch_name
    raise RuntimeError(
        f"원격 반영 대상 브랜치를 결정할 수 없습니다. current={current_branch or '<detached>'}, base={base_branch}"
    )


def branch_has_publishable_commits(workspace: Path, branch_name: str, base_branch: str) -> bool:
    target_ref = resolve_branch_comparison_ref(branch_name, workspace) or f"origin/{base_branch}"
    result = subprocess.run(
        [
            "git",
            "-c",
            f"safe.directory={workspace}",
            "-c",
            "core.autocrlf=false",
            "rev-list",
            "--count",
            f"{target_ref}..HEAD",
        ],
        cwd=workspace,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        **build_hidden_windows_subprocess_kwargs(),
    )
    if result.returncode != 0:
        raise RuntimeError(f"로컬 커밋 확인 실패: {(result.stdout or '').strip()}")
    return int((result.stdout or "0").strip() or "0") > 0


def resolve_branch_comparison_ref(branch_name: str, workspace: Path) -> str | None:
    remote_head = get_remote_branch_head(branch_name, workspace)
    if remote_head is None:
        return None

    remote_tracking_ref = f"refs/remotes/origin/{branch_name}"
    if git_ref_exists(workspace, remote_tracking_ref):
        return remote_tracking_ref

    fetch_result = subprocess.run(
        [
            "git",
            "-c",
            f"safe.directory={workspace}",
            "-c",
            "core.autocrlf=false",
            "fetch",
            "origin",
            f"{branch_name}:{remote_tracking_ref}",
        ],
        cwd=workspace,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        **build_hidden_windows_subprocess_kwargs(),
    )
    if fetch_result.returncode == 0 and git_ref_exists(workspace, remote_tracking_ref):
        return remote_tracking_ref

    if git_commit_exists(workspace, remote_head):
        return remote_head
    return None


def git_ref_exists(workspace: Path, ref_name: str) -> bool:
    result = subprocess.run(
        [
            "git",
            "-c",
            f"safe.directory={workspace}",
            "-c",
            "core.autocrlf=false",
            "rev-parse",
            "--verify",
            ref_name,
        ],
        cwd=workspace,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        **build_hidden_windows_subprocess_kwargs(),
    )
    return result.returncode == 0


def git_commit_exists(workspace: Path, ref_name: str) -> bool:
    result = subprocess.run(
        [
            "git",
            "-c",
            f"safe.directory={workspace}",
            "-c",
            "core.autocrlf=false",
            "cat-file",
            "-e",
            f"{ref_name}^{{commit}}",
        ],
        cwd=workspace,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        **build_hidden_windows_subprocess_kwargs(),
    )
    return result.returncode == 0


def has_unmerged_paths(workspace: Path) -> bool:
    result = subprocess.run(
        [
            "git",
            "-c",
            f"safe.directory={workspace}",
            "-c",
            "core.autocrlf=false",
            "diff",
            "--name-only",
            "--diff-filter=U",
        ],
        cwd=workspace,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        **build_hidden_windows_subprocess_kwargs(),
    )
    return bool((result.stdout or "").strip())


def remote_branch_exists(branch_name: str, workspace: Path) -> bool:
    return get_remote_branch_head(branch_name, workspace) is not None


def get_remote_branch_head(branch_name: str, workspace: Path) -> str | None:
    result = subprocess.run(
        [
            "git",
            "-c",
            f"safe.directory={workspace}",
            "-c",
            "core.autocrlf=false",
            "ls-remote",
            "--exit-code",
            "--heads",
            "origin",
            branch_name,
        ],
        cwd=workspace,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        **build_hidden_windows_subprocess_kwargs(),
    )
    if result.returncode != 0:
        return None

    line = (result.stdout or "").strip().splitlines()
    if not line:
        return None

    sha = line[0].split()[0].strip()
    return sha or None


def get_staged_files(workspace: Path) -> list[str]:
    result = subprocess.run(
        [
            "git",
            "-c",
            f"safe.directory={workspace}",
            "-c",
            "core.autocrlf=false",
            "diff",
            "--cached",
            "--name-only",
        ],
        cwd=workspace,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        **build_hidden_windows_subprocess_kwargs(),
    )

    if result.returncode != 0:
        raise RuntimeError(f"Git staged file list failed: {result.stdout}")

    return [line for line in result.stdout.splitlines() if line.strip()]


def get_workspace_changed_files(workspace: Path) -> list[str]:
    result = subprocess.run(
        [
            "git",
            "-c",
            f"safe.directory={workspace}",
            "-c",
            "core.autocrlf=false",
            "status",
            "--porcelain",
        ],
        cwd=workspace,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        **build_hidden_windows_subprocess_kwargs(),
    )

    if result.returncode != 0:
        raise RuntimeError(f"Git changed file list failed: {result.stdout}")

    paths: list[str] = []
    for line in result.stdout.splitlines():
        entry = line[3:].strip()
        if not entry:
            continue
        if " -> " in entry:
            entry = entry.split(" -> ", 1)[1].strip()
        if entry not in paths:
            paths.append(entry)
    config = load_config(workspace)
    return filter_output_artifact_paths(paths, config.output_dir)


def get_branch_changed_files(workspace: Path, base_branch: str) -> list[str]:
    changed_files = get_raw_branch_changed_files(workspace, base_branch)
    config = load_config(workspace)
    return filter_output_artifact_paths(changed_files, config.output_dir)


def get_raw_branch_changed_files(workspace: Path, base_branch: str) -> list[str]:
    result = subprocess.run(
        [
            "git",
            "-c",
            f"safe.directory={workspace}",
            "-c",
            "core.autocrlf=false",
            "diff",
            "--name-only",
            f"origin/{base_branch}...HEAD",
        ],
        cwd=workspace,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        **build_hidden_windows_subprocess_kwargs(),
    )
    if result.returncode != 0:
        raise RuntimeError(f"브랜치 변경 파일 확인 실패: {(result.stdout or '').strip()}")
    return [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]


def unstage_output_artifacts(workspace: Path, output_dir: str) -> None:
    result = subprocess.run(
        [
            "git",
            "-c",
            f"safe.directory={workspace}",
            "-c",
            "core.autocrlf=false",
            "reset",
            "--quiet",
            "HEAD",
            "--",
            output_dir,
            ".issue-to-pr-bot",
            ".runtime-output",
            "Microsoft/Windows/PowerShell/ModuleAnalysisCache",
        ],
        cwd=workspace,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        **build_hidden_windows_subprocess_kwargs(),
    )
    if result.returncode != 0 and "did not match any file" not in (result.stdout or "").lower():
        raise RuntimeError(f"출력 산출물 unstage 실패: {(result.stdout or '').strip()}")


def filter_output_artifact_paths(paths: list[str], output_dir: str) -> list[str]:
    normalized_output_dir = normalize_output_dir(output_dir)
    return [path for path in paths if not is_non_publishable_workspace_path(path, normalized_output_dir)]


def get_non_publishable_workspace_paths(paths: list[str], output_dir: str) -> list[str]:
    normalized_output_dir = normalize_output_dir(output_dir)
    return [path for path in paths if is_non_publishable_workspace_path(path, normalized_output_dir)]


def ensure_no_non_publishable_workspace_changes(paths: list[str], output_dir: str) -> None:
    blocked = get_non_publishable_workspace_paths(paths, output_dir)
    if not blocked:
        return
    blocked_text = "\n".join(f"- {path}" for path in blocked)
    raise RuntimeError(
        f"{NON_PUBLISHABLE_WORKSPACE_CHANGES_ERROR_PREFIX}\n"
        "Remove these workspace-only files from the publishable commit history before the wrapper pushes:\n"
        f"{blocked_text}"
    )


def is_output_artifact_path(path: str, output_dir: str) -> bool:
    return is_non_publishable_workspace_path(path, output_dir)


def normalize_output_dir(output_dir: str) -> str:
    return output_dir.replace("\\", "/").strip("/")


def ensure_no_protected_changes(changed_files: list[str], config: BotConfig) -> None:
    blocked = [
        path
        for path in changed_files
        if any(matches_protected_path(path, pattern) for pattern in config.protected_paths)
    ]
    if not blocked:
        return

    blocked_text = ", ".join(blocked)
    raise RuntimeError(f"보호 경로가 변경되어 PR 생성을 중단합니다. {blocked_text}")


def matches_protected_path(path: str, pattern: str) -> bool:
    normalized_path = path.replace("\\", "/")
    normalized_pattern = pattern.replace("\\", "/")
    return fnmatch(normalized_path, normalized_pattern)


def push_branch(repository: str, branch_name: str, token: str, workspace: Path) -> None:
    """원격 브랜치의 현재 SHA를 기준으로 안전한 force-with-lease push를 수행한다."""
    push_url = f"https://x-access-token:{token}@github.com/{repository}.git"
    remote_head = get_remote_branch_head(branch_name, workspace)
    push_args = ["push"]
    if remote_head:
        push_args.append(f"--force-with-lease=refs/heads/{branch_name}:{remote_head}")
    else:
        push_args.append("--force-with-lease")
    push_args.extend([push_url, f"HEAD:{branch_name}"])
    try:
        run_git(push_args, workspace, mask=token)
    except RuntimeError as error:
        raise RuntimeError(
            "브랜치 push에 실패했습니다. 현재 구조에서는 GitHub Actions 권한이 아니라 "
            "GitHub App 설치 상태, 저장소 접근 범위, Contents 쓰기 권한, 또는 "
            "원격 브랜치 갱신으로 인한 force-with-lease 충돌을 확인해야 합니다.\n"
            f"{error}"
        ) from error


def ensure_pull_request(
    repository: str,
    branch_name: str,
    base_branch: str,
    request: IssueRequest,
    token: str,
    config: BotConfig,
    workspace: Path,
    changed_files: list[str],
    verification_commands: list[str],
) -> str:
    existing_url = find_existing_pull_request(repository, branch_name, base_branch, token)
    if existing_url:
        print(f"기존 PR 사용: {existing_url}")
        if request.is_pull_request:
            body_draft = load_pull_request_body_draft(request, workspace, config)
            if body_draft:
                github_request(
                    "PATCH",
                    f"/repos/{repository}/pulls/{parse_pull_request_number(existing_url)}",
                    token,
                    {"body": finalize_pull_request_body(body_draft, request)},
                )
            return existing_url
        body = build_pull_request_body(request, config, workspace, changed_files, verification_commands)
        title = load_pull_request_title_draft(request, workspace, config) or build_pull_request_title(request, config)
        existing_number = parse_pull_request_number(existing_url)
        if existing_number:
            github_request(
                "PATCH",
                f"/repos/{repository}/pulls/{existing_number}",
                token,
                {"title": title, "body": body},
            )
        return existing_url

    body = build_pull_request_body(request, config, workspace, changed_files, verification_commands)
    title = load_pull_request_title_draft(request, workspace, config) or build_pull_request_title(request, config)
    owner = repository.split("/", 1)[0]
    print(f"PR 제목 preview: {truncate_log_text(title, 200)}")
    print(f"PR 본문 preview:\n{truncate_log_text(body, 600)}")
    payload = {
        "title": title,
        "head": f"{owner}:{branch_name}",
        "base": base_branch,
        "body": body,
        "maintainer_can_modify": True,
    }
    response = github_request("POST", f"/repos/{repository}/pulls", token, payload)
    return response["html_url"]

def truncate_log_text(text: str, limit: int) -> str:
    compact = text.strip()
    if len(compact) <= limit:
        return compact
    return compact[: limit - 20].rstrip() + "\n... (생략)"


def apply_issue_metadata_if_possible(
    repository: str,
    issue_number: int,
    request: IssueRequest,
    token: str,
    workspace: Path,
) -> None:
    if issue_number <= 0:
        print("유효한 이슈 번호가 없어 메타데이터 적용을 건너뜁니다.")
        return

    try:
        plan = infer_issue_metadata(workspace, request)
        apply_issue_metadata(repository, issue_number, token, plan)
    except Exception as error:
        print(f"이슈 메타데이터 적용을 건너뜁니다. {error}")


def apply_pull_request_metadata_if_possible(
    repository: str,
    pull_request_url: str | None,
    request: IssueRequest,
    token: str,
    workspace: Path,
    changed_files: list[str],
) -> None:
    pull_request_number = parse_pull_request_number(pull_request_url)
    if not pull_request_number:
        return

    try:
        plan = infer_pull_request_metadata(workspace, request, changed_files)
        apply_pull_request_metadata(repository, pull_request_number, token, plan)
    except Exception as error:
        print(f"PR 메타데이터 적용을 건너뜁니다. {error}")


def apply_issue_metadata(
    repository: str,
    issue_number: int,
    token: str,
    plan: MetadataPlan,
) -> None:
    if not any((plan.issue_labels, plan.assignees, plan.milestone_title)):
        return

    if plan.issue_labels:
        labels = resolve_existing_labels(repository, token, plan.issue_labels)
        if labels:
            github_request("POST", f"/repos/{repository}/issues/{issue_number}/labels", token, {"labels": labels})

    issue_payload: dict[str, object] = {}
    if plan.assignees:
        issue_payload["assignees"] = normalize_usernames(plan.assignees)
    milestone_number = resolve_milestone_number(repository, token, plan.milestone_title)
    if milestone_number is not None:
        issue_payload["milestone"] = milestone_number
    if issue_payload:
        github_request("PATCH", f"/repos/{repository}/issues/{issue_number}", token, issue_payload)


def apply_pull_request_metadata(
    repository: str,
    pull_request_number: int,
    token: str,
    plan: MetadataPlan,
) -> None:
    apply_issue_metadata(
        repository=repository,
        issue_number=pull_request_number,
        token=token,
        plan=MetadataPlan(
            issue_labels=plan.pr_labels,
            pr_labels=[],
            assignees=plan.assignees,
            reviewers=[],
            team_reviewers=[],
            milestone_title=plan.milestone_title,
        ),
    )

    reviewers = normalize_usernames(plan.reviewers)
    team_reviewers = normalize_teams(plan.team_reviewers)
    if not reviewers and not team_reviewers:
        return

    payload: dict[str, object] = {}
    if reviewers:
        payload["reviewers"] = reviewers
    if team_reviewers:
        payload["team_reviewers"] = team_reviewers
    github_request(
        "POST",
        f"/repos/{repository}/pulls/{pull_request_number}/requested_reviewers",
        token,
        payload,
    )


def get_pull_request(repository: str, pull_request_number: int, token: str):
    return github_request("GET", f"/repos/{repository}/pulls/{pull_request_number}", token)


def is_bot_pull_request(pull_request: dict) -> bool:
    body = pull_request.get("body") or ""
    return BOT_PR_MARKER in body


def is_auto_merge_requested(pull_request: dict) -> bool:
    body = pull_request.get("body") or ""
    return BOT_AUTO_MERGE_MARKER in body


def request_pull_request_merge(repository: str, pull_request_number: int, token: str) -> MergeRequestResult:
    pull_request = get_pull_request(repository, pull_request_number, token)
    if not is_bot_pull_request(pull_request):
        raise RuntimeError("봇이 만든 PR에서만 merge 요청을 등록할 수 있습니다.")

    body = pull_request.get("body") or ""
    if BOT_AUTO_MERGE_MARKER not in body:
        updated_body = body.rstrip()
        if updated_body:
            updated_body += "\n\n"
        updated_body += BOT_AUTO_MERGE_MARKER
        pull_request = github_request(
            "PATCH",
            f"/repos/{repository}/pulls/{pull_request_number}",
            token,
            {"body": updated_body},
        )

    merge_sha = try_auto_merge_pull_request(repository, pull_request_number, token)
    return MergeRequestResult(
        pull_request_url=pull_request.get("html_url"),
        requested=True,
        merged=bool(merge_sha),
        merge_sha=merge_sha,
    )


def try_requested_auto_merge_pull_request(repository: str, pull_request_number: int, token: str) -> str | None:
    pull_request = get_pull_request(repository, pull_request_number, token)
    if not is_auto_merge_requested(pull_request):
        print("auto-merge 요청이 등록되지 않아 merge를 건너뜁니다.")
        return None
    return try_auto_merge_pull_request(repository, pull_request_number, token)


def try_auto_merge_pull_request(repository: str, pull_request_number: int, token: str) -> str | None:
    pull_request = get_pull_request(repository, pull_request_number, token)
    if not is_bot_pull_request(pull_request):
        print("봇이 만든 PR이 아니어서 auto-merge를 건너뜁니다.")
        return None

    if pull_request.get("state") != "open":
        print("열린 PR이 아니어서 auto-merge를 건너뜁니다.")
        return None

    payload = {
        "merge_method": "squash",
    }
    try:
        response = github_request("PUT", f"/repos/{repository}/pulls/{pull_request_number}/merge", token, payload)
    except RuntimeError as error:
        message = str(error).lower()
        if any(keyword in message for keyword in ("review", "required", "merge", "405", "409")):
            print(f"아직 auto-merge 조건이 충족되지 않았습니다. {error}")
            return None
        raise

    return response.get("sha")


def find_existing_pull_request(
    repository: str,
    branch_name: str,
    base_branch: str,
    token: str,
) -> str | None:
    owner = repository.split("/", 1)[0]
    query = urllib.parse.urlencode(
        {
            "state": "open",
            "head": f"{owner}:{branch_name}",
            "base": base_branch,
        }
    )
    response = github_request("GET", f"/repos/{repository}/pulls?{query}", token)
    if not response:
        return None
    return response[0]["html_url"]


def build_pull_request_body(
    request: IssueRequest,
    config: BotConfig,
    workspace: Path,
    changed_files: list[str],
    verification_commands: list[str] | None = None,
) -> str:
    llm_body = load_pull_request_body_draft(request, workspace, config)
    if llm_body:
        return finalize_pull_request_body(llm_body, request)

    template = load_pull_request_template(workspace)
    llm_summary = load_pull_request_summary(request, workspace, config)
    has_summary_placeholder = bool(template and "{{LLM_PR_SUMMARY}}" in template)
    if not template:
        body = build_default_pull_request_body(request, config, changed_files, verification_commands, llm_summary)
        return finalize_pull_request_body(body, request)

    rendered = template
    replacements = {
        "{{ISSUE_NUMBER}}": str(request.issue_number),
        "{{ISSUE_TITLE}}": request.issue_title,
        "{{TRIGGER_COMMAND}}": request.comment_body.strip(),
        "{{BOT_MODE}}": config.mode,
        "{{CHANGED_FILES}}": format_pull_request_changed_files(changed_files),
        "{{VERIFICATION_COMMANDS}}": format_pull_request_verification_commands(config, verification_commands),
        "{{LLM_PR_SUMMARY}}": llm_summary,
    }
    for placeholder, value in replacements.items():
        rendered = rendered.replace(placeholder, value)

    rendered = inject_llm_summary_into_template(rendered, llm_summary, placeholder_already_used=has_summary_placeholder)
    return finalize_pull_request_body(rendered, request)


def load_pull_request_template(workspace: Path) -> str | None:
    for template_path in iter_pull_request_template_paths(workspace):
        if not template_path.exists() or not template_path.is_file():
            continue
        content = template_path.read_text(encoding="utf-8")
        if content.strip():
            return content
    return None


def iter_pull_request_template_paths(workspace: Path) -> list[Path]:
    candidates = [
        workspace / ".github" / "pull_request_template.md",
        workspace / ".github" / "PULL_REQUEST_TEMPLATE.md",
        workspace / "pull_request_template.md",
        workspace / "PULL_REQUEST_TEMPLATE.md",
    ]

    template_dir = workspace / ".github" / "PULL_REQUEST_TEMPLATE"
    if template_dir.exists() and template_dir.is_dir():
        candidates.extend(sorted(path for path in template_dir.glob("*.md") if path.is_file()))

    return candidates


def build_default_pull_request_body(
    request: IssueRequest,
    config: BotConfig,
    changed_files: list[str],
    verification_commands: list[str] | None = None,
    llm_summary: str = "",
) -> str:
    sections = [
        "## 요약",
        "",
        llm_summary.strip() or format_pull_request_changed_files(changed_files),
        "",
        "## 검증",
        "",
        format_pull_request_verification_commands(config, verification_commands),
        "",
        "## 이슈",
        "",
        f"Closes #{request.issue_number}",
        "",
        "## 참고",
        "",
        f"- 트리거 명령: `{request.comment_body.strip()}`",
        f"- 봇 모드: `{config.mode}`",
        "",
        BOT_PR_MARKER,
    ]
    return "\n".join(sections).strip()


def load_pull_request_body_draft(request: IssueRequest, _workspace: Path, _config: BotConfig) -> str:
    body_path = find_existing_output_artifact_path("pr-body.md", request, _workspace)
    if body_path is not None:
        return body_path.read_text(encoding="utf-8-sig").strip()
    return ""


def load_pull_request_summary(request: IssueRequest, _workspace: Path, _config: BotConfig) -> str:
    summary_path = find_existing_output_artifact_path("pr-summary.md", request, _workspace)
    if summary_path is not None:
        return summary_path.read_text(encoding="utf-8-sig").strip()
    return ""


def load_pull_request_title_draft(request: IssueRequest, _workspace: Path, _config: BotConfig) -> str:
    title_path = find_existing_output_artifact_path("pr-title.txt", request, _workspace)
    if title_path is not None:
        for line in title_path.read_text(encoding="utf-8-sig").splitlines():
            stripped = line.strip()
            if stripped:
                return stripped
    return ""


def inject_llm_summary_into_template(
    template: str,
    llm_summary: str,
    placeholder_already_used: bool = False,
) -> str:
    if not llm_summary.strip():
        return template
    if placeholder_already_used:
        return template

    patterns = [
        r"(##\s*변경 내용\s*\n)",
        r"(##\s*Summary\s*\n)",
        r"(##\s*요약\s*\n)",
    ]
    for pattern in patterns:
        updated = re.sub(pattern, r"\1\n" + llm_summary.strip() + "\n\n", template, count=1, flags=re.IGNORECASE)
        if updated != template:
            return updated
    return template.rstrip() + "\n\n## 변경 내용\n\n" + llm_summary.strip()


def finalize_pull_request_body(body: str, request: IssueRequest) -> str:
    rendered = body.strip()
    rendered = re.sub(r"Closes #(?=\s|$)", f"Closes #{request.issue_number}", rendered)
    if f"Closes #{request.issue_number}" not in rendered:
        rendered = rendered.rstrip() + f"\n\nCloses #{request.issue_number}"
    if BOT_PR_MARKER not in rendered:
        rendered = rendered.rstrip() + "\n\n" + BOT_PR_MARKER
    return rendered.strip()


def format_pull_request_changed_files(changed_files: list[str]) -> str:
    if not changed_files:
        return "- No file changes were detected."
    return "\n".join(f"- `{path}`" for path in changed_files)


def format_pull_request_verification_commands(
    config: BotConfig,
    verification_commands: list[str] | None = None,
) -> str:
    commands = verification_commands if verification_commands is not None else get_check_commands(config)
    if not commands:
        return "- [ ] No verification commands selected for this change scope."
    return "\n".join(f"- [x] `{command}`" for command in commands)


def create_issue_comment(
    repository: str,
    issue_number: int,
    body: str,
    token: str | None = None,
) -> str | None:
    if issue_number <= 0:
        print("유효한 이슈/PR 번호가 없어 댓글 생성을 건너뜁니다.")
        return None

    token = token or os.getenv("BOT_GITHUB_TOKEN") or os.getenv("GITHUB_TOKEN")
    if not token:
        print("GitHub token is not available; skipping issue comment.")
        return None

    response = github_request(
        "POST",
        f"/repos/{repository}/issues/{issue_number}/comments",
        token,
        {"body": body},
    )
    return response["html_url"]


def parse_pull_request_number(pull_request_url: str | None) -> int | None:
    if not pull_request_url:
        return None
    match = re.search(r"/pull/(\d+)", pull_request_url)
    return int(match.group(1)) if match else None


def resolve_existing_labels(repository: str, token: str, requested_labels: list[str]) -> list[str]:
    available = github_request("GET", f"/repos/{repository}/labels?per_page=100", token)
    label_map = {normalize_label_name(item["name"]): item["name"] for item in available}
    resolved: list[str] = []
    for requested in requested_labels:
        direct = label_map.get(normalize_label_name(requested))
        if direct and direct not in resolved:
            resolved.append(direct)
            continue
        for candidate in expand_label_candidates(requested):
            matched = label_map.get(normalize_label_name(candidate))
            if matched and matched not in resolved:
                resolved.append(matched)
                break
    return resolved


def resolve_milestone_number(repository: str, token: str, requested_title: str | None) -> int | None:
    if not requested_title:
        return None

    milestones = github_request("GET", f"/repos/{repository}/milestones?state=open&per_page=100", token)
    target = normalize_label_name(requested_title)
    for milestone in milestones:
        if normalize_label_name(milestone["title"]) == target:
            return int(milestone["number"])

    for milestone in milestones:
        normalized_title = normalize_label_name(milestone["title"])
        if target in normalized_title or normalized_title in target:
            return int(milestone["number"])
    return None


def expand_label_candidates(requested: str) -> list[str]:
    normalized = normalize_label_name(requested)
    variants = [requested]
    label_aliases = {
        "bug": ["fix", "bugs", "버그"],
        "enhancement": ["feature", "features", "improvement", "개선", "기능"],
        "documentation": ["docs", "doc", "문서"],
        "refactor": ["cleanup", "리팩터링"],
        "tests": ["test", "qa", "테스트"],
        "automation": ["bot", "ci", "workflow", "자동화"],
        "dependencies": ["dependency", "deps", "의존성"],
        "frontend": ["front-end", "ui", "web", "프론트"],
        "backend": ["back-end", "api", "server", "백엔드"],
        "infra": ["infrastructure", "deploy", "ops", "config", "배포", "인프라"],
    }
    for canonical, aliases in label_aliases.items():
        names = [canonical, *aliases]
        if normalized in {normalize_label_name(name) for name in names}:
            variants.extend(names)
    return variants


def normalize_usernames(values: list[str]) -> list[str]:
    normalized: list[str] = []
    for value in values:
        candidate = value.strip()
        if candidate.startswith("@"):
            candidate = candidate[1:]
        if not candidate or "/" in candidate or candidate in normalized:
            continue
        normalized.append(candidate)
    return normalized


def normalize_teams(values: list[str]) -> list[str]:
    normalized: list[str] = []
    for value in values:
        candidate = value.strip()
        if candidate.startswith("@"):
            candidate = candidate[1:]
        if not candidate or "/" not in candidate or candidate in normalized:
            continue
        normalized.append(candidate)
    return normalized


def normalize_label_name(value: str) -> str:
    return re.sub(r"[\s_\-]+", "", value.strip().lower())


def github_request(method: str, path: str, token: str, payload: dict | None = None):
    body = None
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "User-Agent": "issue-to-pr-bot",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(
        f"https://api.github.com{path}",
        data=body,
        headers=headers,
        method=method,
    )

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            response_body = response.read().decode("utf-8")
            if not response_body:
                return None
            return json.loads(response_body)
    except urllib.error.HTTPError as error:
        error_body = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub API 요청 실패: {error.code} {error_body}") from error


def run_git(args: list[str], workspace: Path, mask: str | None = None) -> None:
    command = ["git", "-c", f"safe.directory={workspace}", "-c", "core.autocrlf=false", *args]
    result = subprocess.run(
        command,
        cwd=workspace,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        **build_hidden_windows_subprocess_kwargs(),
    )

    output = result.stdout
    if mask:
        output = output.replace(mask, "***")

    if output.strip():
        print(output.rstrip())

    if result.returncode != 0:
        display_command = "git " + " ".join(command[1:])
        if mask:
            display_command = display_command.replace(mask, "***")
        detail = output.strip()
        if not detail:
            detail = "(git output 없음)"
        raise RuntimeError(f"Git 명령 실패({result.returncode}): {display_command}\n{detail}")
