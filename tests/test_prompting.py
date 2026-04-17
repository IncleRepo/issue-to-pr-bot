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

    def test_prepare_prompt_prioritizes_frontend_context_for_frontend_request(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "README.md").write_text("# Frontend guide\n", encoding="utf-8")
            (workspace / "AGENTS.md").write_text("# Agents\n", encoding="utf-8")
            (workspace / "CONTRIBUTING.md").write_text("# Contributing\n", encoding="utf-8")
            (workspace / "package.json").write_text('{"name":"demo"}\n', encoding="utf-8")
            (workspace / "pyproject.toml").write_text("[tool.ruff]\n", encoding="utf-8")
            (workspace / "examples").mkdir()
            (workspace / "examples" / "hello-world").mkdir()
            (workspace / "examples" / "hello-world" / "index.html").write_text("<h1>Hello</h1>\n", encoding="utf-8")

            request = IssueRequest(
                repository="IncleRepo/issue-to-pr-bot",
                issue_number=3,
                issue_title="HTML 페이지 수정",
                issue_body="frontend 페이지를 다듬어줘",
                comment_body="@incle-issue-to-pr-bot html 페이지 스타일을 정리해줘",
                comment_author="IncleRepo",
                comment_id=3,
            )

            prepared = prepare_prompt(request, workspace, BotConfig(), action="run")

        self.assertIn("--- package.json", prepared.repository_context)
        self.assertNotIn("--- pyproject.toml", prepared.repository_context)

    def test_prepare_prompt_prioritizes_review_path_tokens_in_project_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "AGENTS.md").write_text("# Agents\n", encoding="utf-8")
            (workspace / "README.md").write_text("# Readme\n", encoding="utf-8")
            (workspace / "src").mkdir()
            (workspace / "src" / "feature").mkdir()
            (workspace / "src" / "feature" / "widget.ts").write_text("export const x = 1;\n", encoding="utf-8")
            (workspace / "docs").mkdir()
            (workspace / "docs" / "guide.md").write_text("guide\n", encoding="utf-8")

            request = IssueRequest(
                repository="IncleRepo/issue-to-pr-bot",
                issue_number=4,
                issue_title="리뷰 반영",
                issue_body="PR body",
                comment_body="@incle-issue-to-pr-bot 이 리뷰 반영해줘",
                comment_author="reviewer",
                comment_id=4,
                is_pull_request=True,
                pull_request_number=4,
                review_path="src/feature/widget.ts",
            )

            prepared = prepare_prompt(request, workspace, BotConfig(), action="run")

        self.assertIn("src/feature/widget.ts", prepared.project_summary)

    def test_prepare_prompt_collects_issue_relevant_code_context(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "AGENTS.md").write_text("# Agents\n", encoding="utf-8")
            (workspace / "README.md").write_text("# Readme\n", encoding="utf-8")
            (workspace / "src").mkdir()
            (workspace / "src" / "battle.js").write_text(
                "export function applyDamage(player, amount) {\n"
                "  player.hp -= amount;\n"
                "}\n",
                encoding="utf-8",
            )
            (workspace / "src" / "inventory.js").write_text(
                "export function addItem(state, item) {\n"
                "  state.items.push(item);\n"
                "}\n",
                encoding="utf-8",
            )

            request = IssueRequest(
                repository="IncleRepo/-RPG",
                issue_number=5,
                issue_title="전투 데미지 표시 개선",
                issue_body="battle damage 계산과 표시를 같이 다듬어줘",
                comment_body="@incle-issue-to-pr-bot battle damage 쪽 구현 확인하고 수정해줘",
                comment_author="IncleRepo",
                comment_id=5,
            )

            prepared = prepare_prompt(request, workspace, BotConfig(), action="run")

        self.assertIn("--- src/battle.js ---", prepared.code_context)
        self.assertNotIn("--- src/inventory.js ---", prepared.code_context)
        self.assertGreater(prepared.metrics.code_context_chars, 0)
        self.assertGreater(prepared.metrics.code_context_file_count, 0)


if __name__ == "__main__":
    unittest.main()
