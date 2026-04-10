import tempfile
import unittest
from pathlib import Path

from app.config import BotConfig
from app.repo_context import collect_context_documents, collect_project_summary, format_context_documents


class RepoContextTest(unittest.TestCase):
    def test_collect_context_documents_reads_configured_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            workspace.joinpath("README.md").write_text("Project guide", encoding="utf-8")
            workspace.joinpath("docs").mkdir()
            workspace.joinpath("docs", "rules.md").write_text("Team rules", encoding="utf-8")

            documents = collect_context_documents(
                workspace,
                BotConfig(context_paths=["README.md", "docs"]),
            )

        self.assertEqual([document.path for document in documents], ["README.md", "docs/rules.md"])
        self.assertEqual(documents[0].content, "Project guide")

    def test_format_context_documents(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            workspace.joinpath("README.md").write_text("Project guide", encoding="utf-8")
            documents = collect_context_documents(workspace, BotConfig(context_paths=["README.md"]))

        formatted = format_context_documents(documents)

        self.assertIn("--- README.md ---", formatted)
        self.assertIn("Project guide", formatted)

    def test_collect_project_summary_skips_generated_directories(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            workspace.joinpath("app").mkdir()
            workspace.joinpath("app", "main.py").write_text("print('hi')", encoding="utf-8")
            workspace.joinpath(".venv").mkdir()
            workspace.joinpath(".venv", "ignored.py").write_text("ignored", encoding="utf-8")

            summary = collect_project_summary(workspace)

        self.assertIn("- app/", summary)
        self.assertIn("- app/main.py", summary)
        self.assertNotIn(".venv", summary)


if __name__ == "__main__":
    unittest.main()
