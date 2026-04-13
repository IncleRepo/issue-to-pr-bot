import subprocess
from dataclasses import dataclass
from pathlib import Path

from app.bot import BotCommand, BotRuntimeOptions


@dataclass(frozen=True)
class ProviderRunResult:
    output: str


ALLOWED_EFFORTS = {"low", "medium", "high", "xhigh"}


def run_codex_prompt(
    workspace: Path,
    prompt: str,
    bot_command: BotCommand | None = None,
    runtime_options: BotRuntimeOptions | None = None,
    output_last_message: Path | None = None,
) -> ProviderRunResult:
    command = build_codex_command(
        workspace,
        effort=get_effort(bot_command, runtime_options),
        output_last_message=output_last_message,
    )

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

    if output_last_message and output_last_message.exists():
        return ProviderRunResult(output=output_last_message.read_text(encoding="utf-8"))
    return ProviderRunResult(output=output)


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
