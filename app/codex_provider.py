import subprocess
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

from app.bot import BotCommand, BotRuntimeOptions


@dataclass(frozen=True)
class ProviderRunResult:
    output: str
    duration_seconds: float = 0.0
    prompt_chars: int = 0


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

    started_at = perf_counter()
    result = subprocess.run(
        command,
        cwd=workspace,
        input=prompt,
        text=True,
        encoding="utf-8",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    duration_seconds = perf_counter() - started_at

    output = result.stdout or ""
    if output.strip():
        print(format_provider_output(output))

    if result.returncode != 0:
        detail = format_provider_output(output) if output.strip() else "(no codex output)"
        raise RuntimeError(f"Codex execution failed ({result.returncode})\n{detail}")

    if output_last_message and output_last_message.exists():
        return ProviderRunResult(
            output=output_last_message.read_text(encoding="utf-8"),
            duration_seconds=duration_seconds,
            prompt_chars=len(prompt),
        )
    return ProviderRunResult(output=output, duration_seconds=duration_seconds, prompt_chars=len(prompt))


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
        raise ValueError(f"Unsupported effort value: {effort}. Allowed values: {allowed}")
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


def format_provider_output(output: str) -> str:
    text = output.rstrip()
    if len(text) <= 4_000:
        return text
    return text[:3_980].rstrip() + "\n... (provider output truncated)"
