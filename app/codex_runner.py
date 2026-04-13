from dataclasses import dataclass
from pathlib import Path

from app.bot import BotCommand, BotRuntimeOptions, IssueRequest, build_codex_commit_message
from app.config import BotConfig
from app.github_pr import PullRequestResult, checkout_request_target, commit_push_and_open_pr, sync_pull_request_branch_with_base
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
    if request.is_pull_request and runtime_options.sync_base:
        sync_result = sync_pull_request_branch_with_base(workspace, target.base_branch)
        if sync_result.up_to_date:
            print(f"PR branch is already up to date with {target.base_branch}.")
        elif sync_result.has_conflicts:
            print(f"PR branch now has merge conflicts against {target.base_branch}. Codex will resolve them in-place.")
        else:
            print(f"PR branch synced with {target.base_branch} before Codex execution.")

    run_codex(request, workspace, config, command, runtime_options, prepared_prompt)
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

    return commit_push_and_open_pr(
        request=request,
        workspace=workspace,
        config=config,
        branch_name=target.branch_name,
        base_branch=target.base_branch,
        commit_message=build_codex_commit_message(request, config),
        verification_commands=verification_commands,
    )


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
