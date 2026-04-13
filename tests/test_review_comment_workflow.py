from pathlib import Path

import unittest


class ReviewCommentWorkflowTest(unittest.TestCase):
    def test_review_comment_workflow_uses_pull_request_review_comment_event(self) -> None:
        content = Path(".github/workflows/pull-request-review-comment.yml").read_text(encoding="utf-8")

        self.assertIn("pull_request_review_comment:", content)
        self.assertIn("contains(github.event.comment.body, vars.BOT_MENTION)", content)
        self.assertIn("uses: ./.github/workflows/reusable-bot.yml", content)

    def test_review_comment_template_targets_public_engine_repository(self) -> None:
        content = Path("templates/pull-request-review-comment.yml.example").read_text(encoding="utf-8")

        self.assertIn("pull_request_review_comment:", content)
        self.assertIn("contains(github.event.comment.body, vars.BOT_MENTION)", content)
        self.assertIn("IncleRepo/issue-to-pr-bot/.github/workflows/reusable-bot.yml@main", content)


if __name__ == "__main__":
    unittest.main()
