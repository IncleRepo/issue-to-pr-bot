import re
from dataclasses import dataclass
from datetime import UTC, datetime

from app.config import BotConfig


@dataclass(frozen=True)
class IssueRequest:
    repository: str
    issue_number: int
    issue_title: str
    issue_body: str
    comment_body: str
    comment_author: str
    comment_id: int


def should_run_bot(comment_body: str, config: BotConfig | None = None) -> bool:
    config = config or BotConfig()
    return config.command in comment_body


def build_issue_request(payload: dict) -> IssueRequest:
    issue = payload.get("issue", {})
    repo = payload.get("repository", {})
    comment = payload.get("comment", {})

    return IssueRequest(
        repository=repo.get("full_name") or "unknown/unknown",
        issue_number=int(issue.get("number") or 0),
        issue_title=issue.get("title") or "",
        issue_body=issue.get("body") or "",
        comment_body=comment.get("body") or "",
        comment_author=(comment.get("user") or {}).get("login") or "unknown",
        comment_id=int(comment.get("id") or 0),
    )


def build_branch_name(request: IssueRequest, config: BotConfig | None = None) -> str:
    config = config or BotConfig()
    slug = re.sub(r"[^a-z0-9]+", "-", request.issue_title.lower()).strip("-")
    if not slug:
        slug = "issue"
    suffix = f"-comment-{request.comment_id}" if request.comment_id else ""
    return f"{config.branch_prefix}/issue-{request.issue_number}{suffix}-{slug[:40]}"


def build_task_prompt(
    request: IssueRequest,
    config: BotConfig | None = None,
    repository_context: str | None = None,
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
            "Repository context:",
            repository_context or "No repository guidance documents were provided.",
            "",
            "Rules:",
            "- Create changes on a dedicated branch only.",
            "- Do not push directly to main.",
            "- Keep the change focused on the issue request.",
            "- Follow the repository guidance documents when they apply.",
            "- If the issue conflicts with repository guidance, prefer the repository guidance and explain the conflict.",
            f"- Run this verification command before opening a PR: {config.test_command}",
        ]
    )
