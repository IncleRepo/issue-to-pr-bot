import tempfile
import unittest
from pathlib import Path

from app.config import BotConfig, get_check_commands
from app.repo_rules import resolve_bot_config


class RepoRulesTest(unittest.TestCase):
    def test_resolve_bot_config_infers_verification_commands_from_docs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            workspace.joinpath("AGENTS.md").write_text(
                "\n".join(
                    [
                        "# Repository Agent Guide",
                        "",
                        "## Verification",
                        "",
                        "Run all configured verification commands before opening a PR:",
                        "",
                        "```powershell",
                        "python -m compileall -q app tests",
                        "python -m unittest discover -s tests",
                        "```",
                    ]
                ),
                encoding="utf-8",
            )

            resolved = resolve_bot_config(workspace, BotConfig(test_command="python -m unittest"))

        self.assertEqual(
            get_check_commands(resolved),
            [
                "python -m compileall -q app tests",
                "python -m unittest discover -s tests",
            ],
        )

    def test_resolve_bot_config_infers_branch_commit_and_pr_title_templates(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            workspace.joinpath("CONTRIBUTING.md").write_text(
                "\n".join(
                    [
                        "# Contributing",
                        "",
                        "## Automation Conventions",
                        "",
                        "- Branch format: `team/{issue_number}-{slug}`",
                        "- Commit format: `feat(issue-{issue_number}): {issue_title}`",
                        "- PR title format: `[team-bot] #{issue_number} {issue_title}`",
                    ]
                ),
                encoding="utf-8",
            )

            resolved = resolve_bot_config(workspace, BotConfig())

        self.assertEqual(resolved.branch_name_template, "team/{issue_number}-{slug}")
        self.assertEqual(
            resolved.codex_commit_message_template,
            "feat(issue-{issue_number}): {issue_title}",
        )
        self.assertEqual(
            resolved.test_commit_message_template,
            "feat(issue-{issue_number}): {issue_title}",
        )
        self.assertEqual(resolved.pr_title_template, "[team-bot] #{issue_number} {issue_title}")

    def test_resolve_bot_config_keeps_existing_defaults_when_docs_are_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            resolved = resolve_bot_config(Path(temp_dir), BotConfig())

        self.assertEqual(resolved, BotConfig())

    def test_resolve_bot_config_infers_protected_paths_from_docs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            workspace.joinpath("README.md").write_text(
                "\n".join(
                    [
                        "# Repo",
                        "",
                        "## Safety",
                        "",
                        "Protected paths:",
                        "- `.github/workflows/**`",
                        "- `.env`",
                        "- `secrets/**`",
                    ]
                ),
                encoding="utf-8",
            )

            resolved = resolve_bot_config(workspace, BotConfig(protected_paths=["*.pem"]))

        self.assertEqual(
            resolved.protected_paths,
            ["*.pem", ".github/workflows/**", ".env", "secrets/**"],
        )

    def test_resolve_bot_config_infers_protected_paths_from_safety_sentences(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            workspace.joinpath("AGENTS.md").write_text(
                "\n".join(
                    [
                        "# Guide",
                        "",
                        "Do not modify `.github/workflows/**` unless the issue asks for it.",
                        "Never commit `.env`, `.venv/`, or `*.pem`.",
                    ]
                ),
                encoding="utf-8",
            )

            resolved = resolve_bot_config(workspace, BotConfig(protected_paths=[]))

        self.assertEqual(
            resolved.protected_paths,
            [".github/workflows/**", ".env", ".venv/", "*.pem"],
        )

    def test_resolve_bot_config_does_not_infer_required_context_from_docs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            workspace.joinpath("README.md").write_text(
                "\n".join(
                    [
                        "# Repo",
                        "",
                        "## Required Context",
                        "",
                        "- `docs/domain.md`",
                        "- `external:product/schema.sql`",
                    ]
                ),
                encoding="utf-8",
            )

            resolved = resolve_bot_config(
                workspace,
                BotConfig(
                    context_paths=["README.md"],
                    external_context_paths=["product"],
                    required_context_paths=["EXISTING.md"],
                ),
            )

        self.assertEqual(resolved.required_context_paths, ["EXISTING.md"])
        self.assertEqual(resolved.context_paths, ["README.md"])
        self.assertEqual(resolved.external_context_paths, ["product"])

    def test_resolve_bot_config_does_not_infer_required_secret_env_from_docs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            workspace.joinpath("AGENTS.md").write_text(
                "\n".join(
                    [
                        "# Guide",
                        "",
                        "## Required Secrets",
                        "",
                        "- `DB_URL`",
                        "- `OPENAI_API_KEY`",
                    ]
                ),
                encoding="utf-8",
            )

            resolved = resolve_bot_config(
                workspace,
                BotConfig(secret_env_keys=["EXISTING_KEY"], required_secret_env=["EXISTING_KEY"]),
            )

        self.assertEqual(resolved.required_secret_env, ["EXISTING_KEY"])
        self.assertEqual(resolved.secret_env_keys, ["EXISTING_KEY"])

    def test_resolve_bot_config_does_not_treat_tutorial_variables_as_required_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            workspace.joinpath("README.md").write_text(
                "\n".join(
                    [
                        "# Repo",
                        "",
                        "## Repository variables",
                        "",
                        "- `BOT_MENTION`",
                        "- `BOT_APP_ID`",
                        "",
                        "## Repository secrets",
                        "",
                        "- `BOT_APP_PRIVATE_KEY`",
                        "",
                        "## Tutorial",
                        "",
                        "- `DB_URL` can be used in examples.",
                        "- `OPENAI_API_KEY` can be used in examples.",
                    ]
                ),
                encoding="utf-8",
            )

            resolved = resolve_bot_config(workspace, BotConfig())

        self.assertEqual(resolved.required_secret_env, [])
        self.assertEqual(resolved.secret_env_keys, [])


if __name__ == "__main__":
    unittest.main()
