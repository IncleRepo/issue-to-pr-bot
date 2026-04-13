"""Backward-compatible bot API surface.

This module re-exports the public parsing, template, and model helpers from the
structured subpackages so older imports keep working while the codebase moves
toward clearer boundaries.
"""

from app.automation.parsing import (
    build_issue_request,
    parse_bot_command,
    resolve_runtime_options,
    should_run_bot,
    should_run_for_mention,
)
from app.automation.templates import (
    build_branch_name,
    build_codex_commit_message,
    build_plan_prompt,
    build_pull_request_title,
    build_task_prompt,
    build_test_commit_message,
)
from app.domain.models import BotCommand, BotRuntimeOptions, IssueRequest

__all__ = [
    "BotCommand",
    "BotRuntimeOptions",
    "IssueRequest",
    "build_branch_name",
    "build_codex_commit_message",
    "build_issue_request",
    "build_plan_prompt",
    "build_pull_request_title",
    "build_task_prompt",
    "build_test_commit_message",
    "parse_bot_command",
    "resolve_runtime_options",
    "should_run_bot",
    "should_run_for_mention",
]
