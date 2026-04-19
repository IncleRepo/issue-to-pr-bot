"""브랜치, 커밋, 프롬프트 템플릿을 다루는 도우미."""

from __future__ import annotations

import re
from datetime import UTC, datetime

from app.config import BotConfig, get_check_commands
from app.domain.models import IssueRequest
from app.output_artifacts import (
    find_existing_output_artifact_path,
    get_commit_message_draft_path,
    get_pr_body_draft_path,
    get_pr_summary_draft_path,
    get_pr_title_draft_path,
)
from app.slot_inference import COMMIT_TYPE_SLOT_LEXICON, contains_any_term, score_slot_values


CONVENTIONAL_COMMIT_PREFIX_PATTERN = re.compile(
    r"^(feat|fix|refactor|docs|test|style|perf|build|ci|chore|revert)(?=[:(])",
    re.IGNORECASE,
)
CONVENTIONAL_COMMIT_SUBJECT_PATTERN = re.compile(
    r"^(feat|fix|refactor|docs|test|style|perf|build|ci|chore|revert)(?:\([^)]*\))?:\s*",
    re.IGNORECASE,
)
BRACKETED_LABEL_PATTERN = re.compile(r"^\[([^\]]+)\]\s*")
COMMIT_LABEL_ALIASES = {
    "feat": {"feature", "feat", "기능", "추가", "구현"},
    "fix": {"fix", "bug", "버그", "오류", "수정"},
    "docs": {"docs", "doc", "documentation", "문서"},
    "refactor": {"refactor", "cleanup", "리팩터링"},
    "style": {"style", "format", "formatting", "포맷", "포맷팅"},
    "test": {"test", "tests", "qa", "테스트"},
    "chore": {"chore", "maintenance", "설정", "정리"},
    "perf": {"perf", "performance", "성능"},
    "build": {"build", "release", "배포"},
    "ci": {"ci", "workflow", "pipeline"},
    "revert": {"revert", "rollback", "되돌림"},
}


def build_branch_name(request: IssueRequest, config: BotConfig | None = None) -> str:
    """저장소 템플릿 규칙을 반영해 브랜치 이름을 만든다."""

    config = config or BotConfig()
    rendered = render_request_template(config.branch_name_template, request, config)
    normalized = normalize_branch_name(rendered)
    if (
        normalized
        and not request.is_pull_request
        and request.comment_id
        and "{comment_suffix}" not in config.branch_name_template
        and "{comment_id}" not in config.branch_name_template
    ):
        normalized = f"{normalized}-comment-{request.comment_id}"
    return normalized or f"{config.branch_prefix}/issue-{request.issue_number}"


def build_pull_request_title(request: IssueRequest, config: BotConfig | None = None) -> str:
    """설정된 제목 템플릿으로 PR 제목을 만든다."""

    config = config or BotConfig()
    return render_request_template(config.pr_title_template, request, config)


def build_codex_commit_message(
    request: IssueRequest,
    config: BotConfig | None = None,
    changed_files: list[str] | None = None,
) -> str:
    """Codex가 만든 변경에 사용할 커밋 메시지를 구성한다."""

    config = config or BotConfig()
    commit_type = infer_commit_type(request, changed_files)
    template = normalize_commit_message_template(config.codex_commit_message_template, commit_type)
    summary = choose_commit_summary(request, commit_type)
    return render_request_template(
        template,
        request,
        config,
        extra_context={
            "commit_type": commit_type,
            "issue_title": summary,
            "commit_summary": summary,
        },
    )


def build_test_commit_message(request: IssueRequest, config: BotConfig | None = None) -> str:
    """test-pr 마커 커밋에 사용할 커밋 메시지를 만든다."""

    config = config or BotConfig()
    return render_request_template(config.test_commit_message_template, request, config)


def render_request_template(
    template: str,
    request: IssueRequest,
    config: BotConfig | None = None,
    extra_context: dict[str, object] | None = None,
) -> str:
    """요청 문맥을 넣어 저장소 템플릿을 렌더링한다."""

    config = config or BotConfig()
    context = build_request_template_context(request, config)
    if extra_context:
        context.update(extra_context)
    return template.format_map(DefaultTemplateMap(context))


def build_request_template_context(request: IssueRequest, config: BotConfig) -> dict[str, object]:
    """브랜치·커밋·PR 템플릿에서 공통으로 쓰는 플레이스홀더 값을 만든다."""

    slug = build_issue_slug(request.issue_title)
    comment_suffix = f"-comment-{request.comment_id}" if request.comment_id else ""
    commit_type = infer_commit_type(request)
    return {
        "branch_prefix": config.branch_prefix,
        "comment_author": request.comment_author,
        "comment_id": request.comment_id,
        "comment_suffix": comment_suffix,
        "issue_body": request.issue_body,
        "issue_number": request.issue_number,
        "issue_title": request.issue_title,
        "repository": request.repository,
        "slug": slug,
        "commit_type": commit_type,
    }


def build_issue_slug(issue_title: str) -> str:
    """이슈 제목에서 보수적인 slug 조각을 만든다."""

    slug = re.sub(r"[^a-z0-9]+", "-", issue_title.lower()).strip("-")
    return slug[:40] or "issue"


def normalize_branch_name(branch_name: str) -> str:
    """브랜치 이름을 Git에 안전한 형태로 정규화한다."""

    branch_name = re.sub(r"[^A-Za-z0-9._/-]+", "-", branch_name.strip())
    branch_name = re.sub(r"/{2,}", "/", branch_name)
    branch_name = re.sub(r"-{2,}", "-", branch_name)
    return branch_name.strip("/.-")


class DefaultTemplateMap(dict):
    """알 수 없는 플레이스홀더를 에러 대신 원문 그대로 남긴다."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def normalize_commit_message_template(template: str, commit_type: str) -> str:
    """템플릿에 {commit_type}이 없으면 고정 prefix를 현재 타입으로 바꾼다."""

    if "{commit_type}" in template:
        return template
    if CONVENTIONAL_COMMIT_PREFIX_PATTERN.match(template):
        return CONVENTIONAL_COMMIT_PREFIX_PATTERN.sub(commit_type, template, count=1)
    return template


def choose_commit_summary(request: IssueRequest, commit_type: str) -> str:
    draft = load_commit_message_draft(request)
    if not draft:
        return request.issue_title
    normalized = normalize_commit_summary_draft(draft, commit_type)
    return normalized or request.issue_title


def load_commit_message_draft(request: IssueRequest) -> str:
    draft_path = find_existing_output_artifact_path("commit-message.txt", request)
    if draft_path is None:
        return ""
    for line in draft_path.read_text(encoding="utf-8-sig").splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def normalize_commit_summary_draft(draft: str, commit_type: str) -> str:
    summary = " ".join(draft.strip().split())
    if not summary:
        return ""

    summary = CONVENTIONAL_COMMIT_SUBJECT_PATTERN.sub("", summary, count=1).strip()

    match = BRACKETED_LABEL_PATTERN.match(summary)
    if match and is_redundant_commit_label(match.group(1), commit_type):
        summary = summary[match.end() :].strip()

    return summary


def is_redundant_commit_label(label: str, commit_type: str) -> bool:
    normalized_label = re.sub(r"[^a-z0-9가-힣]+", "", label.lower())
    aliases = COMMIT_LABEL_ALIASES.get(commit_type, set())
    normalized_aliases = {re.sub(r"[^a-z0-9가-힣]+", "", alias.lower()) for alias in aliases}
    return normalized_label in normalized_aliases


def infer_commit_type(request: IssueRequest, changed_files: list[str] | None = None) -> str:
    """텍스트와 변경 파일을 보고 가장 알맞은 커밋 타입을 추론한다."""

    changed_files = changed_files or []
    normalized_files = [path.lower() for path in changed_files]
    text = " ".join(
        [
            request.issue_title.lower(),
            request.issue_body.lower(),
            request.comment_body.lower(),
        ]
    )
    scores = score_slot_values(text, COMMIT_TYPE_SLOT_LEXICON)

    if normalized_files and all(is_documentation_file(path) for path in normalized_files):
        return "docs"
    if normalized_files and all(is_test_file(path) for path in normalized_files):
        return "test"

    if scores.get("refactor", 0) >= 2:
        return "refactor"
    if scores.get("docs", 0) >= 2:
        return "docs"
    if scores.get("test", 0) >= 2:
        return "test"
    if scores.get("fix", 0) >= 2:
        return "fix"
    if scores.get("perf", 0) >= 2:
        return "perf"

    if normalized_files:
        if all(is_test_file(path) for path in normalized_files):
            return "test"
        if all(is_documentation_file(path) for path in normalized_files):
            return "docs"

    if contains_any_term(text, ("implement", "add", "feature", "기능", "구현", "추가")):
        return "feat"
    return "feat"


def is_documentation_file(path: str) -> bool:
    """문서 성격의 파일 경로인지 확인한다."""

    return path.endswith((".md", ".rst", ".txt")) or "/docs/" in path or path.startswith("docs/")


def is_test_file(path: str) -> bool:
    """테스트 코드로 보이는 파일 경로인지 확인한다."""

    return "/tests/" in path or path.startswith("tests/") or path.endswith(("_test.py", ".spec.ts", ".spec.js"))


def build_task_prompt(
    request: IssueRequest,
    config: BotConfig | None = None,
    repository_context: str | None = None,
    project_summary: str | None = None,
    code_context: str | None = None,
    available_secret_keys: list[str] | None = None,
    attachment_context: str | None = None,
) -> str:
    """Codex에 전달할 구현 프롬프트를 구성한다."""

    config = config or BotConfig()
    created_at = datetime.now(UTC).isoformat(timespec="seconds")
    pr_title_path = get_pr_title_draft_path(request)
    pr_body_path = get_pr_body_draft_path(request)
    if request.is_pull_request:
        pr_title_rule = "- For pull request follow-up requests, keep the existing PR title unchanged and do not write a new PR title draft unless the wrapper explicitly asks for one."
        pr_body_rule = (
            f"- For pull request follow-up requests, only write `{pr_body_path}` if the existing PR body should be updated to reflect the new changes.\n"
            "- If the current PR body can stay as-is, leave that file absent and the wrapper will preserve the existing body."
        )
    else:
        pr_title_rule = f"- Before exiting, write the final pull request title draft to `{pr_title_path}`."
        pr_body_rule = f"- Before exiting, write the final pull request body draft to `{pr_body_path}`."
    return "\n".join(
        [
            f"You are working in the {request.repository} repository.",
            "Implement the requested change from this GitHub issue.",
            "",
            f"Repository: {request.repository}",
            f"Issue: #{request.issue_number}",
            f"Title: {request.issue_title}",
            f"Author: {request.comment_author}",
            f"Created at: {created_at}",
            "",
            "Issue body:",
            request.issue_body.strip() or "(empty)",
            "",
            "Trigger comment:",
            request.comment_body.strip(),
            "",
            "Review context:",
            format_review_context(request),
            "",
            "Project structure:",
            project_summary or "No project structure summary was provided.",
            "",
            "Issue-relevant code context:",
            code_context or "No issue-relevant code files were selected.",
            "",
            "Repository context:",
            repository_context or "No repository guidance documents were provided.",
            "",
            "Issue/comment attachments:",
            attachment_context or "No supported issue or comment attachments were collected.",
            "",
            "Available secret environment variables (values hidden):",
            format_secret_keys(available_secret_keys or []),
            "",
            "Rules:",
            "- You may manage local Git state inside the workspace when needed (for example local branch switches, local commits, local rebase/merge, or local conflict resolution).",
            "- The wrapper does not run Codex with codex-sandbox for this task.",
            "- Keep your work centered inside the assigned workspace whenever practical.",
            "- If the implementation needs supporting infrastructure for development (for example a database, cache, queue, search engine, or local service dependency), prefer creating a workspace-local Docker or Docker Compose setup instead of relying on host-level installs.",
            "- Keep any Dockerfiles, compose files, seed scripts, and related environment bootstrap assets inside the assigned workspace.",
            "- Avoid polluting the host machine with permanent local service installs. If host port bindings are needed, keep them minimal and choose settings that are easy to change when conflicts exist.",
            "- If a development or verification step needs isolated infrastructure, create or update a workspace-local Docker or Docker Compose setup and continue there instead of requesting host-level setup.",
            "- Do not push directly to main or any protected base branch.",
            "- Do not push branches, open pull requests, merge pull requests, or post GitHub comments yourself.",
            "- The wrapper will supervise remote publish and merge steps after your local work is done.",
            "- Files under `.issue-to-pr-bot/input/` and `.issue-to-pr-bot/output/` are workspace-only scratch files.",
            "- Never include those workspace-only files in commits, publishable diffs, or pull request changes.",
            "- If you accidentally staged or committed those workspace-only files, remove them from the publishable commit history before exiting.",
            pr_title_rule,
            pr_body_rule,
            "- Create parent directories for those output files if they do not already exist.",
            "- If the repository has a PR template, follow its structure and fill it naturally instead of leaving question prompts behind.",
            "- Treat that file as the reviewer-facing PR description that the wrapper will submit on your behalf.",
            f"- If helpful, you may also write a concise fallback PR summary to `{get_pr_summary_draft_path(request)}`.",
            "- If this work should be published, you must create or amend at least one local commit yourself before exiting.",
            "- The exact number of commits and their message style should follow the repository guidance when it exists.",
            "- When implementation and verification are complete, stop and exit immediately instead of attempting any extra GitHub workflow steps.",
            "- Keep the change focused on the issue request.",
            "- Follow the repository guidance documents when they apply.",
            "- If the issue conflicts with repository guidance, prefer the repository guidance and explain the conflict.",
            "- Use available secret environment variables when needed, but never print or commit their values.",
        ]
    )


def build_plan_prompt(
    request: IssueRequest,
    config: BotConfig | None = None,
    repository_context: str | None = None,
    project_summary: str | None = None,
    code_context: str | None = None,
    available_secret_keys: list[str] | None = None,
    attachment_context: str | None = None,
) -> str:
    """코드 수정 없이 계획만 필요할 때 사용할 프롬프트를 구성한다."""

    config = config or BotConfig()
    created_at = datetime.now(UTC).isoformat(timespec="seconds")
    return "\n".join(
        [
            f"You are reviewing the {request.repository} repository.",
            "Create an implementation plan for this GitHub issue.",
            "Do not edit files, do not create commits, and do not open a pull request.",
            "",
            f"Repository: {request.repository}",
            f"Issue: #{request.issue_number}",
            f"Title: {request.issue_title}",
            f"Author: {request.comment_author}",
            f"Created at: {created_at}",
            "",
            "Issue body:",
            request.issue_body.strip() or "(empty)",
            "",
            "Trigger comment:",
            request.comment_body.strip(),
            "",
            "Review context:",
            format_review_context(request),
            "",
            "Project structure:",
            project_summary or "No project structure summary was provided.",
            "",
            "Issue-relevant code context:",
            code_context or "No issue-relevant code files were selected.",
            "",
            "Repository context:",
            repository_context or "No repository guidance documents were provided.",
            "",
            "Issue/comment attachments:",
            attachment_context or "No supported issue or comment attachments were collected.",
            "",
            "Available secret environment variables (values hidden):",
            format_secret_keys(available_secret_keys or []),
            "",
            "Configured verification commands:",
            format_check_commands(get_check_commands(config)),
            "",
            "Return a concise Korean plan with:",
            "- likely files to inspect or change",
            "- implementation steps",
            "- verification commands",
            "- blockers or missing context, if any",
        ]
    )


def format_check_commands(commands: list[str]) -> str:
    """프롬프트에 넣을 검증 명령 목록을 서식화한다."""

    if not commands:
        return "- No verification commands are configured."
    return "\n".join(f"- {command}" for command in commands)


def format_secret_keys(secret_keys: list[str]) -> str:
    """값을 노출하지 않고 secret key 이름만 프롬프트용으로 정리한다."""

    if not secret_keys:
        return "- No named secret environment variables were provided."
    return "\n".join(f"- {key}" for key in secret_keys)


def format_review_context(request: IssueRequest) -> str:
    """PR 리뷰 코멘트 문맥을 간결한 프롬프트 섹션으로 정리한다."""

    if not request.review_path:
        return "No pull request review comment context was provided."

    lines = [
        "- This request came from a pull request review comment.",
        f"- File: {request.review_path}",
    ]
    if request.review_line is not None:
        lines.append(f"- Line: {request.review_line}")
    if request.review_start_line is not None:
        lines.append(f"- Start line: {request.review_start_line}")
    if request.review_side:
        lines.append(f"- Side: {request.review_side}")
    if request.review_comment_url:
        lines.append(f"- Review comment URL: {request.review_comment_url}")
    if request.review_diff_hunk:
        lines.extend(
            [
                "- Diff hunk:",
                "```diff",
                request.review_diff_hunk.strip(),
                "```",
            ]
        )
    return "\n".join(lines)
