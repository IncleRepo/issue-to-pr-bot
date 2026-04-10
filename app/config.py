from dataclasses import dataclass
from dataclasses import field
from pathlib import Path


CONFIG_FILE = ".issue-to-pr-bot.yml"


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
    command: str = "/bot run"
    plan_command: str = "/bot plan"
    mention: str = "@incle-issue-to-pr-bot"
    branch_prefix: str = "bot"
    output_dir: str = "bot-output"
    test_command: str = "python -m unittest discover -s tests"
    mode: str = "test-pr"
    context_paths: list[str] = field(default_factory=lambda: DEFAULT_CONTEXT_PATHS.copy())
    protected_paths: list[str] = field(default_factory=lambda: DEFAULT_PROTECTED_PATHS.copy())


def load_config(workspace: Path) -> BotConfig:
    config_path = workspace / CONFIG_FILE
    if not config_path.exists():
        return BotConfig()

    defaults = BotConfig()
    values = parse_simple_bot_config(config_path.read_text(encoding="utf-8-sig"))
    return BotConfig(
        command=values.get("command", defaults.command),
        plan_command=values.get("plan_command", defaults.plan_command),
        mention=values.get("mention", defaults.mention),
        branch_prefix=values.get("branch_prefix", defaults.branch_prefix),
        output_dir=values.get("output_dir", defaults.output_dir),
        test_command=values.get("test_command", defaults.test_command),
        mode=values.get("mode", defaults.mode),
        context_paths=values.get("context_paths", defaults.context_paths),
        protected_paths=values.get("protected_paths", defaults.protected_paths),
    )


def parse_simple_bot_config(config_text: str) -> dict[str, str | list[str]]:
    values: dict[str, str | list[str]] = {}
    in_bot_section = False
    current_list_key: str | None = None

    for raw_line in config_text.splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
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
