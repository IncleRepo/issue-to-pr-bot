import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from app.attachments import collect_attachment_context, format_attachment_context
from app.bot import (
    BotCommand,
    BotRuntimeOptions,
    IssueRequest,
    build_codex_commit_message,
    build_plan_prompt,
    build_task_prompt,
)
from app.config import BotConfig
from app.github_pr import PullRequestResult, checkout_bot_branch, commit_push_and_open_pr
from app.repo_context import collect_context_documents, collect_project_summary, format_context_documents
from app.runtime_secrets import load_runtime_secrets
from app.verification import run_verification


@dataclass(frozen=True)
class CodexRunResult:
    output: str


ALLOWED_EFFORTS = {"low", "medium", "high", "xhigh"}


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
    command = build_codex_command(workspace, effort=get_effort(bot_command, runtime_options))

    print(f"저장소 규칙 문서 {len(documents)}개를 프롬프트에 포함합니다.")
    print("Codex 실행 시작")
    result = subprocess.run(
        command,
        cwd=workspace,
        input=prompt,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )

    output = result.stdout or ""
    if output.strip():
        print(output.rstrip())

    if result.returncode != 0:
        raise RuntimeError(f"Codex 실행 실패({result.returncode})")

    return CodexRunResult(output=output)


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
    output_path = Path(tempfile.gettempdir()) / "issue-to-pr-bot-codex-plan.txt"
    command = build_codex_command(
        workspace,
        effort=get_effort(bot_command, runtime_options),
        output_last_message=output_path,
    )

    print(f"저장소 규칙 문서 {len(documents)}개를 계획 프롬프트에 포함합니다.")
    print("Codex 계획 생성 시작")
    result = subprocess.run(
        command,
        cwd=workspace,
        input=prompt,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )

    raw_output = result.stdout or ""
    if raw_output.strip():
        print(raw_output.rstrip())

    if result.returncode != 0:
        raise RuntimeError(f"Codex 계획 생성 실패({result.returncode})")

    output = output_path.read_text(encoding="utf-8") if output_path.exists() else raw_output
    return CodexRunResult(output=output)


def get_effort(
    bot_command: BotCommand | None,
    runtime_options: BotRuntimeOptions | None = None,
) -> str | None:
    if runtime_options and runtime_options.effort:
        effort = runtime_options.effort
    elif bot_command:
        effort = bot_command.options.get("effort")
    else:
        return None

    if not effort:
        return None

    effort = effort.lower()
    if effort not in ALLOWED_EFFORTS:
        allowed = ", ".join(sorted(ALLOWED_EFFORTS))
        raise ValueError(f"지원하지 않는 effort 값입니다: {effort}. 허용 값: {allowed}")
    return effort


def build_codex_command(
    workspace: Path,
    effort: str | None = None,
    output_last_message: Path | None = None,
) -> list[str]:
    command = [
        "codex",
        "exec",
        "--cd",
        str(workspace),
        "--ephemeral",
        "--dangerously-bypass-approvals-and-sandbox",
        "--color",
        "never",
    ]
    if effort:
        command.extend(["-c", f'reasoning_effort="{effort}"'])
    if output_last_message:
        command.extend(["--output-last-message", str(output_last_message)])
    command.append("-")
    return command
