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


if __name__ == "__main__":
    unittest.main()
