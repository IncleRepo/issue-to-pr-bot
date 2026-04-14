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
from app.config import BotConfig, get_check_commands, load_config
from app.domain.models import MetadataPlan
from app.metadata_rules import infer_issue_metadata, infer_pull_request_metadata

BOT_PR_MARKER = "<!-- incle-issue-to-pr-bot -->"
BOT_AUTO_MERGE_MARKER = "<!-- incle-issue-to-pr-bot:auto-merge -->"


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
    up_to_date: bool = False
    has_conflicts: bool = False


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
    branch_name = build_branch_name(request, config)
    configure_git(workspace)
    run_git(["checkout", "-B", branch_name], workspace)
    reset_worktree_if_requested(workspace)
    return CheckoutTarget(
        branch_name=branch_name,
        base_branch=os.getenv("GITHUB_REF_NAME") or "main",
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
    configure_git(workspace)
    run_git(["fetch", "origin", base_branch], workspace)
    result = subprocess.run(
        [
            "git",
            "-c",
            f"safe.directory={workspace}",
            "-c",
            "core.autocrlf=false",
            "merge",
            "--no-ff",
            "--no-commit",
            f"origin/{base_branch}",
        ],
        cwd=workspace,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if (result.stdout or "").strip():
        print((result.stdout or "").rstrip())
    output = (result.stdout or "").lower()

    if result.returncode == 0:
        return BaseSyncResult(
            attempted=True,
            up_to_date="already up to date" in output,
            has_conflicts=False,
        )

    if has_unmerged_paths(workspace):
        print("Base branch sync produced merge conflicts. Leaving the worktree in conflict state for Codex.")
        return BaseSyncResult(attempted=True, has_conflicts=True)

    raise RuntimeError(
        f"Base branch sync failed before Codex could continue: origin/{base_branch}"
    )


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
    commit_message: str,
    add_paths: list[str] | None = None,
    verification_commands: list[str] | None = None,
) -> PullRequestResult:
    token = os.getenv("BOT_GITHUB_TOKEN") or os.getenv("GITHUB_TOKEN")
    if not token:
        raise RuntimeError("BOT_GITHUB_TOKEN 또는 GITHUB_TOKEN이 없어 PR을 생성할 수 없습니다.")

    repository = os.getenv("GITHUB_REPOSITORY") or request.repository

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
            verification_commands=verification_commands or [],
        )

    changed_files = get_staged_files(workspace)
    ensure_no_protected_changes(changed_files, config)
    run_git(["commit", "-m", commit_message], workspace)
    push_branch(repository, branch_name, token, workspace)
    pr_url = ensure_pull_request(
        repository,
        branch_name,
        base_branch,
        request,
        token,
        config,
        workspace,
        changed_files,
        verification_commands or [],
    )
    apply_pull_request_metadata_if_possible(
        repository=repository,
        pull_request_url=pr_url,
        request=request,
        token=token,
        workspace=workspace,
        changed_files=changed_files,
    )

    return PullRequestResult(
        branch_name=branch_name,
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
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    return bool((result.stdout or "").strip())


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
    workspace: Path,
    changed_files: list[str],
    verification_commands: list[str],
) -> str:
    existing_url = find_existing_pull_request(repository, branch_name, base_branch, token)
    if existing_url:
        print(f"기존 PR 사용: {existing_url}")
        return existing_url

    owner = repository.split("/", 1)[0]
    body = build_pull_request_body(request, config, workspace, changed_files, verification_commands)
    payload = {
        "title": build_pull_request_title(request, config),
        "head": f"{owner}:{branch_name}",
        "base": base_branch,
        "body": body,
        "maintainer_can_modify": True,
    }
    response = github_request("POST", f"/repos/{repository}/pulls", token, payload)
    return response["html_url"]


def apply_issue_metadata_if_possible(
    repository: str,
    issue_number: int,
    request: IssueRequest,
    token: str,
    workspace: Path,
) -> None:
    try:
        plan = infer_issue_metadata(workspace, request)
        apply_issue_metadata(repository, issue_number, token, plan)
    except Exception as error:
        print(f"이슈 메타데이터 적용을 건너뜁니다: {error}")


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
        print(f"PR 메타데이터 적용을 건너뜁니다: {error}")


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
        raise RuntimeError("봇이 만든 PR에 대해서만 merge 요청을 등록할 수 있습니다.")

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
        print("봇이 만든 PR이 아니라서 auto-merge를 건너뜁니다.")
        return None

    if pull_request.get("state") != "open":
        print("열린 PR이 아니라서 auto-merge를 건너뜁니다.")
        return None

    payload = {
        "merge_method": "squash",
    }
    try:
        response = github_request("PUT", f"/repos/{repository}/pulls/{pull_request_number}/merge", token, payload)
    except RuntimeError as error:
        message = str(error).lower()
        if any(keyword in message for keyword in ("review", "required", "merge", "405", "409")):
            print(f"아직 auto-merge 조건이 충족되지 않았습니다: {error}")
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
    template = load_pull_request_template(workspace)
    if not template:
        return build_default_pull_request_body(request, config, changed_files, verification_commands)

    rendered = template
    replacements = {
        "{{ISSUE_NUMBER}}": str(request.issue_number),
        "{{ISSUE_TITLE}}": request.issue_title,
        "{{TRIGGER_COMMAND}}": request.comment_body.strip(),
        "{{BOT_MODE}}": config.mode,
        "{{CHANGED_FILES}}": format_pull_request_changed_files(changed_files),
        "{{VERIFICATION_COMMANDS}}": format_pull_request_verification_commands(config, verification_commands),
    }
    for placeholder, value in replacements.items():
        rendered = rendered.replace(placeholder, value)

    rendered = re.sub(r"Closes #(?=\s|$)", f"Closes #{request.issue_number}", rendered)
    if BOT_PR_MARKER not in rendered:
        rendered = rendered.rstrip() + "\n\n" + BOT_PR_MARKER
    return rendered.strip()


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
) -> str:
    return "\n".join(
        [
            "## 요약",
            "",
            format_pull_request_changed_files(changed_files),
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
            f"- 트리거 댓글: `{request.comment_body.strip()}`",
            f"- 봇 모드: `{config.mode}`",
            "",
            BOT_PR_MARKER,
        ]
    ).strip()


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
        "refactor": ["cleanup", "리팩토링"],
        "tests": ["test", "qa", "테스트"],
        "automation": ["bot", "ci", "workflow", "자동화"],
        "dependencies": ["dependency", "deps", "의존성"],
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
