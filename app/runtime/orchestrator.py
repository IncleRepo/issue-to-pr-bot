"""이슈, PR, 리뷰 이벤트 전반을 조율하는 상위 런타임 모듈."""

import json
import os
import re
import traceback
from pathlib import Path

from app.attachments import collect_attachment_context
from app.auto_merge import handle_auto_merge_event, handle_pull_request_review_event
from app.codex_runner import create_codex_pr, run_codex_plan
from app.config import BOT_MENTION, BotConfig, load_config
from app.domain.models import BotCommand, BotRuntimeOptions, IssueRequest
from app.github_pr import (
    MergeRequestResult,
    PullRequestResult,
    apply_issue_metadata_if_possible,
    create_test_pr,
    request_pull_request_merge,
)
from app.prompting import PreparedPrompt, prepare_prompt
from app.repo_context import MissingContextError, collect_context_documents, get_external_context_root
from app.repo_rules import resolve_bot_config
from app.runtime.comments import (
    BotStatusSnapshot,
    configure_output_encoding,
    format_check_commands,
    format_runtime_options,
    format_secret_keys_for_log,
    post_failure_comment,
    post_help_comment,
    post_merge_request_comment,
    post_no_changes_comment,
    post_plan_comment,
    post_status_comment,
    post_success_comment,
)
from app.runtime_secrets import MissingSecretError, get_secrets_file_path, load_runtime_secrets
from app.automation.parsing import build_issue_request, parse_bot_command, resolve_runtime_options
from app.automation.templates import build_branch_name


def load_event_payload() -> dict:
    event_path = os.getenv("GITHUB_EVENT_PATH")
    if not event_path:
        print("GITHUB_EVENT_PATH is missing. Using a local sample payload.")
        return {
            "action": "created",
            "comment": {
                "body": f"{BOT_MENTION} README local run section 추가해줘",
                "id": 1,
                "user": {"login": "local-user"},
            },
            "issue": {
                "number": 1,
                "title": "Local sample issue",
                "body": "Sample requirement",
            },
            "repository": {"full_name": "example/issue-to-pr-bot"},
        }

    payload_text = Path(event_path).read_text(encoding="utf-8-sig")
    return json.loads(payload_text)


def main() -> None:
    configure_output_encoding()
    workspace = Path.cwd()
    config = resolve_bot_config(workspace, load_config(workspace))
    payload = load_event_payload()

    if is_auto_merge_signal_event(payload, config):
        handle_auto_merge_event(payload, workspace=workspace, config=config)
        return

    if is_pull_request_review_event(payload):
        handle_pull_request_review_payload(workspace, config, payload)
        return

    request = build_issue_request(payload)

    try:
        run_bot(workspace, config, request)
    except Exception as error:
        print(traceback.format_exc())
        post_failure_comment(request, config, error, parse_bot_command(request.comment_body, config))
        raise


def is_pull_request_review_event(payload: dict) -> bool:
    return bool(payload.get("review")) and bool(payload.get("pull_request"))


def is_auto_merge_signal_event(payload: dict, config: BotConfig) -> bool:
    if is_pull_request_review_event(payload):
        return not parse_bot_command(((payload.get("review") or {}).get("body") or ""), config)
    return bool(payload.get("check_run")) or bool(payload.get("check_suite")) or (
        payload.get("state") is not None and isinstance(payload.get("branches"), list)
    )


def handle_pull_request_review_payload(workspace: Path, config: BotConfig, payload: dict) -> None:
    request = build_issue_request(payload)
    command = parse_bot_command(request.comment_body, config)
    if command:
        try:
            run_bot(workspace, config, request)
        except Exception as error:
            print(traceback.format_exc())
            post_failure_comment(request, config, error, command)
            raise
        return

    handle_pull_request_review_event(payload, workspace=workspace, config=config)


def run_bot(workspace: Path, config: BotConfig, request: IssueRequest) -> None:
    command = parse_bot_command(request.comment_body, config)
    if not command:
        print("No bot mention found in the comment.")
        return

    if command.action == "help":
        post_help_comment(request, config)
        return

    if command.action == "status":
        snapshot = collect_status_snapshot(workspace, config)
        post_status_comment(request, config, snapshot)
        return

    runtime_options = resolve_runtime_options(command, config)
    branch_name = build_branch_name(request, config)

    print("Bot execution started")
    print(f"Repository: {request.repository}")
    print(f"Issue/PR number: {request.issue_number}")
    print(f"Title: {request.issue_title}")
    print(f"Comment author: {request.comment_author}")
    print(f"Action: {command.action}")
    print(f"Runtime options: {format_runtime_options(runtime_options)}")
    print(f"Verification commands: {format_check_commands(config)}")
    print(f"Target branch: {branch_name}")

    if os.getenv("BOT_CREATE_PR") != "1":
        print("BOT_CREATE_PR is not 1. Skipping PR creation.")
        return

    if command.action == "merge":
        merge_result = handle_merge_request(request, workspace=workspace, config=config)
        post_merge_request_comment(request, command, runtime_options, merge_result)
        return

    prepared_prompt: PreparedPrompt | None = None
    if command.action == "plan" or runtime_options.mode == "codex":
        prepared_prompt = prepare_prompt(request, workspace, config, command.action)
        attachment_info = prepared_prompt.attachment_info
    else:
        attachment_info = collect_attachment_context(request)

    if prepared_prompt:
        print(f"Available secret env keys: {format_secret_keys_for_log(prepared_prompt.available_secret_keys)}")
        print(
            "Prompt preparation: "
            f"chars={prepared_prompt.metrics.prompt_chars}, "
            f"context_docs={prepared_prompt.metrics.selected_document_count}/{prepared_prompt.metrics.document_count}, "
            f"attachments={prepared_prompt.metrics.attachment_count}, "
            f"collection={prepared_prompt.metrics.collection_seconds:.2f}s"
        )
    else:
        print("Available secret env keys: `none`")

    maybe_apply_issue_metadata(request, workspace)

    if command.action == "plan":
        result = run_codex_plan(request, workspace, config, command, runtime_options, prepared_prompt)
        post_plan_comment(request, config, command, runtime_options, attachment_info, result.output)
        return

    result = run_configured_mode(runtime_options, request, workspace, config, command, prepared_prompt)
    merge_result = None
    if runtime_options.request_merge:
        merge_result = handle_merge_request(request, result.pull_request_url, workspace=workspace, config=config)

    if result.created:
        print(f"PR ready: {result.pull_request_url}")
        post_success_comment(request, config, command, runtime_options, attachment_info, result, merge_result)
        return

    print("Skipping PR creation because no changes were staged.")
    post_no_changes_comment(request, config, command, runtime_options, attachment_info, result)


def run_configured_mode(
    runtime_options: BotRuntimeOptions,
    request: IssueRequest,
    workspace: Path,
    config: BotConfig,
    command: BotCommand | None = None,
    prepared_prompt: PreparedPrompt | None = None,
) -> PullRequestResult:
    normalized_mode = runtime_options.mode.strip().lower()
    if normalized_mode == "test-pr":
        return create_test_pr(request, workspace, config)
    if normalized_mode == "codex":
        return create_codex_pr(request, workspace, config, command, runtime_options, prepared_prompt)
    raise RuntimeError(f"Unsupported mode: {runtime_options.mode}")


def handle_merge_request(
    request: IssueRequest,
    pull_request_url: str | None = None,
    workspace: Path | None = None,
    config: BotConfig | None = None,
) -> MergeRequestResult:
    if not request.is_pull_request and not pull_request_url:
        raise ValueError("Merge requests are only supported from PR comments or after a PR has been created.")

    token = os.getenv("BOT_GITHUB_TOKEN") or os.getenv("GITHUB_TOKEN")
    if not token:
        raise RuntimeError("A GitHub token is required to register a merge request.")

    pull_request_number = request.pull_request_number or parse_pull_request_number(pull_request_url)
    if not pull_request_number:
        raise RuntimeError("Could not determine the target pull request number for merge.")

    if workspace and config:
        from app.auto_merge import maybe_prepare_pull_request_for_merge

        maybe_prepare_pull_request_for_merge(workspace, config, request.repository, pull_request_number, token)

    return request_pull_request_merge(request.repository, pull_request_number, token)


def maybe_apply_issue_metadata(request: IssueRequest, workspace: Path) -> None:
    if request.is_pull_request:
        return

    token = os.getenv("BOT_GITHUB_TOKEN") or os.getenv("GITHUB_TOKEN")
    if not token:
        return

    apply_issue_metadata_if_possible(
        repository=request.repository,
        issue_number=request.issue_number,
        request=request,
        token=token,
        workspace=workspace,
    )


def parse_pull_request_number(pull_request_url: str | None) -> int | None:
    if not pull_request_url:
        return None
    match = re.search(r"/pull/(\d+)", pull_request_url)
    return int(match.group(1)) if match else None


def collect_status_snapshot(workspace: Path, config: BotConfig) -> BotStatusSnapshot:
    available_secret_keys: list[str] = []
    missing_secret_keys: list[str] = []
    try:
        available_secret_keys = load_runtime_secrets(config)
    except MissingSecretError as error:
        missing_secret_keys = error.missing_keys

    context_document_count = 0
    missing_context_paths: list[str] = []
    try:
        context_document_count = len(collect_context_documents(workspace, config))
    except MissingContextError as error:
        missing_context_paths = error.missing_paths

    external_context_root = get_external_context_root()
    secrets_file_path = get_secrets_file_path()
    return BotStatusSnapshot(
        available_secret_keys=available_secret_keys,
        missing_secret_keys=missing_secret_keys,
        context_document_count=context_document_count,
        missing_context_paths=missing_context_paths,
        external_context_root=str(external_context_root) if external_context_root else None,
        secrets_file_path=str(secrets_file_path),
        secrets_file_exists=secrets_file_path.exists(),
    )
