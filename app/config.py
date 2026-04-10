from dataclasses import dataclass
from pathlib import Path


CONFIG_FILE = ".issue-to-pr-bot.yml"


@dataclass(frozen=True)
class BotConfig:
    command: str = "/bot run"
    branch_prefix: str = "bot"
    output_dir: str = "bot-output"
    test_command: str = "python -m unittest discover -s tests"
    mode: str = "test-pr"


def load_config(workspace: Path) -> BotConfig:
    config_path = workspace / CONFIG_FILE
    if not config_path.exists():
        return BotConfig()

    defaults = BotConfig()
    values = parse_simple_bot_config(config_path.read_text(encoding="utf-8-sig"))
    return BotConfig(
        command=values.get("command", defaults.command),
        branch_prefix=values.get("branch_prefix", defaults.branch_prefix),
        output_dir=values.get("output_dir", defaults.output_dir),
        test_command=values.get("test_command", defaults.test_command),
        mode=values.get("mode", defaults.mode),
    )


def parse_simple_bot_config(config_text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    in_bot_section = False

    for raw_line in config_text.splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue

        if not raw_line.startswith((" ", "\t")):
            section = line.strip().rstrip(":")
            in_bot_section = section == "bot"
            continue

        if not in_bot_section:
            continue

        stripped = line.strip()
        if ":" not in stripped:
            continue

        key, raw_value = stripped.split(":", 1)
        values[key.strip()] = unquote_config_value(raw_value.strip())

    return values


def unquote_config_value(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value
