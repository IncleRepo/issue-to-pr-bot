import re
from dataclasses import dataclass
from datetime import UTC, datetime

from app.config import BotConfig, get_check_commands


@dataclass(frozen=True)
class IssueRequest:
    repository: str
    issue_number: int
    issue_title: str
    issue_body: str
    comment_body: str
    comment_author: str
    comment_id: int


@dataclass(frozen=True)
class BotCommand:
    action: str
    trigger: str
    instruction: str
    options: dict[str, str]


def should_run_bot(comment_body: str, config: BotConfig | None = None) -> bool:
    config = config or BotConfig()
    return parse_bot_command(comment_body, config) is not None


def parse_bot_command(comment_body: str, config: BotConfig | None = None) -> BotCommand | None:
    config = config or BotConfig()

    command_match = find_literal_command(comment_body, config.help_command)
    if command_match:
        instruction = comment_body[command_match.end() :].strip()
        return BotCommand("help", config.help_command, instruction, parse_options(instruction))

    command_match = find_literal_command(comment_body, config.status_command)
    if command_match:
        instruction = comment_body[command_match.end() :].strip()
        return BotCommand("status", config.status_command, instruction, parse_options(instruction))

    command_match = find_literal_command(comment_body, config.plan_command)
    if command_match:
        instruction = comment_body[command_match.end() :].strip()
        return BotCommand("plan", config.plan_command, instruction, parse_options(instruction))

    command_match = find_literal_command(comment_body, config.command)
    if command_match:
        instruction = comment_body[command_match.end() :].strip()
        return BotCommand("run", config.command, instruction, parse_options(instruction))

    mention_match = find_mention(comment_body, config)
    if not mention_match:
        return None

    instruction = comment_body[mention_match.end() :].strip()
    action = "run"
    lowered = instruction.lower()
    if lowered.startswith("help"):
        action = "help"
        instruction = instruction[4:].strip(" \t:,-")
    elif lowered.startswith("status"):
        action = "status"
        instruction = instruction[6:].strip(" \t:,-")
    elif lowered.startswith("plan"):
        action = "plan"
        instruction = instruction[4:].strip(" \t:,-")
    elif lowered.startswith("run"):
        instruction = instruction[3:].strip(" \t:,-")

    return BotCommand(action, config.mention, instruction, parse_options(instruction))


def should_run_for_mention(comment_body: str, config: BotConfig | None = None) -> bool:
    config = config or BotConfig()
    return find_mention(comment_body, config) is not None


def find_literal_command(comment_body: str, command: str) -> re.Match[str] | None:
    command = command.strip()
    if not command:
        return None
    return re.search(rf"(^|\s){re.escape(command)}(\s|$)", comment_body, re.IGNORECASE)


def find_mention(comment_body: str, config: BotConfig) -> re.Match[str] | None:
    mention = config.mention.strip()
    if not mention:
        return None
    return re.search(rf"(^|\s){re.escape(mention)}(\s|$|[,.!?])", comment_body, re.IGNORECASE)


def parse_options(instruction: str) -> dict[str, str]:
    options: dict[str, str] = {}
    for token in instruction.split():
        if "=" not in token:
            continue
        key, value = token.split("=", 1)
        key = key.strip().lower()
        value = value.strip().strip(",")
        if key and value:
            options[key] = value
    return options


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
    project_summary: str | None = None,
    available_secret_keys: list[str] | None = None,
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
            "Project structure:",
            project_summary or "No project structure summary was provided.",
            "",
            "Repository context:",
            repository_context or "No repository guidance documents were provided.",
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
            "Project structure:",
            project_summary or "No project structure summary was provided.",
            "",
            "Repository context:",
            repository_context or "No repository guidance documents were provided.",
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
