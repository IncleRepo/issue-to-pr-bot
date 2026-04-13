import tempfile
import unittest
from pathlib import Path

from app.bot import IssueRequest
from app.config import BotConfig
from app.prompting import MAX_PROMPT_CHARS, prepare_prompt


class PromptingTest(unittest.TestCase):
    def test_prepare_prompt_applies_budget_and_collects_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "README.md").write_text("# Title\n\n" + ("README line\n" * 400), encoding="utf-8")
            (workspace / "AGENTS.md").write_text("# Agents\n\n" + ("Agent rule\n" * 400), encoding="utf-8")
            (workspace / "CONTRIBUTING.md").write_text("# Contributing\n\n" + ("Contribution rule\n" * 400), encoding="utf-8")
            (workspace / "app").mkdir()
            (workspace / "app" / "main.py").write_text("print('hello')\n", encoding="utf-8")

            request = IssueRequest(
                repository="IncleRepo/issue-to-pr-bot",
                issue_number=1,
                issue_title="README update",
                issue_body="Please update the docs.",
                comment_body="@incle-issue-to-pr-bot README 문구만 다듬어줘",
                comment_author="IncleRepo",
                comment_id=1,
            )

            prepared = prepare_prompt(request, workspace, BotConfig(), action="run")

        self.assertLessEqual(len(prepared.prompt), MAX_PROMPT_CHARS)
        self.assertGreater(prepared.metrics.document_count, 0)
        self.assertGreater(prepared.metrics.selected_document_count, 0)
        self.assertGreater(prepared.metrics.prompt_chars, 0)

    def test_prepare_prompt_prioritizes_fewer_docs_for_pull_request_feedback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "README.md").write_text("# Title\n", encoding="utf-8")
            (workspace / "AGENTS.md").write_text("# Agents\n", encoding="utf-8")
            (workspace / "CONTRIBUTING.md").write_text("# Contributing\n", encoding="utf-8")
            (workspace / ".github").mkdir()
            (workspace / ".github" / "pull_request_template.md").write_text("Template", encoding="utf-8")
            (workspace / "app").mkdir()
            (workspace / "app" / "main.py").write_text("print('hello')\n", encoding="utf-8")

            request = IssueRequest(
                repository="IncleRepo/issue-to-pr-bot",
                issue_number=2,
                issue_title="Reflect review feedback",
                issue_body="PR body",
                comment_body="@incle-issue-to-pr-bot 이 리뷰 반영해줘",
                comment_author="reviewer",
                comment_id=2,
                is_pull_request=True,
                pull_request_number=2,
                review_path="app/main.py",
            )

            prepared = prepare_prompt(request, workspace, BotConfig(), action="run")

        self.assertLessEqual(prepared.metrics.selected_document_count, 4)


if __name__ == "__main__":
    unittest.main()
