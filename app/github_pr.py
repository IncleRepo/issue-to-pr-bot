import json
import os
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path

from app.bot import IssueRequest, build_branch_name, build_task_prompt
from app.config import BotConfig, load_config


@dataclass(frozen=True)
class PullRequestResult:
    branch_name: str
    pull_request_url: str | None
    created: bool
    changed_files: list[str]


def create_test_pr(request: IssueRequest, workspace: Path) -> PullRequestResult:
    config = load_config(workspace)
    branch_name = checkout_bot_branch(request, workspace, config)

    write_marker_file(request, workspace, config)
    return commit_push_and_open_pr(
        request=request,
        workspace=workspace,
        config=config,
        branch_name=branch_name,
        commit_message=f"chore: issue #{request.issue_number} 작업 기록",
        add_paths=[config.output_dir],
    )


def checkout_bot_branch(request: IssueRequest, workspace: Path, config: BotConfig) -> str:
    branch_name = build_branch_name(request, config)
    configure_git(workspace)
    run_git(["checkout", "-B", branch_name], workspace)
    reset_worktree_if_requested(workspace)
    return branch_name


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
    commit_message: str,
    add_paths: list[str] | None = None,
) -> PullRequestResult:
    token = os.getenv("BOT_GITHUB_TOKEN") or os.getenv("GITHUB_TOKEN")
    if not token:
        raise RuntimeError("BOT_GITHUB_TOKEN 또는 GITHUB_TOKEN이 없어 PR을 생성할 수 없습니다.")

    repository = os.getenv("GITHUB_REPOSITORY") or request.repository
    base_branch = os.getenv("GITHUB_REF_NAME") or "main"

    print("변경 파일 확인:")
    run_git(["status", "--short"], workspace)

    for path in add_paths or ["--all"]:
        run_git(["add", path], workspace)

    if not has_staged_changes(workspace):
        print("커밋할 변경사항이 없어 PR 생성을 건너뜁니다.")
        return PullRequestResult(
            branch_name=branch_name,
            pull_request_url=None,
            created=False,
            changed_files=[],
        )

    changed_files = get_staged_files(workspace)
    ensure_no_protected_changes(changed_files, config)
    run_git(["commit", "-m", commit_message], workspace)
    push_branch(repository, branch_name, token, workspace)
    pr_url = ensure_pull_request(repository, branch_name, base_branch, request, token, config)

    return PullRequestResult(
        branch_name=branch_name,
        pull_request_url=pr_url,
        created=True,
        changed_files=changed_files,
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
    run_git(["config", "user.name", "issue-to-pr-bot"], workspace)
    run_git(["config", "user.email", "issue-to-pr-bot@users.noreply.github.com"], workspace)


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
    )
    return result.returncode != 0


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
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )

    if result.returncode != 0:
        raise RuntimeError(f"Git staged file list failed: {result.stdout}")

    return [line for line in result.stdout.splitlines() if line.strip()]


def ensure_no_protected_changes(changed_files: list[str], config: BotConfig) -> None:
    blocked = [
        path
        for path in changed_files
        if any(matches_protected_path(path, pattern) for pattern in config.protected_paths)
    ]
    if not blocked:
        return

    blocked_text = ", ".join(blocked)
    raise RuntimeError(f"보호 경로가 변경되어 PR 생성을 중단합니다: {blocked_text}")


def matches_protected_path(path: str, pattern: str) -> bool:
    normalized_path = path.replace("\\", "/")
    normalized_pattern = pattern.replace("\\", "/")
    return fnmatch(normalized_path, normalized_pattern)


def push_branch(repository: str, branch_name: str, token: str, workspace: Path) -> None:
    push_url = f"https://x-access-token:{token}@github.com/{repository}.git"
    try:
        run_git(["push", "--force-with-lease", push_url, f"HEAD:{branch_name}"], workspace, mask=token)
    except RuntimeError as error:
        raise RuntimeError(
            "브랜치 push에 실패했습니다. GitHub Actions의 Workflow permissions가 "
            "'Read and write permissions'인지 확인하거나, 쓰기 권한이 있는 PAT를 "
            "BOT_GITHUB_TOKEN secret으로 설정해야 합니다."
        ) from error


def ensure_pull_request(
    repository: str,
    branch_name: str,
    base_branch: str,
    request: IssueRequest,
    token: str,
    config: BotConfig,
) -> str:
    existing_url = find_existing_pull_request(repository, branch_name, base_branch, token)
    if existing_url:
        print(f"기존 PR 사용: {existing_url}")
        return existing_url

    owner = repository.split("/", 1)[0]
    body = "\n".join(
        [
            f"Closes #{request.issue_number}",
            "",
            f"이 PR은 `{config.command}` 댓글로 자동 생성되었습니다.",
            f"봇 모드: `{config.mode}`",
        ]
    )
    payload = {
        "title": f"[bot] Issue #{request.issue_number}: {request.issue_title}",
        "head": f"{owner}:{branch_name}",
        "base": base_branch,
        "body": body,
        "maintainer_can_modify": True,
    }
    response = github_request("POST", f"/repos/{repository}/pulls", token, payload)
    return response["html_url"]


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


def create_issue_comment(
    repository: str,
    issue_number: int,
    body: str,
    token: str | None = None,
) -> str | None:
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
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
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
        raise RuntimeError(f"Git 명령 실패({result.returncode}): {display_command}")
