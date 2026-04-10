import subprocess
from dataclasses import dataclass
from pathlib import Path

from app.bot import IssueRequest, build_task_prompt
from app.config import BotConfig, load_config
from app.github_pr import PullRequestResult, checkout_bot_branch, commit_push_and_open_pr
from app.repo_context import collect_context_documents, format_context_documents
from app.verification import run_verification


@dataclass(frozen=True)
class CodexRunResult:
    output: str


def create_codex_pr(request: IssueRequest, workspace: Path) -> PullRequestResult:
    config = load_config(workspace)
    branch_name = checkout_bot_branch(request, workspace, config)

    run_codex(request, workspace, config)
    run_verification(config, workspace)

    return commit_push_and_open_pr(
        request=request,
        workspace=workspace,
        config=config,
        branch_name=branch_name,
        commit_message=f"feat: issue #{request.issue_number} Codex 작업 반영",
    )


def run_codex(request: IssueRequest, workspace: Path, config: BotConfig) -> CodexRunResult:
    documents = collect_context_documents(workspace, config)
    repository_context = format_context_documents(documents)
    prompt = build_task_prompt(request, config, repository_context)
    command = build_codex_command(workspace)

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


def build_codex_command(workspace: Path) -> list[str]:
    return [
        "codex",
        "exec",
        "--cd",
        str(workspace),
        "--ephemeral",
        "--dangerously-bypass-approvals-and-sandbox",
        "--color",
        "never",
        "-",
    ]
