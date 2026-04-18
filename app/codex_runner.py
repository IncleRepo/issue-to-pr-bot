from dataclasses import dataclass
from pathlib import Path

from app.bot import BotCommand, BotRuntimeOptions, IssueRequest
from app.config import BotConfig, GitSyncRule
from app.github_pr import BaseSyncResult, PullRequestResult, checkout_request_target, commit_push_and_open_pr
from app.llm_provider import ProviderExecutionRequest, build_plan_output_path, run_provider_request
from app.prompting import PreparedPrompt, prepare_prompt
from app.verification import resolve_verification_plan, run_verification


@dataclass(frozen=True)
class CodexRunResult:
    output: str


def create_codex_pr(
    request: IssueRequest,
    workspace: Path,
    config: BotConfig,
    command: BotCommand | None = None,
    runtime_options: BotRuntimeOptions | None = None,
    prepared_prompt: PreparedPrompt | None = None,
) -> PullRequestResult:
    runtime_options = runtime_options or BotRuntimeOptions(mode="codex", provider="codex", verify=True)
    target = checkout_request_target(request, workspace, config)
    verification_commands: list[str] = []
    primary_prompt = prepared_prompt or prepare_prompt(request, workspace, config, action="run")

    run_codex(request, workspace, config, command, runtime_options, primary_prompt)

    if runtime_options.verify:
        verification_plan = resolve_verification_plan(config, workspace, request)
        verification_commands = verification_plan.commands
        print(
            "Selected verification plan: "
            f"profile={verification_plan.profile}, "
            f"changed_files={len(verification_plan.changed_files)}, "
            f"commands={verification_commands or ['<none>']}"
        )
        if verification_plan.changed_files:
            run_verification(config, workspace, commands=verification_commands)
        else:
            print("Codex run produced no workspace changes, so verification is skipped.")

    follow_up_prompt = primary_prompt
    attempted_recoveries: set[str] = set()
    while True:
        try:
            return commit_push_and_open_pr(
                request=request,
                workspace=workspace,
                config=config,
                branch_name=target.branch_name,
                base_branch=target.base_branch,
                verification_commands=verification_commands,
            )
        except RuntimeError as error:
            recovery_kind = classify_publish_recovery_error(error)
            if recovery_kind is None or recovery_kind in attempted_recoveries:
                raise
            attempted_recoveries.add(recovery_kind)
            follow_up_prompt = build_publish_recovery_follow_up_prompt(follow_up_prompt, error)
            if recovery_kind == "missing_local_commit":
                print("Codex가 로컬 변경 후 커밋 없이 종료해, 커밋만 보강하도록 한 번 더 요청합니다.")
            else:
                print("Publish 대상에 작업용 파일이 포함되어 있어, Codex에게 정리 후 다시 종료하도록 한 번 더 요청합니다.")
            run_codex(request, workspace, config, command, runtime_options, follow_up_prompt)


def run_codex(
    request: IssueRequest,
    workspace: Path,
    config: BotConfig,
    bot_command: BotCommand | None = None,
    runtime_options: BotRuntimeOptions | None = None,
    prepared_prompt: PreparedPrompt | None = None,
) -> CodexRunResult:
    runtime_options = runtime_options or BotRuntimeOptions(mode="codex", provider="codex", verify=True)
    prepared_prompt = prepared_prompt or prepare_prompt(request, workspace, config, action="run")

    print(
        "Prompt metrics: "
        f"chars={prepared_prompt.metrics.prompt_chars}, "
        f"context_docs={prepared_prompt.metrics.selected_document_count}/{prepared_prompt.metrics.document_count}, "
        f"attachments={prepared_prompt.metrics.attachment_count}, "
        f"collection={prepared_prompt.metrics.collection_seconds:.2f}s"
    )
    print(f"{runtime_options.provider} execution started")
    result = run_provider_request(
        ProviderExecutionRequest(
            workspace=workspace,
            prompt=prepared_prompt.prompt,
            runtime_options=runtime_options,
            bot_command=bot_command,
        )
    )
    return CodexRunResult(output=result.output)


def is_missing_local_commit_error(error: Exception) -> bool:
    return "Codex finished with local changes but no local commit." in str(error)


def is_non_publishable_workspace_changes_error(error: Exception) -> bool:
    return "Non-publishable workspace files are present in the publishable diff." in str(error)


def classify_publish_recovery_error(error: Exception) -> str | None:
    if is_missing_local_commit_error(error):
        return "missing_local_commit"
    if is_non_publishable_workspace_changes_error(error):
        return "non_publishable_workspace_changes"
    return None


def build_missing_commit_follow_up_prompt(prepared_prompt: PreparedPrompt) -> PreparedPrompt:
    follow_up = "\n".join(
        [
            prepared_prompt.prompt,
            "",
            "Follow-up wrapper instruction:",
            "- You already changed files in the workspace, but you exited without creating the required local commit.",
            "- Do not restart the task from scratch.",
            "- Inspect the current workspace state, create or amend the publishable local commit now, and then exit.",
            "- Do not push, open a PR, merge, or perform any extra GitHub workflow steps yourself.",
            "- Only make additional code changes if they are strictly necessary to complete that local commit cleanly.",
        ]
    )
    return PreparedPrompt(
        prompt=follow_up,
        attachment_info=prepared_prompt.attachment_info,
        available_secret_keys=prepared_prompt.available_secret_keys,
        repository_context=prepared_prompt.repository_context,
        project_summary=prepared_prompt.project_summary,
        code_context=prepared_prompt.code_context,
        attachment_context=prepared_prompt.attachment_context,
        metrics=prepared_prompt.metrics,
    )


def build_non_publishable_workspace_changes_follow_up_prompt(
    prepared_prompt: PreparedPrompt,
    error: Exception,
) -> PreparedPrompt:
    follow_up = "\n".join(
        [
            prepared_prompt.prompt,
            "",
            "Follow-up wrapper instruction:",
            "- Workspace-only scratch files were found in the publishable diff.",
            "- Do not restart the task from scratch.",
            "- Remove every `.issue-to-pr-bot/input/**`, `.issue-to-pr-bot/output/**`, and legacy `.runtime-output/**` file from staged changes and from the publishable commit history before exiting.",
            "- Amend or replace the local commit if needed so those files are not part of the branch diff anymore.",
            "- Do not remove the files from disk if they are still needed as local scratch files; just make sure they are not tracked in the publishable diff.",
            "- Do not push, open a PR, merge, or perform any extra GitHub workflow steps yourself.",
            "- Wrapper validation failed with this detail:",
            str(error),
        ]
    )
    return PreparedPrompt(
        prompt=follow_up,
        attachment_info=prepared_prompt.attachment_info,
        available_secret_keys=prepared_prompt.available_secret_keys,
        repository_context=prepared_prompt.repository_context,
        project_summary=prepared_prompt.project_summary,
        code_context=prepared_prompt.code_context,
        attachment_context=prepared_prompt.attachment_context,
        metrics=prepared_prompt.metrics,
    )


def build_publish_recovery_follow_up_prompt(
    prepared_prompt: PreparedPrompt,
    error: Exception,
) -> PreparedPrompt:
    if is_missing_local_commit_error(error):
        return build_missing_commit_follow_up_prompt(prepared_prompt)
    if is_non_publishable_workspace_changes_error(error):
        return build_non_publishable_workspace_changes_follow_up_prompt(prepared_prompt, error)
    raise ValueError(f"Unsupported publish recovery error: {error}")


def should_rerun_codex_after_sync(sync_result: BaseSyncResult) -> bool:
    return sync_result.has_conflicts or sync_result.changed_tree


def build_post_sync_prompt(
    prepared_prompt: PreparedPrompt,
    sync_result: BaseSyncResult,
    rule: GitSyncRule,
) -> PreparedPrompt:
    sync_summary = "\n".join(
        [
            "",
            "Follow-up wrapper instruction:",
            (
                f"- The wrapper has just {sync_result.mode}d the current branch with `{sync_result.base_branch}` "
                "according to repository workflow guidance."
            ),
            "- Re-evaluate the current implementation against the updated base branch state.",
            "- If the base sync changed assumptions, rework or partially re-implement the code on top of the synced state.",
            "- If there are merge/rebase conflicts, resolve them in the worktree and continue from the synced state.",
            "- Rewrite the PR body/summary drafts if the implementation direction changed.",
            "- This is a single follow-up pass after base sync, not an open-ended loop.",
        ]
    )
    return PreparedPrompt(
        prompt=prepared_prompt.prompt + "\n" + sync_summary,
        attachment_info=prepared_prompt.attachment_info,
        available_secret_keys=prepared_prompt.available_secret_keys,
        repository_context=prepared_prompt.repository_context,
        project_summary=prepared_prompt.project_summary,
        code_context=prepared_prompt.code_context,
        attachment_context=prepared_prompt.attachment_context,
        metrics=prepared_prompt.metrics,
    )


def log_base_sync_result(sync_result: BaseSyncResult, before_codex: bool) -> None:
    phase_text = "before Codex execution" if before_codex else "before PR finalization"
    if sync_result.up_to_date:
        print(f"Base branch `{sync_result.base_branch}` is already up to date {phase_text}.")
        return
    if sync_result.has_conflicts:
        print(
            f"Base branch `{sync_result.base_branch}` sync ({sync_result.mode}) produced conflicts {phase_text}. "
            "Codex will continue from the synced conflict state."
        )
        return
    print(
        f"Base branch `{sync_result.base_branch}` synced with mode `{sync_result.mode}` {phase_text}. "
        "Codex will reconcile the updated state."
    )


def run_codex_plan(
    request: IssueRequest,
    workspace: Path,
    config: BotConfig,
    bot_command: BotCommand | None = None,
    runtime_options: BotRuntimeOptions | None = None,
    prepared_prompt: PreparedPrompt | None = None,
) -> CodexRunResult:
    runtime_options = runtime_options or BotRuntimeOptions(mode="codex", provider="codex", verify=False)
    prepared_prompt = prepared_prompt or prepare_prompt(request, workspace, config, action="plan")
    output_path = build_plan_output_path()

    print(
        "Plan prompt metrics: "
        f"chars={prepared_prompt.metrics.prompt_chars}, "
        f"context_docs={prepared_prompt.metrics.selected_document_count}/{prepared_prompt.metrics.document_count}, "
        f"attachments={prepared_prompt.metrics.attachment_count}, "
        f"collection={prepared_prompt.metrics.collection_seconds:.2f}s"
    )
    print(f"{runtime_options.provider} plan generation started")
    result = run_provider_request(
        ProviderExecutionRequest(
            workspace=workspace,
            prompt=prepared_prompt.prompt,
            runtime_options=runtime_options,
            bot_command=bot_command,
            output_last_message=output_path,
        )
    )
    return CodexRunResult(output=result.output)
