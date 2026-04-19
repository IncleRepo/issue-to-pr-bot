import subprocess
import tempfile
import unittest
from os import environ
from pathlib import Path
from unittest.mock import Mock, patch

from app.bot import IssueRequest
from app.config import BotConfig
from app.github_pr import (
    BOT_AUTO_MERGE_MARKER,
    BOT_PR_MARKER,
    BaseSyncResult,
    apply_base_sync_strategy,
    apply_issue_metadata,
    apply_pull_request_metadata,
    branch_has_publishable_commits,
    build_pull_request_body,
    commit_push_and_open_pr,
    create_issue_comment,
    ensure_pull_request,
    get_workspace_changed_files,
    matches_protected_path,
    request_pull_request_merge,
    run_git,
    sync_pull_request_branch_with_base,
    write_marker_file,
)
from app.domain.models import MetadataPlan
from app.output_artifacts import (
    get_legacy_task_output_root,
    get_pr_body_draft_path,
    get_pr_summary_draft_path,
    get_pr_title_draft_path,
)


class GitHubPrTest(unittest.TestCase):
    @patch("app.github_pr.subprocess.run")
    def test_run_git_uses_utf8_replace_for_git_output(self, subprocess_run_mock) -> None:
        subprocess_run_mock.return_value = subprocess.CompletedProcess(
            args=["git"],
            returncode=0,
            stdout="ok\n",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            run_git(["status", "--short"], Path(temp_dir))

        _, kwargs = subprocess_run_mock.call_args
        self.assertEqual(kwargs["encoding"], "utf-8")
        self.assertEqual(kwargs["errors"], "replace")

    def test_write_marker_file_creates_issue_summary(self) -> None:
        request = IssueRequest(
            repository="IncleRepo/issue-to-pr-bot",
            issue_number=3,
            issue_title="Add sample output",
            issue_body="요구사항 본문",
            comment_body="@incle-issue-to-pr-bot 샘플 출력 추가해줘",
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

    @patch("app.github_pr.subprocess.run")
    def test_get_workspace_changed_files_ignores_output_artifacts(self, subprocess_run_mock) -> None:
        subprocess_run_mock.return_value = subprocess.CompletedProcess(
            args=["git"],
            returncode=0,
            stdout=(
                "?? bot-output/pr-body.md\n"
                "?? .issue-to-pr-bot/output/pr-body.md\n"
                "?? Microsoft/\n"
                " M src/main.js\n"
            ),
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            changed_files = get_workspace_changed_files(Path(temp_dir))

        self.assertEqual(changed_files, ["src/main.js"])

    def test_build_pull_request_body_uses_template_placeholders(self) -> None:
        request = IssueRequest(
            repository="IncleRepo/issue-to-pr-bot",
            issue_number=7,
            issue_title="Add docs",
            issue_body="",
            comment_body="@incle-issue-to-pr-bot README 수정해줘. codex high로.",
            comment_author="IncleRepo",
            comment_id=10,
        )
        config = BotConfig(
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
                        "- Trigger comment: `{{TRIGGER_COMMAND}}`",
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
                ["python -m compileall -q app tests"],
            )

        self.assertIn("Closes #7", body)
        self.assertIn("- `README.md`", body)
        self.assertIn("- `app/main.py`", body)
        self.assertIn("- [x] `python -m compileall -q app tests`", body)
        self.assertNotIn("- [x] `python -m unittest discover -s tests`", body)
        self.assertIn("- Trigger comment: `@incle-issue-to-pr-bot README 수정해줘. codex high로.`", body)
        self.assertIn("- Bot mode: `codex`", body)
        self.assertIn(BOT_PR_MARKER, body)

    def test_build_pull_request_body_falls_back_without_template(self) -> None:
        request = IssueRequest(
            repository="IncleRepo/issue-to-pr-bot",
            issue_number=8,
            issue_title="Fallback body",
            issue_body="",
            comment_body="@incle-issue-to-pr-bot README 정리해줘",
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

        self.assertIn("## 요약", body)
        self.assertIn("Closes #8", body)
        self.assertIn("- `README.md`", body)
        self.assertIn(BOT_PR_MARKER, body)

    def test_build_pull_request_body_uses_llm_summary_placeholder_when_available(self) -> None:
        request = IssueRequest(
            repository="IncleRepo/issue-to-pr-bot",
            issue_number=13,
            issue_title="Fill template",
            issue_body="",
            comment_body="@incle-issue-to-pr-bot 구현해줘",
            comment_author="IncleRepo",
            comment_id=13,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            workspace.joinpath(".github").mkdir()
            workspace.joinpath(".github", "pull_request_template.md").write_text(
                "\n".join(
                    [
                        "## 변경 내용",
                        "",
                        "{{LLM_PR_SUMMARY}}",
                        "",
                        "## 관련 이슈",
                        "",
                        "Closes #{{ISSUE_NUMBER}}",
                    ]
                ),
                encoding="utf-8",
            )
            summary_path = get_pr_summary_draft_path(request, workspace)
            summary_path.parent.mkdir(parents=True, exist_ok=True)
            summary_path.write_text(
                "- 플레이어 이동 로직을 추가했습니다.\n- 점프와 중력 처리를 구현했습니다.",
                encoding="utf-8",
            )

            body = build_pull_request_body(request, BotConfig(), workspace, ["index.html", "main.js"])

        self.assertIn("- 플레이어 이동 로직을 추가했습니다.", body)
        self.assertIn("- 점프와 중력 처리를 구현했습니다.", body)
        self.assertEqual(body.count("## 변경 내용"), 1)
        self.assertIn(BOT_PR_MARKER, body)

    def test_build_pull_request_body_prefers_llm_body_draft_over_template(self) -> None:
        request = IssueRequest(
            repository="IncleRepo/issue-to-pr-bot",
            issue_number=15,
            issue_title="Flexible template",
            issue_body="",
            comment_body="@incle-issue-to-pr-bot 구현해줘",
            comment_author="IncleRepo",
            comment_id=15,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            workspace.joinpath(".github").mkdir()
            workspace.joinpath(".github", "pull_request_template.md").write_text(
                "\n".join(
                    [
                        "## 변경 내용",
                        "",
                        "- 템플릿 질문",
                        "",
                        "## 관련 이슈",
                        "",
                        "Closes #{{ISSUE_NUMBER}}",
                    ]
                ),
                encoding="utf-8",
            )
            body_path = get_pr_body_draft_path(request, workspace)
            body_path.parent.mkdir(parents=True, exist_ok=True)
            body_path.write_text(
                "\n".join(
                    [
                        "## 변경 내용",
                        "",
                        "- 플레이어 이동 입력을 구현했습니다.",
                        "- 템플릿 구조를 유지하면서 설명을 채웠습니다.",
                        "",
                        "## 관련 이슈",
                        "",
                        "Closes #{{ISSUE_NUMBER}}",
                    ]
                ),
                encoding="utf-8",
            )

            body = build_pull_request_body(request, BotConfig(), workspace, ["index.html", "main.js"])

        self.assertIn("- 플레이어 이동 입력을 구현했습니다.", body)
        self.assertNotIn("- 템플릿 질문", body)
        self.assertIn("Closes #15", body)
        self.assertIn(BOT_PR_MARKER, body)

    def test_build_pull_request_body_reads_legacy_runtime_output_as_fallback(self) -> None:
        request = IssueRequest(
            repository="IncleRepo/issue-to-pr-bot",
            issue_number=16,
            issue_title="Legacy fallback",
            issue_body="",
            comment_body="@incle-issue-to-pr-bot 구현해줘",
            comment_author="IncleRepo",
            comment_id=16,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            workspace.joinpath(".github").mkdir()
            workspace.joinpath(".github", "pull_request_template.md").write_text(
                "## 변경 내용\n\n- 템플릿 질문\n",
                encoding="utf-8",
            )
            legacy_body_path = get_legacy_task_output_root(request, workspace) / "pr-body.md"
            legacy_body_path.parent.mkdir(parents=True, exist_ok=True)
            legacy_body_path.write_text("## 변경 내용\n\n- 레거시 경로에서 읽었습니다.\n", encoding="utf-8")

            body = build_pull_request_body(request, BotConfig(), workspace, ["index.html"])

        self.assertIn("- 레거시 경로에서 읽었습니다.", body)
        self.assertNotIn("- 템플릿 질문", body)

    def test_build_pull_request_body_injects_llm_summary_into_existing_section(self) -> None:
        request = IssueRequest(
            repository="IncleRepo/issue-to-pr-bot",
            issue_number=14,
            issue_title="Inject summary",
            issue_body="",
            comment_body="@incle-issue-to-pr-bot 구현해줘",
            comment_author="IncleRepo",
            comment_id=14,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            workspace.joinpath(".github").mkdir()
            workspace.joinpath(".github", "pull_request_template.md").write_text(
                "\n".join(
                    [
                        "## 변경 내용",
                        "",
                        "- 무엇을 변경했나요?",
                        "- 왜 이 변경이 필요한가요?",
                        "",
                        "## 관련 이슈",
                        "",
                        "Closes #{{ISSUE_NUMBER}}",
                    ]
                ),
                encoding="utf-8",
            )
            summary_path = get_pr_summary_draft_path(request, workspace)
            summary_path.parent.mkdir(parents=True, exist_ok=True)
            summary_path.write_text(
                "- 키보드 입력으로 좌우 이동을 추가했습니다.\n- HUD에 좌표 표시를 추가했습니다.",
                encoding="utf-8",
            )

            body = build_pull_request_body(request, BotConfig(), workspace, ["index.html", "main.js"])

        self.assertIn("- 키보드 입력으로 좌우 이동을 추가했습니다.", body)
        self.assertIn("- HUD에 좌표 표시를 추가했습니다.", body)
        self.assertEqual(body.count("## 변경 내용"), 1)

    def test_build_pull_request_title_template_is_used(self) -> None:
        request = IssueRequest(
            repository="IncleRepo/issue-to-pr-bot",
            issue_number=9,
            issue_title="Custom title",
            issue_body="",
            comment_body="@incle-issue-to-pr-bot 제목만 확인해줘",
            comment_author="IncleRepo",
            comment_id=12,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            workspace.joinpath(".github").mkdir()
            workspace.joinpath(".github", "pull_request_template.md").write_text(
                "Closes #{{ISSUE_NUMBER}}",
                encoding="utf-8",
            )

            config = BotConfig(pr_title_template="BOT-{issue_number}: {issue_title}")
            body = build_pull_request_body(request, config, workspace, ["README.md"])

        self.assertEqual(body, f"Closes #9\n\n{BOT_PR_MARKER}")

    def test_commit_push_and_open_pr_uses_current_branch_when_present(self) -> None:
        request = IssueRequest(
            repository="IncleRepo/sample-repo",
            issue_number=21,
            issue_title="Publish current branch",
            issue_body="",
            comment_body="@incle-issue-to-pr-bot 구현해줘",
            comment_author="IncleRepo",
            comment_id=21,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            with (
                patch.dict(environ, {"BOT_GITHUB_TOKEN": "token", "GITHUB_REPOSITORY": request.repository}, clear=False),
                patch("app.github_pr.run_git") as run_git_mock,
                patch("app.github_pr.get_current_branch", return_value="feat/current-branch"),
                patch("app.github_pr.unstage_output_artifacts"),
                patch("app.github_pr.has_staged_changes", return_value=True),
                patch("app.github_pr.get_staged_files", return_value=["src/main.js"]),
                patch("app.github_pr.ensure_no_protected_changes"),
                patch("app.github_pr.push_branch") as push_branch_mock,
                patch("app.github_pr.ensure_pull_request", return_value="https://example.com/pr/21") as ensure_pr_mock,
                patch("app.github_pr.apply_pull_request_metadata_if_possible"),
            ):
                result = commit_push_and_open_pr(
                    request=request,
                    workspace=workspace,
                    config=BotConfig(),
                    branch_name="wrapper-branch",
                    base_branch="main",
                    commit_message="feat: publish current branch",
                )

        push_branch_mock.assert_called_once_with(request.repository, "feat/current-branch", "token", workspace)
        self.assertEqual(ensure_pr_mock.call_args.args[1], "feat/current-branch")
        self.assertEqual(result.branch_name, "feat/current-branch")
        self.assertTrue(any(call.args[0][:2] == ["commit", "-m"] for call in run_git_mock.call_args_list))

    def test_commit_push_and_open_pr_publishes_existing_local_commits_without_new_commit(self) -> None:
        request = IssueRequest(
            repository="IncleRepo/sample-repo",
            issue_number=22,
            issue_title="Reuse local commits",
            issue_body="",
            comment_body="@incle-issue-to-pr-bot 리뷰 반영해줘",
            comment_author="IncleRepo",
            comment_id=22,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            with (
                patch.dict(environ, {"BOT_GITHUB_TOKEN": "token", "GITHUB_REPOSITORY": request.repository}, clear=False),
                patch("app.github_pr.run_git") as run_git_mock,
                patch("app.github_pr.get_current_branch", return_value="feat/existing-commits"),
                patch("app.github_pr.unstage_output_artifacts"),
                patch("app.github_pr.has_staged_changes", return_value=False),
                patch("app.github_pr.branch_has_publishable_commits", return_value=True),
                patch("app.github_pr.get_raw_branch_changed_files", return_value=["src/main.js"]),
                patch("app.github_pr.push_branch") as push_branch_mock,
                patch("app.github_pr.ensure_pull_request", return_value="https://example.com/pr/22"),
                patch("app.github_pr.apply_pull_request_metadata_if_possible"),
            ):
                result = commit_push_and_open_pr(
                    request=request,
                    workspace=workspace,
                    config=BotConfig(),
                    branch_name="wrapper-branch",
                    base_branch="main",
                    commit_message="fix: ignored because codex already committed",
                )

        self.assertFalse(any(call.args[0][:2] == ["commit", "-m"] for call in run_git_mock.call_args_list))
        push_branch_mock.assert_called_once_with(request.repository, "feat/existing-commits", "token", workspace)
        self.assertEqual(result.changed_files, ["src/main.js"])
        self.assertEqual(result.branch_name, "feat/existing-commits")

    def test_commit_push_and_open_pr_requires_local_commit_when_changes_are_staged(self) -> None:
        request = IssueRequest(
            repository="IncleRepo/sample-repo",
            issue_number=23,
            issue_title="Codex local commit required",
            issue_body="",
            comment_body="@incle-issue-to-pr-bot 구현해줘",
            comment_author="IncleRepo",
            comment_id=23,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            with (
                patch.dict(environ, {"BOT_GITHUB_TOKEN": "token", "GITHUB_REPOSITORY": request.repository}, clear=False),
                patch("app.github_pr.run_git") as run_git_mock,
                patch("app.github_pr.get_current_branch", return_value="feat/codex-committed"),
                patch("app.github_pr.invalidate_codex_session"),
                patch("app.github_pr.unstage_output_artifacts"),
                patch("app.github_pr.has_staged_changes", return_value=True),
                patch("app.github_pr.get_staged_files", return_value=["src/main.js"]),
                patch("app.github_pr.ensure_no_protected_changes"),
            ):
                with self.assertRaises(RuntimeError) as context:
                    commit_push_and_open_pr(
                        request=request,
                        workspace=workspace,
                        config=BotConfig(),
                        branch_name="wrapper-branch",
                        base_branch="main",
                    )

        self.assertIn("no local commit", str(context.exception))
        self.assertFalse(any(call.args[0][:2] == ["commit", "-m"] for call in run_git_mock.call_args_list))

    def test_commit_push_and_open_pr_rejects_non_publishable_workspace_files_in_branch_diff(self) -> None:
        request = IssueRequest(
            repository="IncleRepo/sample-repo",
            issue_number=24,
            issue_title="Block workspace scratch files",
            issue_body="",
            comment_body="@incle-issue-to-pr-bot 구현해줘",
            comment_author="IncleRepo",
            comment_id=24,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            with (
                patch.dict(environ, {"BOT_GITHUB_TOKEN": "token", "GITHUB_REPOSITORY": request.repository}, clear=False),
                patch("app.github_pr.run_git"),
                patch("app.github_pr.get_current_branch", return_value="feat/current-branch"),
                patch("app.github_pr.unstage_output_artifacts"),
                patch("app.github_pr.has_staged_changes", return_value=False),
                patch("app.github_pr.branch_has_publishable_commits", return_value=True),
                patch("app.github_pr.get_raw_branch_changed_files", return_value=[".issue-to-pr-bot/output/pr-body.md"]),
                patch("app.github_pr.push_branch") as push_branch_mock,
                patch("app.github_pr.ensure_pull_request") as ensure_pr_mock,
            ):
                with self.assertRaises(RuntimeError) as context:
                    commit_push_and_open_pr(
                        request=request,
                        workspace=workspace,
                        config=BotConfig(),
                        branch_name="wrapper-branch",
                        base_branch="main",
                    )

        self.assertIn("Non-publishable workspace files are present", str(context.exception))
        self.assertIn(".issue-to-pr-bot/output/pr-body.md", str(context.exception))
        push_branch_mock.assert_not_called()
        ensure_pr_mock.assert_not_called()

    def test_ensure_pull_request_prefers_codex_title_draft(self) -> None:
        request = IssueRequest(
            repository="IncleRepo/sample-repo",
            issue_number=25,
            issue_title="[Feature] 배경에 구름 추가",
            issue_body="",
            comment_body="@incle-issue-to-pr-bot 구현해줘",
            comment_author="IncleRepo",
            comment_id=25,
        )
        config = BotConfig(pr_title_template="wrapper title fallback")

        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            title_path = get_pr_title_draft_path(request, workspace)
            title_path.parent.mkdir(parents=True, exist_ok=True)
            title_path.write_text("배경에 구름 추가", encoding="utf-8")
            with (
                patch("app.github_pr.find_existing_pull_request", return_value=None),
                patch("app.github_pr.build_pull_request_body", return_value="body"),
                patch("app.github_pr.github_request", return_value={"html_url": "https://example.com/pr/25"}) as github_request_mock,
            ):
                pr_url = ensure_pull_request(
                    repository=request.repository,
                    branch_name="feat/25-clouds",
                    base_branch="main",
                    request=request,
                    token="token",
                    config=config,
                    workspace=workspace,
                    changed_files=["index.html"],
                    verification_commands=[],
                )

        self.assertEqual(pr_url, "https://example.com/pr/25")
        self.assertEqual(github_request_mock.call_args.args[3]["title"], "배경에 구름 추가")

    def test_ensure_pull_request_updates_body_only_for_pull_request_follow_up_when_draft_exists(self) -> None:
        request = IssueRequest(
            repository="IncleRepo/sample-repo",
            issue_number=25,
            issue_title="[Feature] 배경에 구름 추가",
            issue_body="",
            comment_body="@incle-issue-to-pr-bot 이 리뷰 반영해줘",
            comment_author="IncleRepo",
            comment_id=99,
            is_pull_request=True,
            pull_request_number=25,
        )
        config = BotConfig(pr_title_template="wrapper title fallback")

        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            body_path = get_pr_body_draft_path(request, workspace)
            body_path.parent.mkdir(parents=True, exist_ok=True)
            body_path.write_text("## 변경 사항\n\n- 리뷰 반영 내용을 추가로 명시합니다.\n", encoding="utf-8")
            with (
                patch("app.github_pr.find_existing_pull_request", return_value="https://example.com/pull/25"),
                patch("app.github_pr.github_request") as github_request_mock,
            ):
                pr_url = ensure_pull_request(
                    repository=request.repository,
                    branch_name="feat/25-clouds",
                    base_branch="main",
                    request=request,
                    token="token",
                    config=config,
                    workspace=workspace,
                    changed_files=["index.html"],
                    verification_commands=[],
                )

        self.assertEqual(pr_url, "https://example.com/pull/25")
        self.assertEqual(
            github_request_mock.call_args.args[3],
            {"body": "## 변경 사항\n\n- 리뷰 반영 내용을 추가로 명시합니다.\n\nCloses #25\n\n<!-- incle-issue-to-pr-bot -->"},
        )

    def test_ensure_pull_request_keeps_existing_body_for_pull_request_follow_up_without_new_draft(self) -> None:
        request = IssueRequest(
            repository="IncleRepo/sample-repo",
            issue_number=25,
            issue_title="[Feature] 배경에 구름 추가",
            issue_body="",
            comment_body="@incle-issue-to-pr-bot 이 리뷰 반영해줘",
            comment_author="IncleRepo",
            comment_id=100,
            is_pull_request=True,
            pull_request_number=25,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            with (
                patch("app.github_pr.find_existing_pull_request", return_value="https://example.com/pull/25"),
                patch("app.github_pr.github_request") as github_request_mock,
            ):
                pr_url = ensure_pull_request(
                    repository=request.repository,
                    branch_name="feat/25-clouds",
                    base_branch="main",
                    request=request,
                    token="token",
                    config=BotConfig(),
                    workspace=workspace,
                    changed_files=["index.html"],
                    verification_commands=[],
                )

        self.assertEqual(pr_url, "https://example.com/pull/25")
        github_request_mock.assert_not_called()

    def test_branch_has_publishable_commits_fetches_remote_tracking_ref_when_missing_locally(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            with (
                patch("app.github_pr.get_remote_branch_head", return_value="abc123"),
                patch("app.github_pr.git_ref_exists", side_effect=[False, True]),
                patch("app.github_pr.subprocess.run") as subprocess_run_mock,
            ):
                subprocess_run_mock.side_effect = [
                    Mock(returncode=0, stdout=""),
                    Mock(returncode=0, stdout="2\n"),
                ]
                has_commits = branch_has_publishable_commits(workspace, "feat/existing", "main")

        self.assertTrue(has_commits)
        fetch_call = subprocess_run_mock.call_args_list[0]
        self.assertIn("fetch", fetch_call.args[0])
        self.assertIn("feat/existing:refs/remotes/origin/feat/existing", fetch_call.args[0])

    def test_sync_pull_request_branch_with_base_detects_clean_merge(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            with (
                patch("app.github_pr.configure_git"),
                patch("app.github_pr.run_git") as run_git_mock,
                patch(
                    "app.github_pr.subprocess.run",
                    return_value=subprocess.CompletedProcess(
                        args=["git"],
                        returncode=0,
                        stdout="Automatic merge went well; stopped before committing as requested.\n",
                    ),
                ),
            ):
                result = sync_pull_request_branch_with_base(workspace, "main")

        run_git_mock.assert_called_once_with(["fetch", "origin", "main"], workspace)
        self.assertEqual(
            result,
            BaseSyncResult(
                attempted=True,
                mode="merge",
                up_to_date=False,
                has_conflicts=False,
                changed_tree=True,
                base_branch="main",
            ),
        )

    def test_sync_pull_request_branch_with_base_detects_conflicts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            with (
                patch("app.github_pr.configure_git"),
                patch("app.github_pr.run_git"),
                patch(
                    "app.github_pr.subprocess.run",
                    side_effect=[
                        subprocess.CompletedProcess(args=["git"], returncode=1, stdout="CONFLICT\n"),
                        subprocess.CompletedProcess(args=["git"], returncode=0, stdout="app/main.py\n"),
                    ],
                ),
            ):
                result = sync_pull_request_branch_with_base(workspace, "main")

        self.assertEqual(
            result,
            BaseSyncResult(
                attempted=True,
                mode="merge",
                up_to_date=False,
                has_conflicts=True,
                changed_tree=True,
                base_branch="main",
            ),
        )

    def test_apply_base_sync_strategy_autostashes_dirty_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            with (
                patch(
                    "app.github_pr.inspect_worktree_state",
                    return_value=type("State", (), {"dirty": True})(),
                ),
                patch("app.github_pr.create_temporary_stash", return_value=True) as stash_mock,
                patch(
                    "app.github_pr.sync_branch_with_base",
                    return_value=BaseSyncResult(
                        attempted=True,
                        mode="rebase",
                        up_to_date=False,
                        has_conflicts=False,
                        changed_tree=True,
                        base_branch="main",
                    ),
                ) as sync_mock,
                patch("app.github_pr.restore_temporary_stash", return_value="restored") as restore_mock,
            ):
                result = apply_base_sync_strategy(workspace, "main", "rebase", allow_autostash=True)

        stash_mock.assert_called_once()
        sync_mock.assert_called_once_with(workspace, "main", "rebase")
        restore_mock.assert_called_once()
        self.assertEqual(result.mode, "rebase")
        self.assertTrue(result.changed_tree)

    def test_apply_base_sync_strategy_rejects_dirty_worktree_without_autostash(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            with patch(
                "app.github_pr.inspect_worktree_state",
                return_value=type("State", (), {"dirty": True})(),
            ):
                with self.assertRaises(RuntimeError):
                    apply_base_sync_strategy(workspace, "main", "rebase", allow_autostash=False)

    @patch("app.github_pr.try_auto_merge_pull_request")
    @patch("app.github_pr.github_request")
    def test_request_pull_request_merge_records_marker_and_attempts_merge(self, github_request_mock, try_merge_mock) -> None:
        github_request_mock.side_effect = [
            {"number": 5, "body": BOT_PR_MARKER, "html_url": "https://example.com/pr/5"},
            {"number": 5, "body": f"{BOT_PR_MARKER}\n\n{BOT_AUTO_MERGE_MARKER}", "html_url": "https://example.com/pr/5"},
        ]
        try_merge_mock.return_value = "abc123"

        result = request_pull_request_merge("IncleRepo/issue-to-pr-bot", 5, "token")

        self.assertTrue(result.requested)
        self.assertTrue(result.merged)
        self.assertEqual(result.merge_sha, "abc123")
        self.assertEqual(result.pull_request_url, "https://example.com/pr/5")

    @patch("app.github_pr.github_request")
    def test_apply_issue_metadata_uses_existing_labels_and_milestone(self, github_request_mock) -> None:
        github_request_mock.side_effect = [
            [{"name": "bug"}, {"name": "documentation"}],
            None,
            [{"number": 3, "title": "Sprint 1"}],
            None,
        ]

        apply_issue_metadata(
            repository="IncleRepo/issue-to-pr-bot",
            issue_number=9,
            token="token",
            plan=MetadataPlan(
                issue_labels=["bug", "docs"],
                pr_labels=[],
                assignees=["@alice"],
                reviewers=[],
                team_reviewers=[],
                milestone_title="Sprint 1",
            ),
        )

        label_call = github_request_mock.call_args_list[1]
        issue_call = github_request_mock.call_args_list[3]
        self.assertIn("/repos/IncleRepo/issue-to-pr-bot/issues/9/labels", label_call.args[1])
        self.assertEqual(label_call.args[3], {"labels": ["bug", "documentation"]})
        self.assertEqual(issue_call.args[3], {"assignees": ["alice"], "milestone": 3})

    @patch("app.github_pr.github_request")
    def test_create_issue_comment_skips_invalid_issue_number(self, github_request_mock) -> None:
        result = create_issue_comment("IncleRepo/issue-to-pr-bot", 0, "body", token="token")

        self.assertIsNone(result)
        github_request_mock.assert_not_called()

    @patch("app.github_pr.github_request")
    def test_apply_pull_request_metadata_requests_reviewers(self, github_request_mock) -> None:
        github_request_mock.side_effect = [
            [{"name": "automation"}],
            [],
            None,
            None,
            None,
        ]

        apply_pull_request_metadata(
            repository="IncleRepo/issue-to-pr-bot",
            pull_request_number=15,
            token="token",
            plan=MetadataPlan(
                issue_labels=[],
                pr_labels=["automation"],
                assignees=["@alice"],
                reviewers=["@bob"],
                team_reviewers=["@org/backend"],
                milestone_title=None,
            ),
        )

        review_call = github_request_mock.call_args_list[-1]
        self.assertIn("/repos/IncleRepo/issue-to-pr-bot/pulls/15/requested_reviewers", review_call.args[1])
        self.assertEqual(review_call.args[3], {"reviewers": ["bob"], "team_reviewers": ["org/backend"]})


if __name__ == "__main__":
    unittest.main()
