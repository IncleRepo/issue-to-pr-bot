"""Branch, commit, and prompt template helpers."""

import re
from datetime import UTC, datetime

from app.config import BotConfig, get_check_commands
from app.domain.models import IssueRequest


def build_branch_name(request: IssueRequest, config: BotConfig | None = None) -> str:
    config = config or BotConfig()
    rendered = render_request_template(config.branch_name_template, request, config)
    normalized = normalize_branch_name(rendered)
    return normalized or f"{config.branch_prefix}/issue-{request.issue_number}"


def build_pull_request_title(request: IssueRequest, config: BotConfig | None = None) -> str:
    config = config or BotConfig()
    return render_request_template(config.pr_title_template, request, config)


def build_codex_commit_message(request: IssueRequest, config: BotConfig | None = None) -> str:
    config = config or BotConfig()
    return render_request_template(config.codex_commit_message_template, request, config)


def build_test_commit_message(request: IssueRequest, config: BotConfig | None = None) -> str:
    config = config or BotConfig()
    return render_request_template(config.test_commit_message_template, request, config)


def render_request_template(template: str, request: IssueRequest, config: BotConfig | None = None) -> str:
    config = config or BotConfig()
    context = build_request_template_context(request, config)
    return template.format_map(DefaultTemplateMap(context))


def build_request_template_context(request: IssueRequest, config: BotConfig) -> dict[str, object]:
    slug = build_issue_slug(request.issue_title)
    comment_suffix = f"-comment-{request.comment_id}" if request.comment_id else ""
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
    }


def build_issue_slug(issue_title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", issue_title.lower()).strip("-")
    return slug[:40] or "issue"


def normalize_branch_name(branch_name: str) -> str:
    branch_name = re.sub(r"[^A-Za-z0-9._/-]+", "-", branch_name.strip())
    branch_name = re.sub(r"/{2,}", "/", branch_name)
    branch_name = re.sub(r"-{2,}", "-", branch_name)
    return branch_name.strip("/.-")


class DefaultTemplateMap(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def build_task_prompt(
    request: IssueRequest,
    config: BotConfig | None = None,
    repository_context: str | None = None,
    project_summary: str | None = None,
    available_secret_keys: list[str] | None = None,
    attachment_context: str | None = None,
) -> str:
    config = config or BotConfig()
    created_at = datetime.now(UTC).isoformat(timespec="seconds")
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
            "- Create changes on a dedicated branch only.",
            "- Do not push directly to main.",
            "- Keep the change focused on the issue request.",
            "- Follow the repository guidance documents when they apply.",
            "- If the issue conflicts with repository guidance, prefer the repository guidance and explain the conflict.",
            "- Use available secret environment variables when needed, but never print or commit their values.",
            "- Run all verification commands before opening a PR:",
            format_check_commands(get_check_commands(config)),
        ]
    )


def build_plan_prompt(
    request: IssueRequest,
    config: BotConfig | None = None,
    repository_context: str | None = None,
    project_summary: str | None = None,
    available_secret_keys: list[str] | None = None,
    attachment_context: str | None = None,
) -> str:
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
    if not commands:
        return "- No verification commands are configured."
    return "\n".join(f"- {command}" for command in commands)


def format_secret_keys(secret_keys: list[str]) -> str:
    if not secret_keys:
        return "- No named secret environment variables were provided."
    return "\n".join(f"- {key}" for key in secret_keys)


def format_review_context(request: IssueRequest) -> str:
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
