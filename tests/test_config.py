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

    def test_load_config_reads_current_supported_settings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            Path(temp_dir, ".issue-to-pr-bot.yml").write_text(
                """
bot:
  output_dir: "agent-output"
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
    - "python -m compileall -q app tests"
  protected_paths:
    - "secrets/**"
  base_branch: "develop"
  git_sync_phase: "before_pr"
  git_sync_action: "rebase"
  git_sync_base_branch: "main"
  git_sync_require_conflict_free: false
""",
                encoding="utf-8",
            )

            config = load_config(Path(temp_dir))

        self.assertEqual(config.output_dir, "agent-output")
        self.assertEqual(BOT_MENTION, "@incle-issue-to-pr-bot")
        self.assertEqual(config.check_commands, ["python -m compileall -q app tests"])
        self.assertEqual(config.context_paths, ["README.md"])
        self.assertEqual(config.external_context_paths, ["product"])
        self.assertEqual(config.required_context_paths, ["docs/domain.md", "external:product/api.md"])
        self.assertEqual(config.secret_env_keys, ["DB_URL", "OPENAI_API_KEY"])
        self.assertEqual(config.required_secret_env, ["DB_URL"])
        self.assertIn("secrets/**", config.protected_paths)
        self.assertIn("*.pem", config.protected_paths)
        self.assertEqual(config.default_base_branch, "develop")
        self.assertIsNotNone(config.git_sync_rule)
        assert config.git_sync_rule is not None
        self.assertEqual(config.git_sync_rule.phase, "before_pr")
        self.assertEqual(config.git_sync_rule.action, "rebase")
        self.assertEqual(config.git_sync_rule.base_branch, "main")
        self.assertFalse(config.git_sync_rule.require_conflict_free)

    def test_load_config_ignores_removed_legacy_override_keys(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            Path(temp_dir, ".issue-to-pr-bot.yml").write_text(
                """
bot:
  branch_prefix: "agent"
  branch_name_template: "{branch_prefix}/{issue_number}-{slug}"
  pr_title_template: "BOT-{issue_number}: {issue_title}"
  codex_commit_message_template: "{commit_type}: {issue_title}"
  test_commit_message_template: "chore: marker"
  mode: "test-pr"
  provider: "other"
  test_command: "python -m pytest"
""",
                encoding="utf-8",
            )

            config = load_config(Path(temp_dir))

        defaults = BotConfig()
        self.assertEqual(config.branch_prefix, defaults.branch_prefix)
        self.assertEqual(config.branch_name_template, defaults.branch_name_template)
        self.assertEqual(config.pr_title_template, defaults.pr_title_template)
        self.assertEqual(config.codex_commit_message_template, defaults.codex_commit_message_template)
        self.assertEqual(config.test_commit_message_template, defaults.test_commit_message_template)
        self.assertEqual(config.mode, defaults.mode)
        self.assertEqual(config.provider, defaults.provider)
        self.assertEqual(config.test_command, defaults.test_command)

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
