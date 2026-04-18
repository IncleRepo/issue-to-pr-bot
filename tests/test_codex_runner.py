import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.attachments import AttachmentContext
from app.bot import BotRuntimeOptions, IssueRequest
from app.codex_runner import create_codex_pr
from app.codex_provider import (
    LATEST_CODEX_MODEL,
    build_codex_command,
    build_codex_resume_command,
    build_codex_environment,
    can_force_codex_completion,
    classify_codex_output,
    get_effort,
    has_usable_last_message,
    interrupt_active_codex_process,
    stream_codex_output,
)
from app.github_pr import CheckoutTarget, PullRequestResult
from app.config import BotConfig
from app.prompting import PreparedPrompt, PromptMetrics


class CodexRunnerTest(unittest.TestCase):
    def test_build_codex_command_uses_persistent_noninteractive_exec(self) -> None:
        workspace = Path("/workspace")
        with patch("app.codex_provider.resolve_codex_executable", return_value="codex"):
            command = build_codex_command(workspace)

        self.assertEqual(
            command[:8],
            [
                "codex",
                "--dangerously-bypass-approvals-and-sandbox",
                "-C",
                str(workspace),
                "exec",
                "--model",
                LATEST_CODEX_MODEL,
                "-",
            ],
        )
        self.assertNotIn("--full-auto", command)
        self.assertNotIn("-s", command)
        self.assertNotIn("-a", command)

    def test_build_codex_resume_command_uses_last_session(self) -> None:
        workspace = Path("/workspace")
        with patch("app.codex_provider.resolve_codex_executable", return_value="codex"):
            command = build_codex_resume_command(workspace)

        self.assertEqual(
            command[:7],
            [
                "codex",
                "--dangerously-bypass-approvals-and-sandbox",
                "-C",
                str(workspace),
                "exec",
                "resume",
                "--last",
            ],
        )
        self.assertEqual(command[command.index("--model") + 1], LATEST_CODEX_MODEL)
        self.assertNotIn("--full-auto", command)
        self.assertNotIn("-s", command)
        self.assertNotIn("-a", command)
        self.assertEqual(command[-1], "-")

    def test_build_codex_command_can_write_last_message(self) -> None:
        output_path = Path("/tmp/last-message.txt")
        with patch("app.codex_provider.resolve_codex_executable", return_value="codex"):
            command = build_codex_command(Path("/workspace"), output_last_message=output_path)

        self.assertEqual(command[command.index("--output-last-message") + 1], str(output_path))
        self.assertEqual(command[-1], "-")

    def test_build_codex_command_can_set_effort(self) -> None:
        with patch("app.codex_provider.resolve_codex_executable", return_value="codex"):
            command = build_codex_command(Path("/workspace"), effort="high")

        self.assertIn("-c", command)
        self.assertIn('reasoning_effort="high"', command)

    def test_resolve_codex_executable_prefers_user_installed_windows_cli(self) -> None:
        with patch("app.codex_provider.os.name", "nt"), patch(
            "app.codex_provider.shutil_module.which",
            side_effect=lambda name: {
                "codex.exe": r"C:\\Users\\me\\.vscode\\extensions\\openai.chatgpt\\bin\\windows-x86_64\\codex.exe",
                "codex.cmd": r"C:\\Users\\me\\AppData\\Roaming\\npm\\codex.cmd",
            }.get(name),
        ):
            from app.codex_provider import resolve_codex_executable

            self.assertEqual(
                resolve_codex_executable(),
                r"C:\\Users\\me\\AppData\\Roaming\\npm\\codex.cmd",
            )

    def test_get_effort_validates_values(self) -> None:
        runtime_options = BotRuntimeOptions(mode="codex", provider="codex", verify=True, effort="xhigh")
        invalid_runtime_options = BotRuntimeOptions(mode="codex", provider="codex", verify=True, effort="turbo")

        self.assertEqual(get_effort(None, runtime_options), "xhigh")
        with self.assertRaises(ValueError):
            get_effort(None, invalid_runtime_options)

    def test_classify_codex_output_detects_activity(self) -> None:
        self.assertEqual(classify_codex_output("Thinking about the implementation"), "\ubd84\uc11d \uc911")
        self.assertEqual(classify_codex_output("Get-Content app/main.py"), "\ucf54\ub4dc/\ud30c\uc77c \ud655\uc778 \uc911")
        self.assertEqual(classify_codex_output("*** Update File: app/main.py"), "\ud30c\uc77c \uc218\uc815 \uc911")
        self.assertEqual(classify_codex_output("npm run lint"), "\uba85\ub839 \uc2e4\ud589 \uc911")

    def test_stream_codex_output_emits_status_and_lines(self) -> None:
        class FakeProcess:
            def __init__(self, text: str) -> None:
                self.stdout = io.StringIO(text)
                self.returncode = 0

            def poll(self):
                return self.returncode

        fake_process = FakeProcess("Thinking about fix\nGet-Content app/main.py\n")

        with patch("builtins.print") as print_mock:
            result = stream_codex_output(fake_process, started_at=0.0, heartbeat_seconds=999.0)

        self.assertEqual(result.output, "Thinking about fix\nGet-Content app/main.py\n")
        self.assertFalse(result.forced_completion)
        printed = [call.args[0] for call in print_mock.call_args_list]
        self.assertIn("[codex-status] \ubd84\uc11d \uc911", printed)
        self.assertIn("Thinking about fix", printed)
        self.assertIn("[codex-status] \ucf54\ub4dc/\ud30c\uc77c \ud655\uc778 \uc911", printed)
        self.assertIn("Get-Content app/main.py", printed)

    def test_can_force_codex_completion_requires_nonempty_last_message(self) -> None:
        with self.subTest("missing file"):
            self.assertFalse(can_force_codex_completion(None, quiet_seconds=30))

        with self.subTest("existing file"):
            with tempfile.TemporaryDirectory() as temp_dir:
                output_path = Path(temp_dir) / "last-message.txt"
                output_path.write_text("done", encoding="utf-8")

                self.assertTrue(has_usable_last_message(output_path))
                self.assertTrue(can_force_codex_completion(output_path, quiet_seconds=30))
                self.assertFalse(can_force_codex_completion(output_path, quiet_seconds=5, grace_seconds=20))

    def test_build_codex_environment_removes_github_auth(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "BOT_GITHUB_TOKEN": "bot-token",
                "GITHUB_TOKEN": "github-token",
                "GH_TOKEN": "gh-token",
                "EXTRA_VAR": "keep-me",
            },
            clear=False,
        ):
            env = build_codex_environment("C:\\temp\\gh-config", "C:\\temp\\codex-home\\.codex")

        self.assertNotIn("BOT_GITHUB_TOKEN", env)
        self.assertNotIn("GITHUB_TOKEN", env)
        self.assertNotIn("GH_TOKEN", env)
        self.assertEqual(env["GH_CONFIG_DIR"], "C:\\temp\\gh-config")
        self.assertEqual(env["CODEX_HOME"], "C:\\temp\\codex-home\\.codex")
        self.assertEqual(env["HOME"], "C:\\temp\\codex-home")
        self.assertEqual(env["USERPROFILE"], "C:\\temp\\codex-home")
        self.assertEqual(env["GIT_TERMINAL_PROMPT"], "0")
        self.assertEqual(env["NO_COLOR"], "1")
        self.assertEqual(env["EXTRA_VAR"], "keep-me")

    def test_interrupt_active_codex_process_terminates_running_process(self) -> None:
        class FakeProcess:
            def __init__(self) -> None:
                self.terminated = False

            def poll(self):
                return None

            def terminate(self) -> None:
                self.terminated = True

            def wait(self, timeout=None) -> int:
                return 0

        fake_process = FakeProcess()

        with patch("app.codex_provider.ACTIVE_CODEX_PROCESS", fake_process):
            interrupted = interrupt_active_codex_process()

        self.assertTrue(interrupted)
        self.assertTrue(fake_process.terminated)

    def test_create_codex_pr_does_not_force_pr_sync_before_running_codex(self) -> None:
        request = IssueRequest(
            repository="IncleRepo/issue-to-pr-bot",
            issue_number=12,
            issue_title="Resolve conflicts",
            issue_body="",
            comment_body="@incle-issue-to-pr-bot sync with main and resolve conflict.",
            comment_author="IncleRepo",
            comment_id=1,
            is_pull_request=True,
            pull_request_number=12,
        )
        runtime_options = BotRuntimeOptions(mode="codex", provider="codex", verify=False, sync_base=True)

        with (
            patch(
                "app.codex_runner.checkout_request_target",
                return_value=CheckoutTarget(branch_name="bot/pr-12", base_branch="main"),
            ),
            patch("app.codex_runner.run_codex"),
            patch(
                "app.codex_runner.commit_push_and_open_pr",
                return_value=PullRequestResult(
                    branch_name="bot/pr-12",
                    pull_request_url="https://example.com/pr/12",
                    created=True,
                    changed_files=["app/main.py"],
                ),
            ),
        ):
            result = create_codex_pr(request, Path("."), BotConfig(), runtime_options=runtime_options)

        self.assertEqual(result.branch_name, "bot/pr-12")

    def test_create_codex_pr_does_not_apply_documented_publish_sync(self) -> None:
        request = IssueRequest(
            repository="IncleRepo/sample-repo",
            issue_number=9,
            issue_title="Implement feature",
            issue_body="",
            comment_body="@incle-issue-to-pr-bot 구현해줘",
            comment_author="IncleRepo",
            comment_id=99,
        )
        prepared_prompt = PreparedPrompt(
            prompt="original prompt",
            attachment_info=AttachmentContext(attachments=[], skipped=[]),
            available_secret_keys=[],
            repository_context="repo",
            project_summary="summary",
            code_context="code",
            attachment_context="attachments",
            metrics=PromptMetrics(
                action="run",
                prompt_chars=10,
                document_count=1,
                selected_document_count=1,
                repository_context_chars=4,
                project_summary_chars=7,
                code_context_chars=4,
                code_context_file_count=1,
                attachment_context_chars=11,
                attachment_count=0,
                skipped_attachment_count=0,
                secret_key_count=0,
                collection_seconds=0.01,
            ),
        )
        config = BotConfig(default_base_branch="develop")

        with (
            patch(
                "app.codex_runner.checkout_request_target",
                return_value=CheckoutTarget(branch_name="bot/issue-9", base_branch="develop"),
            ),
            patch("app.codex_runner.run_codex") as run_codex_mock,
            patch("app.codex_runner.resolve_verification_plan") as verification_plan_mock,
            patch(
                "app.codex_runner.commit_push_and_open_pr",
                return_value=PullRequestResult(
                    branch_name="bot/issue-9",
                    pull_request_url="https://example.com/pr/9",
                    created=True,
                    changed_files=["app/main.py"],
                ),
            ),
        ):
            verification_plan_mock.return_value = type(
                "Plan",
                (),
                {"commands": [], "profile": "default", "changed_files": []},
            )()
            create_codex_pr(
                request,
                Path("."),
                config,
                runtime_options=BotRuntimeOptions(mode="codex", provider="codex", verify=True),
                prepared_prompt=prepared_prompt,
            )

        run_codex_mock.assert_called_once()
        first_prompt = run_codex_mock.call_args.args[5]
        self.assertEqual(first_prompt.prompt, "original prompt")

    def test_create_codex_pr_uses_single_codex_run_for_existing_pull_request(self) -> None:
        request = IssueRequest(
            repository="IncleRepo/sample-repo",
            issue_number=11,
            issue_title="Follow-up review changes",
            issue_body="",
            comment_body="@incle-issue-to-pr-bot 리뷰 반영해줘",
            comment_author="IncleRepo",
            comment_id=101,
            is_pull_request=True,
            pull_request_number=11,
        )
        config = BotConfig(default_base_branch="main")

        with (
            patch(
                "app.codex_runner.checkout_request_target",
                return_value=CheckoutTarget(
                    branch_name="feat/11-follow-up",
                    base_branch="main",
                    pull_request_number=11,
                    pull_request_url="https://example.com/pr/11",
                ),
            ),
            patch("app.codex_runner.run_codex") as run_codex_mock,
            patch("app.codex_runner.resolve_verification_plan") as verification_plan_mock,
            patch(
                "app.codex_runner.commit_push_and_open_pr",
                return_value=PullRequestResult(
                    branch_name="feat/11-follow-up",
                    pull_request_url="https://example.com/pr/11",
                    created=True,
                    changed_files=["src/main.js"],
                ),
            ),
        ):
            verification_plan_mock.return_value = type(
                "Plan",
                (),
                {"commands": [], "profile": "default", "changed_files": []},
            )()
            create_codex_pr(
                request,
                Path("."),
                config,
                runtime_options=BotRuntimeOptions(mode="codex", provider="codex", verify=True),
            )

        run_codex_mock.assert_called_once()

    def test_create_codex_pr_retries_once_when_codex_skips_local_commit(self) -> None:
        request = IssueRequest(
            repository="IncleRepo/sample-repo",
            issue_number=28,
            issue_title="Upgrade character quality",
            issue_body="",
            comment_body="@incle-issue-to-pr-bot 다시 새로 구현부탁해",
            comment_author="IncleRepo",
            comment_id=401,
        )
        prepared_prompt = PreparedPrompt(
            prompt="original prompt",
            attachment_info=AttachmentContext(attachments=[], skipped=[]),
            available_secret_keys=[],
            repository_context="repo",
            project_summary="summary",
            code_context="code",
            attachment_context="attachments",
            metrics=PromptMetrics(
                action="run",
                prompt_chars=10,
                document_count=1,
                selected_document_count=1,
                repository_context_chars=4,
                project_summary_chars=7,
                code_context_chars=4,
                code_context_file_count=1,
                attachment_context_chars=11,
                attachment_count=0,
                skipped_attachment_count=0,
                secret_key_count=0,
                collection_seconds=0.01,
            ),
        )

        with (
            patch(
                "app.codex_runner.checkout_request_target",
                return_value=CheckoutTarget(branch_name="feat/28-feature", base_branch="main"),
            ),
            patch("app.codex_runner.run_codex") as run_codex_mock,
            patch("app.codex_runner.resolve_verification_plan") as verification_plan_mock,
            patch(
                "app.codex_runner.commit_push_and_open_pr",
                side_effect=[
                    RuntimeError(
                        "Codex finished with local changes but no local commit. "
                        "Create the publishable commit inside the workspace before the wrapper pushes."
                    ),
                    PullRequestResult(
                        branch_name="feat/28-feature",
                        pull_request_url="https://example.com/pr/28",
                        created=True,
                        changed_files=["src/main.js"],
                    ),
                ],
            ) as publish_mock,
        ):
            verification_plan_mock.return_value = type(
                "Plan",
                (),
                {"commands": [], "profile": "default", "changed_files": []},
            )()
            result = create_codex_pr(
                request,
                Path("."),
                BotConfig(default_base_branch="main"),
                runtime_options=BotRuntimeOptions(mode="codex", provider="codex", verify=True),
                prepared_prompt=prepared_prompt,
            )

        self.assertEqual(result.branch_name, "feat/28-feature")
        self.assertEqual(run_codex_mock.call_count, 2)
        retry_prompt = run_codex_mock.call_args_list[1].args[5]
        self.assertIn("without creating the required local commit", retry_prompt.prompt)
        self.assertEqual(publish_mock.call_count, 2)

    def test_create_codex_pr_retries_once_when_publish_diff_contains_workspace_only_files(self) -> None:
        request = IssueRequest(
            repository="IncleRepo/sample-repo",
            issue_number=29,
            issue_title="Keep workspace scratch files out of PR",
            issue_body="",
            comment_body="@incle-issue-to-pr-bot 구현해줘",
            comment_author="IncleRepo",
            comment_id=402,
        )
        prepared_prompt = PreparedPrompt(
            prompt="original prompt",
            attachment_info=AttachmentContext(attachments=[], skipped=[]),
            available_secret_keys=[],
            repository_context="repo",
            project_summary="summary",
            code_context="code",
            attachment_context="attachments",
            metrics=PromptMetrics(
                action="run",
                prompt_chars=10,
                document_count=1,
                selected_document_count=1,
                repository_context_chars=4,
                project_summary_chars=7,
                code_context_chars=4,
                code_context_file_count=1,
                attachment_context_chars=11,
                attachment_count=0,
                skipped_attachment_count=0,
                secret_key_count=0,
                collection_seconds=0.01,
            ),
        )

        with (
            patch(
                "app.codex_runner.checkout_request_target",
                return_value=CheckoutTarget(branch_name="feat/29-feature", base_branch="main"),
            ),
            patch("app.codex_runner.run_codex") as run_codex_mock,
            patch("app.codex_runner.resolve_verification_plan") as verification_plan_mock,
            patch(
                "app.codex_runner.commit_push_and_open_pr",
                side_effect=[
                    RuntimeError(
                        "Non-publishable workspace files are present in the publishable diff.\n"
                        "Remove these workspace-only files from the publishable commit history before the wrapper pushes:\n"
                        "- .issue-to-pr-bot/output/pr-body.md"
                    ),
                    PullRequestResult(
                        branch_name="feat/29-feature",
                        pull_request_url="https://example.com/pr/29",
                        created=True,
                        changed_files=["src/main.js"],
                    ),
                ],
            ) as publish_mock,
        ):
            verification_plan_mock.return_value = type(
                "Plan",
                (),
                {"commands": [], "profile": "default", "changed_files": []},
            )()
            result = create_codex_pr(
                request,
                Path("."),
                BotConfig(default_base_branch="main"),
                runtime_options=BotRuntimeOptions(mode="codex", provider="codex", verify=True),
                prepared_prompt=prepared_prompt,
            )

        self.assertEqual(result.branch_name, "feat/29-feature")
        self.assertEqual(run_codex_mock.call_count, 2)
        retry_prompt = run_codex_mock.call_args_list[1].args[5]
        self.assertIn("Workspace-only scratch files were found in the publishable diff", retry_prompt.prompt)
        self.assertIn(".issue-to-pr-bot/output/pr-body.md", retry_prompt.prompt)
        self.assertEqual(publish_mock.call_count, 2)


if __name__ == "__main__":
    unittest.main()
