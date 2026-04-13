import os
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path


CONFIG_FILE = ".issue-to-pr-bot.yml"
BOT_MENTION = os.getenv("BOT_MENTION", "@incle-issue-to-pr-bot")


DEFAULT_CONTEXT_PATHS = [
    ".issue-to-pr-bot.yml",
    "AGENTS.md",
    "CODEX.md",
    "CONTRIBUTING.md",
    "README.md",
    ".github/pull_request_template.md",
    ".github/PULL_REQUEST_TEMPLATE.md",
    ".github/ISSUE_TEMPLATE",
    ".editorconfig",
    "pyproject.toml",
    "package.json",
]


DEFAULT_PROTECTED_PATHS = [
    ".github/workflows/**",
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
]


@dataclass(frozen=True)
class BotConfig:
    provider: str = "codex"
    branch_prefix: str = "bot"
    branch_name_template: str = "{branch_prefix}/issue-{issue_number}{comment_suffix}-{slug}"
    pr_title_template: str = "[bot] Issue #{issue_number}: {issue_title}"
    codex_commit_message_template: str = "feat: issue #{issue_number} Codex 작업 반영"
    test_commit_message_template: str = "chore: issue #{issue_number} 작업 기록"
    output_dir: str = "bot-output"
    test_command: str = "python -m unittest discover -s tests"
    check_commands: list[str] = field(default_factory=list)
    mode: str = "codex"
    context_paths: list[str] = field(default_factory=lambda: DEFAULT_CONTEXT_PATHS.copy())
    external_context_paths: list[str] = field(default_factory=list)
    required_context_paths: list[str] = field(default_factory=list)
    secret_env_keys: list[str] = field(default_factory=list)
    required_secret_env: list[str] = field(default_factory=list)
    protected_paths: list[str] = field(default_factory=lambda: DEFAULT_PROTECTED_PATHS.copy())


def load_config(workspace: Path) -> BotConfig:
    config_path = workspace / CONFIG_FILE
    if not config_path.exists():
        return BotConfig()

    defaults = BotConfig()
    values = parse_simple_bot_config(config_path.read_text(encoding="utf-8-sig"))
    return BotConfig(
        output_dir=values.get("output_dir", defaults.output_dir),
        secret_env_keys=as_string_list(values.get("secret_env_keys"), defaults.secret_env_keys),
        required_secret_env=as_string_list(values.get("required_secret_env"), defaults.required_secret_env),
    )


def get_check_commands(config: BotConfig) -> list[str]:
    commands = [command.strip() for command in config.check_commands if command.strip()]
    if commands:
        return commands

    fallback = config.test_command.strip()
    return [fallback] if fallback else []


def as_string_list(value: object, fallback: list[str]) -> list[str]:
    if value is None:
        return fallback.copy()
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return fallback.copy()


def parse_simple_bot_config(config_text: str) -> dict[str, str | list[str]]:
    values: dict[str, str | list[str]] = {}
    in_bot_section = False
    current_list_key: str | None = None

    for raw_line in config_text.splitlines():
        line = strip_config_comment(raw_line).rstrip()
        if not line.strip():
            continue

        if not raw_line.startswith((" ", "\t")):
            section = line.strip().rstrip(":")
            in_bot_section = section == "bot"
            current_list_key = None
            continue

        if not in_bot_section:
            continue

        stripped = line.strip()
        if stripped.startswith("- ") and current_list_key:
            existing = values.setdefault(current_list_key, [])
            if isinstance(existing, list):
                existing.append(unquote_config_value(stripped[2:].strip()))
            continue

        if ":" not in stripped:
            continue

        key, raw_value = stripped.split(":", 1)
        key = key.strip()
        raw_value = raw_value.strip()
        if not raw_value:
            values[key] = []
            current_list_key = key
            continue

        values[key] = unquote_config_value(raw_value)
        current_list_key = None

    return values


def unquote_config_value(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def strip_config_comment(line: str) -> str:
    quote: str | None = None
    escaped = False
    for index, char in enumerate(line):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if quote:
            if char == quote:
                quote = None
            continue
        if char in {"'", '"'}:
            quote = char
            continue
        if char == "#":
            return line[:index]
    return line
