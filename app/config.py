import os
import re
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
class GitSyncRule:
    """문서나 설정 파일에서 읽은 Git workflow 규칙을 표현한다."""

    phase: str
    action: str
    base_branch: str
    require_conflict_free: bool = True
    confidence: str = "low"
    source: str | None = None


@dataclass(frozen=True)
class BotConfig:
    provider: str = "codex"
    branch_prefix: str = "bot"
    branch_name_template: str = "{branch_prefix}/issue-{issue_number}{comment_suffix}-{slug}"
    pr_title_template: str = "[bot] Issue #{issue_number}: {issue_title}"
    codex_commit_message_template: str = "{commit_type}: issue #{issue_number} Codex 작업 반영"
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
    default_base_branch: str | None = None
    git_sync_rule: GitSyncRule | None = None
    git_sync_rules: list[GitSyncRule] = field(default_factory=list)


def bot_name_from_mention(mention: str | None = None) -> str:
    value = (mention or BOT_MENTION).strip()
    return value[1:] if value.startswith("@") else value


def bot_slug_from_mention(mention: str | None = None) -> str:
    name = bot_name_from_mention(mention)
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-").lower()
    return slug or "issue-to-pr-bot"


def load_config(workspace: Path) -> BotConfig:
    config_path = workspace / CONFIG_FILE
    if not config_path.exists():
        return BotConfig()

    defaults = BotConfig()
    values = parse_simple_bot_config(config_path.read_text(encoding="utf-8-sig"))
    explicit_git_sync_rule = build_explicit_git_sync_rule(values)
    return BotConfig(
        output_dir=as_optional_string(values.get("output_dir"), defaults.output_dir) or defaults.output_dir,
        check_commands=as_string_list(values.get("check_commands"), defaults.check_commands),
        context_paths=as_string_list(values.get("context_paths"), defaults.context_paths),
        external_context_paths=as_string_list(values.get("external_context_paths"), defaults.external_context_paths),
        required_context_paths=as_string_list(values.get("required_context_paths"), defaults.required_context_paths),
        secret_env_keys=as_string_list(values.get("secret_env_keys"), defaults.secret_env_keys),
        required_secret_env=as_string_list(values.get("required_secret_env"), defaults.required_secret_env),
        protected_paths=merge_string_lists(defaults.protected_paths, values.get("protected_paths")),
        default_base_branch=as_optional_string(values.get("base_branch"), defaults.default_base_branch),
        git_sync_rule=explicit_git_sync_rule,
        git_sync_rules=[explicit_git_sync_rule] if explicit_git_sync_rule else [],
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


def merge_string_lists(defaults: list[str], value: object) -> list[str]:
    if value is None:
        return defaults.copy()
    merged: list[str] = []
    for item in defaults + as_string_list(value, []):
        if item not in merged:
            merged.append(item)
    return merged


def as_optional_string(value: object, fallback: str | None) -> str | None:
    if value is None:
        return fallback
    text = str(value).strip()
    return text or fallback


def build_explicit_git_sync_rule(values: dict[str, str | list[str]]) -> GitSyncRule | None:
    phase = as_optional_string(values.get("git_sync_phase"), None)
    action = as_optional_string(values.get("git_sync_action"), None)
    base_branch = as_optional_string(values.get("git_sync_base_branch") or values.get("base_branch"), None)
    if not (phase and action and base_branch):
        return None

    require_conflict_free = parse_optional_bool(values.get("git_sync_require_conflict_free"), default=True)
    return GitSyncRule(
        phase=phase.lower(),
        action=action.lower(),
        base_branch=base_branch,
        require_conflict_free=require_conflict_free,
        confidence="explicit",
        source=CONFIG_FILE,
    )


def get_git_sync_rule(config: BotConfig, phase: str | None = None) -> GitSyncRule | None:
    rules = config.git_sync_rules or ([config.git_sync_rule] if config.git_sync_rule else [])
    if not rules:
        return None
    if phase is None:
        return config.git_sync_rule or rules[0]
    for rule in rules:
        if rule.phase == phase:
            return rule
    return None


def parse_optional_bool(value: object, default: bool) -> bool:
    if value is None:
        return default
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


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
