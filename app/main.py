"""Backward-compatible runtime entrypoint and helper exports."""

from pathlib import Path

from app.auto_merge import handle_pull_request_review_event
from app.runtime import orchestrator as runtime_orchestrator
from app.runtime.comments import (
    BotStatusSnapshot,
    classify_failure_stage,
    configure_output_encoding,
    format_attachment_summary,
    format_changed_files,
    format_failure_detail,
    format_failure_next_steps,
    format_markdown_list,
    format_merge_request_status,
    format_missing_status,
    format_run_url,
    format_runtime_options,
    format_secret_keys_for_log,
    format_verification_status,
    post_failure_comment,
    post_help_comment,
    post_merge_request_comment,
    post_no_changes_comment,
    post_plan_comment,
    post_status_comment,
    post_success_comment,
    safe_create_issue_comment,
    trim_codex_output,
    truncate_text,
)

load_event_payload = runtime_orchestrator.load_event_payload
handle_merge_request = runtime_orchestrator.handle_merge_request
parse_pull_request_number = runtime_orchestrator.parse_pull_request_number
collect_status_snapshot = runtime_orchestrator.collect_status_snapshot
run_configured_mode = runtime_orchestrator.run_configured_mode


def main() -> None:
    runtime_orchestrator.main()


def is_pull_request_review_event(payload: dict) -> bool:
    return runtime_orchestrator.is_pull_request_review_event(payload)


def run_bot(workspace: Path, config, request) -> None:
    runtime_orchestrator.run_bot(workspace, config, request)


def handle_pull_request_review_payload(workspace: Path, config, payload: dict) -> None:
    request = runtime_orchestrator.build_issue_request(payload)
    command = runtime_orchestrator.parse_bot_command(request.comment_body, config)
    if command:
        try:
            run_bot(workspace, config, request)
        except Exception as error:
            import traceback

            print(traceback.format_exc())
            post_failure_comment(request, config, error, command)
            raise
        return

    handle_pull_request_review_event(payload)


__all__ = [
    "BotStatusSnapshot",
    "classify_failure_stage",
    "collect_status_snapshot",
    "configure_output_encoding",
    "format_attachment_summary",
    "format_changed_files",
    "format_failure_detail",
    "format_failure_next_steps",
    "format_markdown_list",
    "format_merge_request_status",
    "format_missing_status",
    "format_run_url",
    "format_runtime_options",
    "format_secret_keys_for_log",
    "format_verification_status",
    "handle_merge_request",
    "handle_pull_request_review_event",
    "handle_pull_request_review_payload",
    "is_pull_request_review_event",
    "load_event_payload",
    "main",
    "parse_pull_request_number",
    "post_failure_comment",
    "post_help_comment",
    "post_merge_request_comment",
    "post_no_changes_comment",
    "post_plan_comment",
    "post_status_comment",
    "post_success_comment",
    "run_bot",
    "run_configured_mode",
    "safe_create_issue_comment",
    "trim_codex_output",
    "truncate_text",
]


if __name__ == "__main__":
    main()
