import re
from dataclasses import dataclass
from datetime import UTC, datetime


BOT_COMMAND = "/bot run"


@dataclass(frozen=True)
class IssueRequest:
    repository: str
    issue_number: int
    issue_title: str
    issue_body: str
    comment_body: str
    comment_author: str


def should_run_bot(comment_body: str) -> bool:
    return BOT_COMMAND in comment_body


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
    )


def build_branch_name(request: IssueRequest) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", request.issue_title.lower()).strip("-")
    if not slug:
        slug = "issue"
    return f"bot/issue-{request.issue_number}-{slug[:40]}"


def build_task_prompt(request: IssueRequest) -> str:
    created_at = datetime.now(UTC).isoformat(timespec="seconds")
    return "\n".join(
        [
            "You are working in the issue-to-pr-bot repository.",
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
            "Rules:",
            "- Create changes on a dedicated branch only.",
            "- Do not push directly to main.",
            "- Keep the change focused on the issue request.",
            "- Run available tests or checks before opening a PR.",
        ]
    )
