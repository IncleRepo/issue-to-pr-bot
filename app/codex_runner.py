import subprocess
from dataclasses import dataclass
from pathlib import Path

from app.bot import IssueRequest, build_task_prompt
from app.config import BotConfig, load_config
from app.github_pr import PullRequestResult, checkout_bot_branch, commit_push_and_open_pr
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
    prompt = build_task_prompt(request, config)
    command = build_codex_command(workspace)

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
        "--sandbox",
        "workspace-write",
        "--color",
        "never",
        "-",
    ]
