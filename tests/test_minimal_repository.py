import tempfile
import unittest
from pathlib import Path

from app.bot import build_task_prompt, parse_bot_command, resolve_runtime_options
from app.config import BotConfig, get_check_commands, load_config
from app.repo_context import collect_context_documents, collect_project_summary, format_context_documents
from app.repo_rules import resolve_bot_config


class MinimalRepositoryTest(unittest.TestCase):
    def test_minimal_repository_works_without_issue_to_pr_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            workspace.joinpath("README.md").write_text(
                "# Sample Repo\n\n간단한 설명만 있는 저장소",
                encoding="utf-8",
            )
            workspace.joinpath("app").mkdir()
            workspace.joinpath("app", "main.py").write_text("print('hello')\n", encoding="utf-8")
            workspace.joinpath(".github").mkdir()
            workspace.joinpath(".github", "pull_request_template.md").write_text(
                "Closes #{{ISSUE_NUMBER}}",
                encoding="utf-8",
            )

            config = resolve_bot_config(workspace, load_config(workspace))
            command = parse_bot_command(
                "@incle-issue-to-pr-bot 이 저장소 작업해줘. codex high로 해줘.",
                config,
            )
            documents = collect_context_documents(workspace, config)
            repository_context = format_context_documents(documents)
            project_summary = collect_project_summary(workspace)

        self.assertEqual(config, BotConfig())
        self.assertEqual(get_check_commands(config), ["python -m unittest discover -s tests"])
        self.assertIsNotNone(command)
        assert command is not None

        runtime_options = resolve_runtime_options(command, config)
        prompt = build_task_prompt(
            request=_build_request(),
            config=config,
            repository_context=repository_context,
            project_summary=project_summary,
            available_secret_keys=[],
            attachment_context=None,
        )

        self.assertEqual(runtime_options.mode, "codex")
        self.assertEqual(runtime_options.provider, "codex")
        self.assertEqual(runtime_options.effort, "high")
        self.assertTrue(runtime_options.verify)
        self.assertEqual([document.path for document in documents], ["README.md", ".github/pull_request_template.md"])
        self.assertIn("README.md", prompt)
        self.assertIn(".github/pull_request_template.md", prompt)
        self.assertIn("- app/", project_summary)
        self.assertIn("- app/main.py", project_summary)


def _build_request():
    from app.bot import IssueRequest

    return IssueRequest(
        repository="example/minimal-repo",
        issue_number=1,
        issue_title="Minimal repository test",
        issue_body="설정 파일 없이도 동작해야 한다.",
        comment_body="@incle-issue-to-pr-bot 이 저장소 작업해줘. codex high로 해줘.",
        comment_author="tester",
        comment_id=1,
    )


if __name__ == "__main__":
    unittest.main()
