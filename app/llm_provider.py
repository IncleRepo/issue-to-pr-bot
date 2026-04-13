import tempfile
from dataclasses import dataclass
from pathlib import Path

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
        raise ValueError(f"지원하지 않는 provider 값입니다: {provider}. 지원 값: {supported}")


def run_provider_request(request: ProviderExecutionRequest) -> ProviderRunResult:
    ensure_supported_provider(request.runtime_options.provider)

    if request.runtime_options.provider == "codex":
        return run_codex_prompt(
            workspace=request.workspace,
            prompt=request.prompt,
            bot_command=request.bot_command,
            runtime_options=request.runtime_options,
            output_last_message=request.output_last_message,
        )

    raise ValueError(f"지원하지 않는 provider 값입니다: {request.runtime_options.provider}")


def build_plan_output_path() -> Path:
    return Path(tempfile.gettempdir()) / "issue-to-pr-bot-provider-plan.txt"
