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

    def test_resolve_bot_config_ignores_setup_commands_in_verification_sections(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            workspace.joinpath("README.md").write_text(
                "\n".join(
                    [
                        "# Repo",
                        "",
                        "## 로컬 검증",
                        "",
                        "```powershell",
                        "python -m venv .venv",
                        "python -m unittest discover -s tests",
                        "```",
                    ]
                ),
                encoding="utf-8",
            )

            resolved = resolve_bot_config(workspace, BotConfig())

        self.assertEqual(get_check_commands(resolved), ["python -m unittest discover -s tests"])

    def test_resolve_bot_config_reads_inline_verification_commands_from_docs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            workspace.joinpath("README.md").write_text(
                "\n".join(
                    [
                        "# Repo",
                        "",
                        "## 검증",
                        "",
                        "- `python -m compileall -q app tests`",
                        "- `python -m unittest discover -s tests`",
                    ]
                ),
                encoding="utf-8",
            )

            resolved = resolve_bot_config(workspace, BotConfig())

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
                        "- Commit format: `{commit_type}(issue-{issue_number}): {issue_title}`",
                        "- PR title format: `[team-bot] #{issue_number} {issue_title}`",
                    ]
                ),
                encoding="utf-8",
            )

            resolved = resolve_bot_config(workspace, BotConfig())

        self.assertEqual(resolved.branch_name_template, "team/{issue_number}-{slug}")
        self.assertEqual(
            resolved.codex_commit_message_template,
            "{commit_type}(issue-{issue_number}): {issue_title}",
        )
        self.assertEqual(
            resolved.test_commit_message_template,
            "{commit_type}(issue-{issue_number}): {issue_title}",
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

    def test_resolve_bot_config_infers_git_sync_rule_from_agents_guidance(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            workspace.joinpath("AGENTS.md").write_text(
                "\n".join(
                    [
                        "# Guide",
                        "",
                        "## PR Checklist",
                        "",
                        "- PR \uc804 \ud604\uc7ac \ube0c\ub79c\uce58\uc5d0 develop\uc744 \ubcd1\ud569\ud558\uc5ec \ucda9\ub3cc\uc774 \uc5c6\ub294\uc9c0 \ud655\uc778\ud558\uc600\ub294\uac00",
                    ]
                ),
                encoding="utf-8",
            )

            resolved = resolve_bot_config(workspace, BotConfig())

        self.assertIsNotNone(resolved.git_sync_rule)
        self.assertEqual(resolved.default_base_branch, "develop")
        self.assertEqual(resolved.git_sync_rule.phase, "before_pr")
        self.assertEqual(resolved.git_sync_rule.action, "merge")
        self.assertEqual(resolved.git_sync_rule.base_branch, "develop")
        self.assertTrue(resolved.git_sync_rule.require_conflict_free)
        self.assertEqual(resolved.git_sync_rule.confidence, "high")

    def test_resolve_bot_config_infers_rebase_main_from_pr_guidance_variation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            workspace.joinpath("AGENTS.md").write_text(
                "\n".join(
                    [
                        "# Guide",
                        "",
                        "## PR 작성 규칙",
                        "",
                        "- PR을 올리기 전에 최신 `main` 기준으로 rebase 한 뒤 전반적인 구현을 한 번 더 확인합니다.",
                    ]
                ),
                encoding="utf-8",
            )

            resolved = resolve_bot_config(workspace, BotConfig())

        self.assertIsNotNone(resolved.git_sync_rule)
        self.assertEqual(resolved.default_base_branch, "main")
        self.assertEqual(resolved.git_sync_rule.phase, "before_pr")
        self.assertEqual(resolved.git_sync_rule.action, "rebase")
        self.assertEqual(resolved.git_sync_rule.base_branch, "main")

    def test_resolve_bot_config_infers_before_merge_rule_alongside_before_pr_rule(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            workspace.joinpath("AGENTS.md").write_text(
                "\n".join(
                    [
                        "# Guide",
                        "",
                        "## PR 작성 규칙",
                        "",
                        "- PR을 올리기 전에 최신 `main` 기준으로 rebase 한 뒤 전반적인 구현을 한 번 더 확인합니다.",
                        "- 브랜치를 merge 하기 전에는 최신 `main`을 merge 한 뒤 전반적인 구현을 한 번 더 확인합니다.",
                    ]
                ),
                encoding="utf-8",
            )

            resolved = resolve_bot_config(workspace, BotConfig())

        self.assertEqual(resolved.default_base_branch, "main")
        self.assertEqual(resolved.git_sync_rule.phase, "before_pr")
        self.assertEqual(len(resolved.git_sync_rules), 2)
        before_merge_rule = next(rule for rule in resolved.git_sync_rules if rule.phase == "before_merge")
        self.assertEqual(before_merge_rule.action, "merge")
        self.assertEqual(before_merge_rule.base_branch, "main")

    def test_resolve_bot_config_infers_examples_from_realistic_agents_document(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            workspace.joinpath("AGENTS.md").write_text(
                "\n".join(
                    [
                        "# AGENTS.md",
                        "",
                        "## 작업 절차",
                        "",
                        "7. PR을 올리기 전에 최신 `main`을 merge 합니다.",
                        "8. merge 이후 전반적인 구현을 한 번 더 살펴보고 필요한 확인을 마칩니다.",
                        "9. PR을 생성합니다.",
                        "",
                        "## 브랜치 네이밍",
                        "",
                        "```text",
                        "feat/12-player-move",
                        "fix/21-map-collision",
                        "docs/30-readme-update",
                        "chore/41-tooling-cleanup",
                        "```",
                        "",
                        "## 커밋 메시지",
                        "",
                        "```text",
                        "feat: 플레이어 이동 추가",
                        "fix: 충돌 판정 수정",
                        "docs: README 정리",
                        "```",
                        "",
                        "## PR 작성 규칙",
                        "",
                        "- PR 전 아래 항목을 확인합니다.",
                        "",
                        "```text",
                        "npm run lint",
                        "npm run format:check",
                        "```",
                    ]
                ),
                encoding="utf-8",
            )

            resolved = resolve_bot_config(workspace, BotConfig())

        self.assertEqual(resolved.branch_name_template, "{commit_type}/{issue_number}-{slug}")
        self.assertEqual(resolved.codex_commit_message_template, "{commit_type}: {issue_title}")
        self.assertEqual(resolved.test_commit_message_template, "{commit_type}: {issue_title}")
        self.assertEqual(resolved.default_base_branch, "main")
        self.assertEqual(get_check_commands(resolved), ["npm run lint", "npm run format:check"])


if __name__ == "__main__":
    unittest.main()
