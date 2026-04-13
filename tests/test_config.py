import tempfile
import unittest
from pathlib import Path

from app.config import BOT_MENTION, BotConfig, get_check_commands, load_config, parse_simple_bot_config


class ConfigTest(unittest.TestCase):
    def test_parse_simple_bot_config(self) -> None:
        values = parse_simple_bot_config(
            """
bot:
  output_dir: "agent-output"
"""
        )

        self.assertEqual(values["output_dir"], "agent-output")

    def test_load_config_returns_defaults_without_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = load_config(Path(temp_dir))

        self.assertEqual(config, BotConfig())

    def test_load_config_reads_output_dir_context_and_secret_settings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            Path(temp_dir, ".issue-to-pr-bot.yml").write_text(
                """
bot:
  mention: "@agent-bot"
  output_dir: "agent-output"
  mode: "test-pr"
  provider: "other"
  context_paths:
    - "README.md"
  external_context_paths:
    - "product"
  required_context_paths:
    - "docs/domain.md"
    - "external:product/api.md"
  secret_env_keys:
    - "DB_URL"
    - "OPENAI_API_KEY"
  required_secret_env:
    - "DB_URL"
  check_commands:
    - "python -m nothing"
""",
                encoding="utf-8",
            )

            config = load_config(Path(temp_dir))

        self.assertEqual(config.output_dir, "agent-output")
        self.assertEqual(BOT_MENTION, "@incle-issue-to-pr-bot")
        self.assertEqual(config.mode, "codex")
        self.assertEqual(config.provider, "codex")
        self.assertEqual(config.check_commands, [])
        self.assertEqual(config.branch_name_template, "{branch_prefix}/issue-{issue_number}{comment_suffix}-{slug}")
        self.assertEqual(config.context_paths, ["README.md"])
        self.assertEqual(config.external_context_paths, ["product"])
        self.assertEqual(config.required_context_paths, ["docs/domain.md", "external:product/api.md"])
        self.assertEqual(config.secret_env_keys, ["DB_URL", "OPENAI_API_KEY"])
        self.assertEqual(config.required_secret_env, ["DB_URL"])

    def test_get_check_commands_prefers_explicit_commands(self) -> None:
        config = BotConfig(
            test_command="python -m unittest",
            check_commands=["python -m compileall -q app tests"],
        )

        self.assertEqual(get_check_commands(config), ["python -m compileall -q app tests"])

    def test_get_check_commands_falls_back_to_test_command(self) -> None:
        config = BotConfig(test_command="python -m unittest")

        self.assertEqual(get_check_commands(config), ["python -m unittest"])


if __name__ == "__main__":
    unittest.main()
