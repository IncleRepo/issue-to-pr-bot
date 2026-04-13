import tempfile
import unittest
from pathlib import Path

from app.bot import IssueRequest
from app.config import BotConfig
from app.github_pr import build_pull_request_body, matches_protected_path, write_marker_file


class GitHubPrTest(unittest.TestCase):
    def test_write_marker_file_creates_issue_summary(self) -> None:
        request = IssueRequest(
            repository="IncleRepo/issue-to-pr-bot",
            issue_number=3,
            issue_title="Add sample output",
            issue_body="요구사항 본문",
            comment_body="/bot run",
            comment_author="IncleRepo",
            comment_id=99,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            output_file = write_marker_file(
                request,
                Path(temp_dir),
                BotConfig(output_dir="custom-output"),
            )
            content = output_file.read_text(encoding="utf-8")

        self.assertEqual(output_file.name, "issue-3.md")
        self.assertEqual(output_file.parent.name, "custom-output")
        self.assertIn("# Issue #3", content)
        self.assertIn("요구사항 본문", content)
        self.assertIn("bot/issue-3-comment-99-add-sample-output", content)

    def test_matches_protected_path(self) -> None:
        self.assertTrue(matches_protected_path(".github/workflows/ci.yml", ".github/workflows/**"))
        self.assertTrue(matches_protected_path("secret.pem", "*.pem"))
        self.assertFalse(matches_protected_path("README.md", ".github/workflows/**"))

    def test_build_pull_request_body_uses_template_placeholders(self) -> None:
        request = IssueRequest(
            repository="IncleRepo/issue-to-pr-bot",
            issue_number=7,
            issue_title="Add docs",
            issue_body="",
            comment_body="/bot run",
            comment_author="IncleRepo",
            comment_id=10,
        )
        config = BotConfig(
            command="/bot run",
            mode="codex",
            check_commands=["python -m compileall -q app tests", "python -m unittest discover -s tests"],
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            workspace.joinpath(".github").mkdir()
            workspace.joinpath(".github", "pull_request_template.md").write_text(
                "\n".join(
                    [
                        "## Summary",
                        "",
                        "{{CHANGED_FILES}}",
                        "",
                        "## Verification",
                        "",
                        "{{VERIFICATION_COMMANDS}}",
                        "",
                        "## Issue",
                        "",
                        "Closes #{{ISSUE_NUMBER}}",
                        "",
                        "## Notes",
                        "",
                        "- Trigger command: `{{TRIGGER_COMMAND}}`",
                        "- Bot mode: `{{BOT_MODE}}`",
                    ]
                ),
                encoding="utf-8",
            )

            body = build_pull_request_body(
                request,
                config,
                workspace,
                ["README.md", "app/main.py"],
            )

        self.assertIn("Closes #7", body)
        self.assertIn("- `README.md`", body)
        self.assertIn("- `app/main.py`", body)
        self.assertIn("- [x] `python -m compileall -q app tests`", body)
        self.assertIn("- [x] `python -m unittest discover -s tests`", body)
        self.assertIn("- Trigger command: `/bot run`", body)
        self.assertIn("- Bot mode: `codex`", body)

    def test_build_pull_request_body_falls_back_without_template(self) -> None:
        request = IssueRequest(
            repository="IncleRepo/issue-to-pr-bot",
            issue_number=8,
            issue_title="Fallback body",
            issue_body="",
            comment_body="/bot run",
            comment_author="IncleRepo",
            comment_id=11,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            body = build_pull_request_body(
                request,
                BotConfig(check_commands=["python -m unittest discover -s tests"]),
                Path(temp_dir),
                ["README.md"],
            )

        self.assertIn("## Summary", body)
        self.assertIn("Closes #8", body)
        self.assertIn("- `README.md`", body)


if __name__ == "__main__":
    unittest.main()
