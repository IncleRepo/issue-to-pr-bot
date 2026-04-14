import tempfile
import unittest
from pathlib import Path

from app.domain.models import IssueRequest
from app.metadata_rules import infer_issue_metadata, infer_pull_request_metadata


class MetadataRulesTest(unittest.TestCase):
    def make_request(self, title: str, body: str = "", comment: str = "@incle-issue-to-pr-bot 처리해줘") -> IssueRequest:
        return IssueRequest(
            repository="IncleRepo/issue-to-pr-bot",
            issue_number=12,
            issue_title=title,
            issue_body=body,
            comment_body=comment,
            comment_author="IncleRepo",
            comment_id=99,
        )

    def test_infer_issue_metadata_from_documents(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            workspace.joinpath("AGENTS.md").write_text(
                "\n".join(
                    [
                        "# Guide",
                        "",
                        "Labels: `automation`, `bot`",
                        "Issue labels: `bug`",
                        "Assignees: `@alice`",
                        "Milestone: `Sprint 1`",
                    ]
                ),
                encoding="utf-8",
            )

            metadata = infer_issue_metadata(workspace, self.make_request("Fix broken workflow", "버그 수정"))

        self.assertEqual(metadata.issue_labels, ["bug", "automation", "bot"])
        self.assertEqual(metadata.assignees, ["@alice"])
        self.assertEqual(metadata.milestone_title, "Sprint 1")

    def test_infer_pull_request_metadata_uses_codeowners_and_fallbacks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            workspace.joinpath("AGENTS.md").write_text(
                "\n".join(
                    [
                        "# Guide",
                        "",
                        "PR labels: `bot`",
                        "Reviewers: `@reviewer1`",
                        "Team reviewers: `@org/platform`",
                    ]
                ),
                encoding="utf-8",
            )
            workspace.joinpath(".github").mkdir()
            workspace.joinpath(".github", "CODEOWNERS").write_text(
                "\n".join(
                    [
                        "docs/* @doc-owner",
                        "app/* @org/backend @service-owner",
                    ]
                ),
                encoding="utf-8",
            )

            metadata = infer_pull_request_metadata(
                workspace,
                self.make_request("README 문서 수정", "문서 개선"),
                ["README.md", "app/main.py", "docs/guide.md"],
            )

        self.assertIn("bot", metadata.pr_labels)
        self.assertIn("documentation", metadata.pr_labels)
        self.assertIn("@reviewer1", metadata.reviewers)
        self.assertIn("service-owner", metadata.reviewers)
        self.assertIn("@org/platform", metadata.team_reviewers)
        self.assertIn("org/backend", metadata.team_reviewers)


if __name__ == "__main__":
    unittest.main()
