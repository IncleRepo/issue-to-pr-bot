from pathlib import Path

import unittest


class ReviewWorkflowTest(unittest.TestCase):
    def test_review_workflow_runs_for_approved_reviews_or_mentions(self) -> None:
        content = Path(".github/workflows/pull-request-review.yml").read_text(encoding="utf-8")

        self.assertIn("pull_request_review:", content)
        self.assertIn("github.event.review.state == 'approved'", content)
        self.assertIn("contains(github.event.review.body, vars.BOT_MENTION)", content)
        self.assertIn("uses: ./.github/workflows/reusable-bot.yml", content)

    def test_review_workflow_template_targets_public_engine_repository(self) -> None:
        content = Path("templates/pull-request-review.yml.example").read_text(encoding="utf-8")

        self.assertIn("pull_request_review:", content)
        self.assertIn("contains(github.event.review.body, vars.BOT_MENTION)", content)
        self.assertIn("IncleRepo/issue-to-pr-bot/.github/workflows/reusable-bot.yml@main", content)


if __name__ == "__main__":
    unittest.main()
