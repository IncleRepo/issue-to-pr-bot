import tempfile
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

from app.bot import BotCommand, BotRuntimeOptions
from app.codex_provider import ProviderRunResult, run_codex_prompt


SUPPORTED_PROVIDERS = {"codex"}


@dataclass(frozen=True)
class ProviderExecutionRequest:
    workspace: Path
    prompt: str
    runtime_options: BotRuntimeOptions
    bot_command: BotCommand | None = None
    output_last_message: Path | None = None


def get_supported_providers() -> list[str]:
    return sorted(SUPPORTED_PROVIDERS)


def ensure_supported_provider(provider: str) -> None:
    if provider not in SUPPORTED_PROVIDERS:
        supported = ", ".join(get_supported_providers())
        raise ValueError(f"Unsupported provider value: {provider}. Supported values: {supported}")


def run_provider_request(request: ProviderExecutionRequest) -> ProviderRunResult:
    ensure_supported_provider(request.runtime_options.provider)
    started_at = perf_counter()

    if request.runtime_options.provider == "codex":
        result = run_codex_prompt(
            workspace=request.workspace,
            prompt=request.prompt,
            bot_command=request.bot_command,
            runtime_options=request.runtime_options,
            output_last_message=request.output_last_message,
        )
        total_duration = perf_counter() - started_at
        print(
            "LLM metrics: "
            f"provider={request.runtime_options.provider}, "
            f"prompt_chars={len(request.prompt)}, "
            f"provider_time={result.duration_seconds:.2f}s, "
            f"total_time={total_duration:.2f}s"
        )
        return result

    raise ValueError(f"Unsupported provider value: {request.runtime_options.provider}")


def build_plan_output_path() -> Path:
    return Path(tempfile.gettempdir()) / "issue-to-pr-bot-provider-plan.txt"
