import tempfile
import unittest
from pathlib import Path

from app.config import BotConfig, load_config, parse_simple_bot_config


class ConfigTest(unittest.TestCase):
    def test_parse_simple_bot_config(self) -> None:
        values = parse_simple_bot_config(
            """
bot:
  command: "/ai go"
  plan_command: "/ai plan"
  mention: "@agent-bot"
  branch_prefix: "agent"
  output_dir: "agent-output"
  test_command: "python -m unittest"
  mode: "codex"
  context_paths:
    - "README.md"
    - "docs"
  protected_paths:
    - ".github/workflows/**"
"""
        )

        self.assertEqual(values["command"], "/ai go")
        self.assertEqual(values["plan_command"], "/ai plan")
        self.assertEqual(values["mention"], "@agent-bot")
        self.assertEqual(values["branch_prefix"], "agent")
        self.assertEqual(values["output_dir"], "agent-output")
        self.assertEqual(values["test_command"], "python -m unittest")
        self.assertEqual(values["mode"], "codex")
        self.assertEqual(values["context_paths"], ["README.md", "docs"])
        self.assertEqual(values["protected_paths"], [".github/workflows/**"])

    def test_load_config_returns_defaults_without_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = load_config(Path(temp_dir))

        self.assertEqual(config, BotConfig())

    def test_load_config_reads_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            Path(temp_dir, ".issue-to-pr-bot.yml").write_text(
                """
bot:
  command: "/ai go"
  branch_prefix: "agent"
""",
                encoding="utf-8",
            )

            config = load_config(Path(temp_dir))

        self.assertEqual(config.command, "/ai go")
        self.assertEqual(config.mention, "@incle-issue-to-pr-bot")
        self.assertEqual(config.branch_prefix, "agent")
        self.assertEqual(config.output_dir, "bot-output")
        self.assertIn("README.md", config.context_paths)
        self.assertIn(".github/workflows/**", config.protected_paths)


if __name__ == "__main__":
    unittest.main()
