from dataclasses import dataclass
from pathlib import Path

from app.attachments import collect_attachment_context, format_attachment_context
from app.bot import BotCommand, BotRuntimeOptions, IssueRequest, build_codex_commit_message, build_plan_prompt, build_task_prompt
from app.config import BotConfig
from app.github_pr import PullRequestResult, checkout_bot_branch, commit_push_and_open_pr
from app.llm_provider import ProviderExecutionRequest, build_plan_output_path, run_provider_request
from app.repo_context import collect_context_documents, collect_project_summary, format_context_documents
from app.runtime_secrets import load_runtime_secrets
from app.verification import run_verification


@dataclass(frozen=True)
class CodexRunResult:
    output: str


def create_codex_pr(
    request: IssueRequest,
    workspace: Path,
    config: BotConfig,
    command: BotCommand | None = None,
    runtime_options: BotRuntimeOptions | None = None,
) -> PullRequestResult:
    runtime_options = runtime_options or BotRuntimeOptions(mode="codex", provider="codex", verify=True)
    branch_name = checkout_bot_branch(request, workspace, config)

    run_codex(request, workspace, config, command, runtime_options)
    if runtime_options.verify:
        run_verification(config, workspace)

    return commit_push_and_open_pr(
        request=request,
        workspace=workspace,
        config=config,
        branch_name=branch_name,
        commit_message=build_codex_commit_message(request, config),
    )


def run_codex(
    request: IssueRequest,
    workspace: Path,
    config: BotConfig,
    bot_command: BotCommand | None = None,
    runtime_options: BotRuntimeOptions | None = None,
) -> CodexRunResult:
    runtime_options = runtime_options or BotRuntimeOptions(mode="codex", provider="codex", verify=True)
    available_secret_keys = load_runtime_secrets(config)
    attachment_context = format_attachment_context(collect_attachment_context(request))
    documents = collect_context_documents(workspace, config)
    repository_context = format_context_documents(documents)
    project_summary = collect_project_summary(workspace)
    prompt = build_task_prompt(
        request,
        config,
        repository_context,
        project_summary,
        available_secret_keys,
        attachment_context,
    )

    print(f"저장소 규칙 문서 {len(documents)}개를 프롬프트에 포함합니다.")
    print(f"{runtime_options.provider} 실행 시작")
    result = run_provider_request(
        ProviderExecutionRequest(
            workspace=workspace,
            prompt=prompt,
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
) -> CodexRunResult:
    runtime_options = runtime_options or BotRuntimeOptions(mode="codex", provider="codex", verify=False)
    available_secret_keys = load_runtime_secrets(config)
    attachment_context = format_attachment_context(collect_attachment_context(request))
    documents = collect_context_documents(workspace, config)
    repository_context = format_context_documents(documents)
    project_summary = collect_project_summary(workspace)
    prompt = build_plan_prompt(
        request,
        config,
        repository_context,
        project_summary,
        available_secret_keys,
        attachment_context,
    )
    output_path = build_plan_output_path()

    print(f"저장소 규칙 문서 {len(documents)}개를 계획 프롬프트에 포함합니다.")
    print(f"{runtime_options.provider} 계획 생성 시작")
    result = run_provider_request(
        ProviderExecutionRequest(
            workspace=workspace,
            prompt=prompt,
            runtime_options=runtime_options,
            bot_command=bot_command,
            output_last_message=output_path,
        )
    )
    return CodexRunResult(output=result.output)
