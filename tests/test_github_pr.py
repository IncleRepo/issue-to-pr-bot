import tempfile
import unittest
from pathlib import Path

from app.bot import IssueRequest
from app.config import BotConfig
from app.github_pr import write_marker_file


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


if __name__ == "__main__":
    unittest.main()
