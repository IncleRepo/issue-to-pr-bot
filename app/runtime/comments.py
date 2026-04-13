"""GitHub-facing execution reports and small formatting helpers."""

import os
import sys
from dataclasses import dataclass

from app.attachments import AttachmentContext
from app.config import BOT_MENTION, BotConfig, get_check_commands
from app.domain.models import BotCommand, BotRuntimeOptions, IssueRequest
from app.github_pr import MergeRequestResult, PullRequestResult, create_issue_comment
from app.repo_context import MissingContextError
from app.runtime_secrets import MissingSecretError
from app.verification import VerificationError


@dataclass(frozen=True)
class BotStatusSnapshot:
    available_secret_keys: list[str]
    missing_secret_keys: list[str]
    context_document_count: int
    missing_context_paths: list[str]
    external_context_root: str | None
    secrets_file_path: str
    secrets_file_exists: bool


def post_help_comment(request: IssueRequest, config: BotConfig) -> None:
    body = "\n".join(
        [
            "## Usage",
            "",
            f"- Mention `{BOT_MENTION}` and write the request in natural language.",
            "- `plan`, `계획`, `설계만` => plan only",
            "- `status`, `state`, `상태` => status snapshot",
            "- `help`, `usage`, `사용법` => this help message",
            "- `merge`, `머지`, `승인되면 머지` => register auto-merge intent on a PR",
            "",
            "Examples:",
            f"- `{BOT_MENTION} README 로컬 실행 방법 추가해줘`",
            f"- `{BOT_MENTION} 이 리뷰 반영해줘. main 반영하고 충돌 해결해줘`",
            f"- `{BOT_MENTION} 승인되면 머지해줘`",
            "",
            format_run_url(),
        ]
    ).strip()
    del config
    safe_create_issue_comment(request, body)


def post_status_comment(request: IssueRequest, config: BotConfig, snapshot: BotStatusSnapshot) -> None:
    body = "\n".join(
        [
            "## Bot Status",
            "",
            "### Defaults",
            f"- mention: `{BOT_MENTION}`",
            f"- mode: `{config.mode}`",
            f"- provider: `{config.provider}`",
            f"- branch prefix: `{config.branch_prefix}`",
            "",
            "### Verification",
            format_markdown_list(get_check_commands(config), code=True),
            "",
            "### Context",
            f"- repository context paths: `{len(config.context_paths)}`",
            f"- external context paths: `{len(config.external_context_paths)}`",
            f"- loaded context documents: `{snapshot.context_document_count}`",
            f"- external context root: `{snapshot.external_context_root or 'not mounted'}`",
            "",
            "### Secrets",
            f"- secret env file: `{snapshot.secrets_file_path}`",
            f"- secret env file exists: `{'yes' if snapshot.secrets_file_exists else 'no'}`",
            f"- available secret keys: {format_secret_keys_for_log(snapshot.available_secret_keys)}",
            "",
            "### Missing",
            format_missing_status(snapshot),
            "",
            format_run_url(),
        ]
    ).strip()
    safe_create_issue_comment(request, body)


def post_merge_request_comment(
    request: IssueRequest,
    command: BotCommand,
    runtime_options: BotRuntimeOptions,
    merge_result: MergeRequestResult,
) -> None:
    status = "merged" if merge_result.merged else "pending"
    body = "\n".join(
        [
            "## Execution Result",
            "",
            "### Summary",
            f"- status: `{status}`",
            f"- action: `{command.action}`",
            f"- mode: `{runtime_options.mode}`",
            f"- provider: `{runtime_options.provider}`",
            "- merge requested: `yes`",
            f"- PR: {merge_result.pull_request_url}",
            (
                f"- merge commit: `{merge_result.merge_sha}`"
                if merge_result.merge_sha
                else "- merge status: waiting for GitHub branch protection requirements"
            ),
            "",
            format_run_url(),
        ]
    ).strip()
    safe_create_issue_comment(request, body)


def post_plan_comment(
    request: IssueRequest,
    config: BotConfig,
    command: BotCommand,
    runtime_options: BotRuntimeOptions,
    attachment_info: AttachmentContext,
    plan_output: str,
) -> None:
    del config
    body = "\n".join(
        [
            "## Execution Result",
            "",
            "### Summary",
            "- status: `planned`",
            f"- action: `{command.action}`",
            f"- mode: `{runtime_options.mode}`",
            f"- provider: `{runtime_options.provider}`",
            f"- verify: `{'on' if runtime_options.verify else 'off'}`",
            f"- attachments: {format_attachment_summary(attachment_info)}",
            "",
            "### Plan",
            trim_codex_output(plan_output),
            "",
            format_run_url(),
        ]
    ).strip()
    safe_create_issue_comment(request, body)


def post_success_comment(
    request: IssueRequest,
    config: BotConfig,
    command: BotCommand,
    runtime_options: BotRuntimeOptions,
    attachment_info: AttachmentContext,
    result: PullRequestResult,
    merge_result: MergeRequestResult | None = None,
) -> None:
    body = "\n".join(
        [
            "## Execution Result",
            "",
            "### Summary",
            "- status: `success`",
            f"- action: `{command.action}`",
            f"- mode: `{runtime_options.mode}`",
            f"- provider: `{runtime_options.provider}`",
            f"- verify: `{'on' if runtime_options.verify else 'off'}`",
            f"- effort: `{runtime_options.effort or 'default'}`",
            f"- branch: `{result.branch_name}`",
            f"- PR: {result.pull_request_url}",
            f"- verification: {format_verification_status(config, runtime_options)}",
            f"- changed files: `{len(result.changed_files)}`",
            f"- attachments: {format_attachment_summary(attachment_info)}",
            f"- merge request: `{format_merge_request_status(merge_result)}`",
            "",
            "### Changed Files",
            format_changed_files(result.changed_files),
            "",
            format_run_url(),
        ]
    ).strip()
    safe_create_issue_comment(request, body)


def post_no_changes_comment(
    request: IssueRequest,
    config: BotConfig,
    command: BotCommand,
    runtime_options: BotRuntimeOptions,
    attachment_info: AttachmentContext,
    result: PullRequestResult,
) -> None:
    del config
    body = "\n".join(
        [
            "## Execution Result",
            "",
            "### Summary",
            "- status: `no_changes`",
            f"- action: `{command.action}`",
            f"- mode: `{runtime_options.mode}`",
            f"- provider: `{runtime_options.provider}`",
            f"- verify: `{'on' if runtime_options.verify else 'off'}`",
            f"- branch: `{result.branch_name}`",
            f"- attachments: {format_attachment_summary(attachment_info)}",
            "- reason: no staged changes were produced",
            "",
            format_run_url(),
        ]
    ).strip()
    safe_create_issue_comment(request, body)


def post_failure_comment(
    request: IssueRequest,
    config: BotConfig,
    error: Exception,
    command: BotCommand | None = None,
) -> None:
    body = "\n".join(
        [
            "## Execution Result",
            "",
            "### Summary",
            "- status: `failed`",
            f"- default mode: `{config.mode}`",
            f"- failure stage: `{classify_failure_stage(error)}`",
            f"- action: `{command.action if command else 'unknown'}`",
            f"- error: `{type(error).__name__}: {error}`",
            "",
            "### Detail",
            format_failure_detail(error),
            "",
            "### Next Steps",
            format_failure_next_steps(request, config, command, error),
            "",
            format_run_url(),
        ]
    ).strip()
    safe_create_issue_comment(request, body)


def format_changed_files(changed_files: list[str]) -> str:
    if not changed_files:
        return "- none"

    displayed = changed_files[:20]
    lines = [f"- `{path}`" for path in displayed]
    if len(changed_files) > len(displayed):
        lines.append(f"- and `{len(changed_files) - len(displayed)}` more")
    return "\n".join(lines)


def format_runtime_options(runtime_options: BotRuntimeOptions) -> str:
    parts = [
        f"mode=`{runtime_options.mode}`",
        f"provider=`{runtime_options.provider}`",
        f"verify=`{'on' if runtime_options.verify else 'off'}`",
    ]
    if runtime_options.effort:
        parts.append(f"effort=`{runtime_options.effort}`")
    if runtime_options.sync_base:
        parts.append("sync_base=`on`")
    if runtime_options.request_merge:
        parts.append("request_merge=`on`")
    return ", ".join(parts)


def format_verification_status(config: BotConfig, runtime_options: BotRuntimeOptions) -> str:
    if not runtime_options.verify:
        return "`skipped (verify=false)`"
    return format_check_commands(config)


def format_merge_request_status(merge_result: MergeRequestResult | None) -> str:
    if merge_result is None:
        return "not requested"
    if merge_result.merged:
        return f"merged ({merge_result.merge_sha})"
    return "requested"


def format_attachment_summary(context: AttachmentContext) -> str:
    return f"`{len(context.attachments)}` loaded, `{len(context.skipped)}` skipped"


def format_secret_keys_for_log(secret_keys: list[str]) -> str:
    if not secret_keys:
        return "`none`"
    return ", ".join(f"`{key}`" for key in secret_keys)


def format_markdown_list(items: list[str], code: bool = False) -> str:
    if not items:
        return "- none"
    if code:
        return "\n".join(f"- `{item}`" for item in items)
    return "\n".join(f"- {item}" for item in items)


def format_missing_status(snapshot: BotStatusSnapshot) -> str:
    lines: list[str] = []
    if snapshot.missing_context_paths:
        lines.append("- missing context")
        lines.extend(f"  - `{path}`" for path in snapshot.missing_context_paths)
    if snapshot.missing_secret_keys:
        lines.append("- missing secret env")
        lines.extend(f"  - `{key}`" for key in snapshot.missing_secret_keys)
    if not lines:
        return "- none"
    return "\n".join(lines)


def format_failure_detail(error: Exception) -> str:
    if isinstance(error, VerificationError):
        output = truncate_text(error.output.strip(), 1800)
        return "\n".join(
            [
                f"Verification command `{error.command}` output:",
                "",
                "```text",
                output or "(no output)",
                "```",
            ]
        )

    if isinstance(error, MissingContextError):
        return "\n".join(["Missing context:", "", "\n".join(f"- `{path}`" for path in error.missing_paths)])

    if isinstance(error, MissingSecretError):
        return "\n".join(["Missing secret env:", "", "\n".join(f"- `{key}`" for key in error.missing_keys)])

    if isinstance(error, ValueError):
        return str(error)

    return "Check the Actions log for the detailed failure output."


def classify_failure_stage(error: Exception) -> str:
    if isinstance(error, VerificationError):
        return "verification"
    if isinstance(error, MissingContextError):
        return "context"
    if isinstance(error, MissingSecretError):
        return "secret"
    if isinstance(error, ValueError):
        return "options"

    message = str(error).lower()
    if "github api" in message or "pull request" in message:
        return "github"
    if "git " in message or "push" in message or "branch" in message:
        return "git"
    if "codex" in message:
        return "codex"
    return "runtime"


def format_failure_next_steps(
    request: IssueRequest,
    config: BotConfig,
    command: BotCommand | None,
    error: Exception,
) -> str:
    del config
    retry_text = request.comment_body.strip() or f"{BOT_MENTION} retry"
    lines = [
        f"- Retry with the same mention: `{retry_text}`",
        f"- Ask for status: `{BOT_MENTION} status`",
    ]

    if isinstance(error, MissingContextError):
        lines.append("- Add the missing documents to the repository or mounted context directory.")
    elif isinstance(error, MissingSecretError):
        lines.append("- Add the missing secret env keys to the mounted secrets file or CI environment.")
    elif isinstance(error, VerificationError):
        lines.append("- Fix the failing verification output and ask the bot to run again.")
    elif isinstance(error, ValueError):
        lines.append(f"- Ask `{BOT_MENTION} help` and retry with a clearer natural language request.")
    else:
        lines.append("- Open the Actions log and retry after fixing the reported failure.")

    return "\n".join(lines)


def format_run_url() -> str:
    server_url = os.getenv("GITHUB_SERVER_URL", "https://github.com")
    repository = os.getenv("GITHUB_REPOSITORY")
    run_id = os.getenv("GITHUB_RUN_ID")
    if not repository or not run_id:
        return ""
    return f"Actions log: {server_url}/{repository}/actions/runs/{run_id}"


def trim_codex_output(output: str) -> str:
    text = output.strip()
    if not text:
        return "(empty plan output)"
    return truncate_text(text, 4000)


def truncate_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 20].rstrip() + "\n... (truncated)"


def safe_create_issue_comment(request: IssueRequest, body: str) -> None:
    if os.getenv("BOT_CREATE_PR") != "1":
        return

    try:
        comment_url = create_issue_comment(request.repository, request.issue_number, body)
        if comment_url:
            print(f"Issue comment created: {comment_url}")
    except Exception as comment_error:
        print(f"Failed to create issue comment: {comment_error}")


def configure_output_encoding() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def format_check_commands(config: BotConfig) -> str:
    commands = get_check_commands(config)
    if not commands:
        return "`none`"
    return ", ".join(f"`{command}`" for command in commands)
