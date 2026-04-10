import json
import os
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from app.bot import IssueRequest, build_branch_name, build_task_prompt


@dataclass(frozen=True)
class PullRequestResult:
    branch_name: str
    pull_request_url: str | None
    created: bool


def create_test_pr(request: IssueRequest, workspace: Path) -> PullRequestResult:
    token = os.getenv("BOT_GITHUB_TOKEN") or os.getenv("GITHUB_TOKEN")
    if not token:
        raise RuntimeError("BOT_GITHUB_TOKEN 또는 GITHUB_TOKEN이 없어 PR을 생성할 수 없습니다.")

    repository = os.getenv("GITHUB_REPOSITORY") or request.repository
    base_branch = os.getenv("GITHUB_REF_NAME") or "main"
    branch_name = build_branch_name(request)

    write_marker_file(request, workspace)
    configure_git(workspace)

    run_git(["checkout", "-B", branch_name], workspace)
    run_git(["add", "bot-output"], workspace)

    if has_staged_changes(workspace):
        run_git(["commit", "-m", f"chore: issue #{request.issue_number} 작업 기록"], workspace)
    else:
        print("커밋할 변경사항이 없습니다.")

    push_branch(repository, branch_name, token, workspace)
    pr_url = ensure_pull_request(repository, branch_name, base_branch, request, token)

    return PullRequestResult(
        branch_name=branch_name,
        pull_request_url=pr_url,
        created=True,
    )


def write_marker_file(request: IssueRequest, workspace: Path) -> Path:
    output_dir = workspace / "bot-output"
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
                f"- Branch: {build_branch_name(request)}",
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
                build_task_prompt(request),
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
            "이 PR은 `/bot run` 테스트 파이프라인으로 자동 생성되었습니다.",
            "현재 단계에서는 Codex 실행 전 브랜치/커밋/PR 생성 흐름만 검증합니다.",
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
