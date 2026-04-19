import unittest

from tempfile import TemporaryDirectory
from unittest.mock import patch

from app.bot import (
    IssueRequest,
    build_branch_name,
    build_codex_commit_message,
    build_issue_request,
    build_pull_request_title,
    build_task_prompt,
    build_test_commit_message,
    parse_bot_command,
    resolve_runtime_options,
    should_run_bot,
    should_run_for_mention,
)
from app.config import BotConfig
from app.output_artifacts import get_commit_message_draft_path, get_pr_body_draft_path, get_pr_summary_draft_path
from app.output_artifacts import get_pr_title_draft_path


class BotTest(unittest.TestCase):
    def test_should_run_bot_requires_mention(self) -> None:
        config = BotConfig()

        self.assertTrue(should_run_bot("@incle-issue-to-pr-bot README ?섏젙?댁쨾", config))
        self.assertFalse(should_run_bot("README ?섏젙?댁쨾", config))
        self.assertFalse(should_run_bot("/bot run", config))

    def test_should_run_bot_accepts_fixed_public_mention(self) -> None:
        config = BotConfig()

        self.assertTrue(should_run_bot("@incle-issue-to-pr-bot README ?낅뜲?댄듃", config))
        self.assertTrue(should_run_for_mention("please @incle-issue-to-pr-bot, handle this", config))
        self.assertFalse(should_run_for_mention("@someone-else handle this", config))

    def test_parse_bot_command_infers_action_from_natural_language(self) -> None:
        run_command = parse_bot_command(
            "@incle-issue-to-pr-bot README 수정해줘. codex high로 돌려주고 테스트 없이 진행해줘.",
            BotConfig(),
        )
        plan_command = parse_bot_command("@incle-issue-to-pr-bot README 수정 계획만 세워줘", BotConfig())
        help_command = parse_bot_command("@incle-issue-to-pr-bot 사용법 알려줘", BotConfig())
        status_command = parse_bot_command("@incle-issue-to-pr-bot 지금 상태 점검해줘", BotConfig())

        self.assertIsNotNone(run_command)
        self.assertIsNotNone(plan_command)
        self.assertIsNotNone(help_command)
        self.assertIsNotNone(status_command)
        assert run_command is not None
        assert plan_command is not None
        assert help_command is not None
        assert status_command is not None

        self.assertEqual(run_command.action, "run")
        self.assertEqual(run_command.options["provider"], "codex")
        self.assertEqual(run_command.options["effort"], "high")
        self.assertEqual(run_command.options["verify"], "false")
        self.assertEqual(plan_command.action, "plan")
        self.assertEqual(help_command.action, "help")
        self.assertEqual(status_command.action, "status")

    def test_parse_bot_command_empty_instruction_defaults_to_help(self) -> None:
        command = parse_bot_command("@incle-issue-to-pr-bot", BotConfig())

        assert command is not None
        self.assertEqual(command.action, "help")

    def test_parse_bot_command_infers_test_pr_mode(self) -> None:
        command = parse_bot_command(
            "@incle-issue-to-pr-bot 브랜치와 PR만 먼저 만들어줘. 코드 수정 없이.",
            BotConfig(),
        )

        assert command is not None
        self.assertEqual(command.options["mode"], "test-pr")

    def test_parse_bot_command_infers_test_pr_mode_from_draft_wording(self) -> None:
        command = parse_bot_command(
            "@incle-issue-to-pr-bot 초안만 먼저 만들어줘. draft만 만들고 코드 수정은 하지마.",
            BotConfig(),
        )

        assert command is not None
        self.assertEqual(command.options["mode"], "test-pr")

    def test_build_issue_request_handles_missing_values(self) -> None:
        request = build_issue_request({})

        self.assertEqual(request.repository, "unknown/unknown")
        self.assertEqual(request.issue_number, 0)
        self.assertEqual(request.issue_title, "")
        self.assertEqual(request.issue_body, "")
        self.assertEqual(request.comment_body, "")
        self.assertEqual(request.comment_author, "unknown")
        self.assertEqual(request.comment_id, 0)

    def test_build_issue_request_reads_event_payload(self) -> None:
        request = build_issue_request(
            {
                "repository": {"full_name": "IncleRepo/issue-to-pr-bot"},
                "issue": {
                    "number": 12,
                    "title": "?뚯뒪??湲곕뒫 異붽?",
                    "body": "?붽뎄?ы빆",
                },
                "comment": {
                    "body": "@incle-issue-to-pr-bot ?뚯뒪??湲곕뒫 異붽??댁쨾",
                    "id": 34,
                    "user": {"login": "IncleRepo"},
                },
            }
        )

        self.assertEqual(request.repository, "IncleRepo/issue-to-pr-bot")
        self.assertEqual(request.issue_number, 12)
        self.assertEqual(request.issue_title, "?뚯뒪??湲곕뒫 異붽?")
        self.assertEqual(request.issue_body, "?붽뎄?ы빆")
        self.assertEqual(request.comment_body, "@incle-issue-to-pr-bot ?뚯뒪??湲곕뒫 異붽??댁쨾")
        self.assertEqual(request.comment_author, "IncleRepo")
        self.assertEqual(request.comment_id, 34)
        self.assertFalse(request.is_pull_request)
        self.assertIsNone(request.pull_request_number)

    def test_build_issue_request_marks_pull_request_comments(self) -> None:
        request = build_issue_request(
            {
                "repository": {"full_name": "IncleRepo/issue-to-pr-bot"},
                "issue": {
                    "number": 44,
                    "title": "Bot PR",
                    "body": "PR body",
                    "pull_request": {"url": "https://api.github.com/repos/IncleRepo/issue-to-pr-bot/pulls/44"},
                },
                "comment": {
                    "body": "@incle-issue-to-pr-bot here",
                    "id": 35,
                    "user": {"login": "IncleRepo"},
                },
            }
        )

        self.assertTrue(request.is_pull_request)
        self.assertEqual(request.pull_request_number, 44)

    def test_build_issue_request_reads_pull_request_review_comment_payload(self) -> None:
        request = build_issue_request(
            {
                "repository": {"full_name": "IncleRepo/issue-to-pr-bot"},
                "pull_request": {
                    "number": 52,
                    "title": "Fix review feedback",
                    "body": "PR body",
                    "html_url": "https://github.com/IncleRepo/issue-to-pr-bot/pull/52",
                    "base": {"ref": "main"},
                    "head": {"ref": "bot/issue-52"},
                },
                "comment": {
                    "body": "@incle-issue-to-pr-bot ??由щ럭 諛섏쁺?댁쨾",
                    "id": 81,
                    "path": "app/main.py",
                    "line": 120,
                    "start_line": 118,
                    "side": "RIGHT",
                    "diff_hunk": "@@ -1 +1 @@\n-old\n+new",
                    "html_url": "https://github.com/IncleRepo/issue-to-pr-bot/pull/52#discussion_r1",
                    "user": {"login": "reviewer"},
                },
            }
        )

        self.assertTrue(request.is_pull_request)
        self.assertEqual(request.pull_request_number, 52)
        self.assertEqual(request.base_branch, "main")
        self.assertEqual(request.head_branch, "bot/issue-52")
        self.assertEqual(request.review_path, "app/main.py")
        self.assertEqual(request.review_line, 120)
        self.assertEqual(request.review_start_line, 118)
        self.assertEqual(request.review_side, "RIGHT")
        self.assertIn("@@ -1 +1 @@", request.review_diff_hunk or "")

    def test_build_task_prompt_includes_review_comment_context(self) -> None:
        request = IssueRequest(
            repository="IncleRepo/issue-to-pr-bot",
            issue_number=52,
            issue_title="Fix review feedback",
            issue_body="PR body",
            comment_body="@incle-issue-to-pr-bot ??由щ럭 諛섏쁺?댁쨾",
            comment_author="reviewer",
            comment_id=81,
            is_pull_request=True,
            pull_request_number=52,
            review_path="app/main.py",
            review_line=120,
            review_diff_hunk="@@ -1 +1 @@\n-old\n+new",
        )

        prompt = build_task_prompt(request, BotConfig())

        self.assertIn("Review context:", prompt)
        self.assertIn("File: app/main.py", prompt)
        self.assertIn("Line: 120", prompt)
        self.assertIn("```diff", prompt)

    def test_build_task_prompt_forbids_direct_github_operations(self) -> None:
        request = IssueRequest(
            repository="IncleRepo/issue-to-pr-bot",
            issue_number=53,
            issue_title="Implement feature",
            issue_body="Issue body",
            comment_body="@incle-issue-to-pr-bot 구현해줘",
            comment_author="IncleRepo",
            comment_id=82,
        )

        prompt = build_task_prompt(request, BotConfig())
        body_path = get_pr_body_draft_path(request)
        title_path = get_pr_title_draft_path(request)
        summary_path = get_pr_summary_draft_path(request)

        self.assertIn(
            "Do not push branches, open pull requests, merge pull requests, or post GitHub comments yourself.",
            prompt,
        )
        self.assertIn("The wrapper will supervise remote publish and merge steps after your local work is done.", prompt)
        self.assertIn("workspace-only scratch files", prompt)
        self.assertIn("Never include those workspace-only files in commits", prompt)
        self.assertIn(str(title_path), prompt)
        self.assertIn(str(body_path), prompt)
        self.assertIn("follow its structure and fill it naturally", prompt)
        self.assertIn(str(summary_path), prompt)
        self.assertIn("write the final pull request title draft", prompt)
        self.assertIn("write the final pull request body draft", prompt)
        self.assertIn("must create or amend at least one local commit", prompt)
        self.assertIn("exact number of commits and their message style should follow the repository guidance", prompt)
        self.assertIn("does not run Codex with codex-sandbox", prompt)
        self.assertIn("Keep your work centered inside the assigned workspace whenever practical.", prompt)
        self.assertIn("prefer creating a workspace-local Docker or Docker Compose setup", prompt)
        self.assertIn("instead of requesting host-level setup", prompt)
        self.assertNotIn("commit-message.txt", prompt)
        self.assertIn("When implementation and verification are complete, stop and exit immediately", prompt)

    def test_build_task_prompt_for_pull_request_follow_up_only_requests_body_draft_when_needed(self) -> None:
        request = IssueRequest(
            repository="IncleRepo/issue-to-pr-bot",
            issue_number=53,
            issue_title="Implement feature",
            issue_body="Issue body",
            comment_body="@incle-issue-to-pr-bot 이 리뷰 반영해줘",
            comment_author="IncleRepo",
            comment_id=91,
            is_pull_request=True,
            pull_request_number=53,
        )

        prompt = build_task_prompt(request, BotConfig())
        body_path = get_pr_body_draft_path(request)

        self.assertIn("keep the existing PR title unchanged", prompt)
        self.assertIn(str(body_path), prompt)
        self.assertIn("only write", prompt)
        self.assertIn("If the current PR body can stay as-is", prompt)
        self.assertNotIn("write the final pull request title draft", prompt)

    def test_build_branch_name_is_stable(self) -> None:
        request = IssueRequest(
            repository="IncleRepo/issue-to-pr-bot",
            issue_number=12,
            issue_title="Add GitHub PR flow!",
            issue_body="",
            comment_body="@incle-issue-to-pr-bot 援ы쁽?댁쨾",
            comment_author="IncleRepo",
            comment_id=34,
        )

        self.assertEqual(build_branch_name(request), "bot/issue-12-comment-34-add-github-pr-flow")

    def test_build_branch_name_uses_configured_prefix(self) -> None:
        request = IssueRequest(
            repository="IncleRepo/issue-to-pr-bot",
            issue_number=12,
            issue_title="Add GitHub PR flow!",
            issue_body="",
            comment_body="@incle-issue-to-pr-bot 援ы쁽?댁쨾",
            comment_author="IncleRepo",
            comment_id=34,
        )

        config = BotConfig(branch_prefix="agent")
        self.assertEqual(build_branch_name(request, config), "agent/issue-12-comment-34-add-github-pr-flow")

    def test_templates_can_customize_branch_commit_and_pr_title(self) -> None:
        request = IssueRequest(
            repository="IncleRepo/issue-to-pr-bot",
            issue_number=15,
            issue_title="Add GitHub PR flow!",
            issue_body="",
            comment_body="@incle-issue-to-pr-bot 援ы쁽?댁쨾",
            comment_author="IncleRepo",
            comment_id=21,
        )
        config = BotConfig(
            branch_prefix="agent",
            branch_name_template="work/{issue_number}-{slug}",
            pr_title_template="bot/#{issue_number} {issue_title}",
            codex_commit_message_template="{commit_type}(issue-{issue_number}): {issue_title}",
            test_commit_message_template="chore(issue-{issue_number}): marker",
        )

        self.assertEqual(build_branch_name(request, config), "work/15-add-github-pr-flow-comment-21")
        self.assertEqual(build_pull_request_title(request, config), "bot/#15 Add GitHub PR flow!")
        self.assertEqual(build_codex_commit_message(request, config), "feat(issue-15): Add GitHub PR flow!")
        self.assertEqual(build_test_commit_message(request, config), "chore(issue-15): marker")

    def test_build_branch_name_can_use_inferred_commit_type_slot(self) -> None:
        request = IssueRequest(
            repository="IncleRepo/game-repo",
            issue_number=21,
            issue_title="Map collision fix",
            issue_body="",
            comment_body="@incle-issue-to-pr-bot 충돌 판정 수정해줘",
            comment_author="IncleRepo",
            comment_id=55,
        )
        config = BotConfig(branch_name_template="{commit_type}/{issue_number}-{slug}")

        self.assertEqual(build_branch_name(request, config), "fix/21-map-collision-fix-comment-55")

    def test_build_codex_commit_message_infers_fix_for_follow_up_correction(self) -> None:
        request = IssueRequest(
            repository="IncleRepo/issue-to-pr-bot",
            issue_number=15,
            issue_title="html hello world 구현",
            issue_body="",
            comment_body="@incle-issue-to-pr-bot 저기 hello world가 아니라 hello html이라고 수정해줘",
            comment_author="IncleRepo",
            comment_id=21,
        )
        config = BotConfig(codex_commit_message_template="{commit_type}(issue-{issue_number}): {issue_title}")

        self.assertEqual(build_codex_commit_message(request, config), "fix(issue-15): html hello world 구현")

    def test_build_codex_commit_message_swaps_fixed_conventional_prefix(self) -> None:
        request = IssueRequest(
            repository="IncleRepo/issue-to-pr-bot",
            issue_number=15,
            issue_title="html hello world 구현",
            issue_body="",
            comment_body="@incle-issue-to-pr-bot 저기 hello world가 아니라 hello html이라고 수정해줘",
            comment_author="IncleRepo",
            comment_id=21,
        )
        config = BotConfig(codex_commit_message_template="feat(issue-{issue_number}): {issue_title}")

        self.assertEqual(build_codex_commit_message(request, config), "fix(issue-15): html hello world 구현")

    def test_build_codex_commit_message_infers_docs_from_changed_files(self) -> None:
        request = IssueRequest(
            repository="IncleRepo/issue-to-pr-bot",
            issue_number=16,
            issue_title="README 蹂닿컯",
            issue_body="",
            comment_body="@incle-issue-to-pr-bot README瑜????먯꽭???⑥쨾",
            comment_author="IncleRepo",
            comment_id=22,
        )
        config = BotConfig(codex_commit_message_template="{commit_type}(issue-{issue_number}): {issue_title}")

        self.assertEqual(
            build_codex_commit_message(request, config, changed_files=["README.md"]),
            "docs(issue-16): README 蹂닿컯",
        )

    def test_build_codex_commit_message_prefers_codex_summary_draft(self) -> None:
        request = IssueRequest(
            repository="IncleRepo/-RPG",
            issue_number=17,
            issue_title="[Feature] 배경에 구름 추가",
            issue_body="",
            comment_body="@incle-issue-to-pr-bot 구현해줘",
            comment_author="IncleRepo",
            comment_id=30,
        )
        config = BotConfig(codex_commit_message_template="{commit_type}: {issue_title}")

        with TemporaryDirectory() as temp_dir, patch.dict("os.environ", {"BOT_WORKSPACE_ROOT": temp_dir}):
            draft_path = get_commit_message_draft_path(request)
            draft_path.parent.mkdir(parents=True, exist_ok=True)
            draft_path.write_text("[Feature] 배경에 구름 추가\n", encoding="utf-8")

            self.assertEqual(build_codex_commit_message(request, config), "feat: 배경에 구름 추가")

    def test_build_codex_commit_message_strips_redundant_prefix_from_draft(self) -> None:
        request = IssueRequest(
            repository="IncleRepo/-RPG",
            issue_number=18,
            issue_title="[Fix] 충돌 판정 수정",
            issue_body="",
            comment_body="@incle-issue-to-pr-bot 수정해줘",
            comment_author="IncleRepo",
            comment_id=31,
        )
        config = BotConfig(codex_commit_message_template="{commit_type}: {issue_title}")

        with TemporaryDirectory() as temp_dir, patch.dict("os.environ", {"BOT_WORKSPACE_ROOT": temp_dir}):
            draft_path = get_commit_message_draft_path(request)
            draft_path.parent.mkdir(parents=True, exist_ok=True)
            draft_path.write_text("fix: 충돌 판정 수정\n", encoding="utf-8")

            self.assertEqual(build_codex_commit_message(request, config), "fix: 충돌 판정 수정")

    def test_resolve_runtime_options_supports_natural_language_hints(self) -> None:
        command = parse_bot_command(
            "@incle-issue-to-pr-bot README 수정해줘. codex high로 해주고 테스트 없이 진행해줘.",
            BotConfig(mode="test-pr"),
        )

        assert command is not None
        options = resolve_runtime_options(command, BotConfig(mode="test-pr"))

        self.assertEqual(options.mode, "codex")
        self.assertEqual(options.provider, "codex")
        self.assertFalse(options.verify)
        self.assertEqual(options.effort, "high")

    def test_resolve_runtime_options_rejects_unsupported_provider(self) -> None:
        command = parse_bot_command("@incle-issue-to-pr-bot README ?섏젙?댁쨾. claude濡??댁쨾.", BotConfig())

        assert command is not None
        with self.assertRaises(ValueError):
            resolve_runtime_options(command, BotConfig())

    def test_resolve_runtime_options_infers_base_sync_from_natural_language(self) -> None:
        command = parse_bot_command(
            "@incle-issue-to-pr-bot sync with main and resolve conflict before pushing again.",
            BotConfig(),
        )

        assert command is not None
        options = resolve_runtime_options(command, BotConfig())

        self.assertTrue(options.sync_base)
        self.assertEqual(options.mode, "codex")

    def test_resolve_runtime_options_infers_fresh_workspace_from_natural_language(self) -> None:
        command = parse_bot_command(
            "@incle-issue-to-pr-bot 기존에있는 워크스페이스 상관없이 다시 새로 구현부탁해",
            BotConfig(),
        )

        assert command is not None
        options = resolve_runtime_options(command, BotConfig())

        self.assertTrue(options.fresh_workspace)

    def test_resolve_runtime_options_infers_default_effort_for_simple_docs_change(self) -> None:
        command = parse_bot_command(
            "@incle-issue-to-pr-bot README 臾멸뎄留??ㅻ벉?댁쨾",
            BotConfig(),
        )

        assert command is not None
        options = resolve_runtime_options(command, BotConfig())

        self.assertEqual(options.effort, "low")

    def test_resolve_runtime_options_infers_default_effort_for_conflict_resolution(self) -> None:
        command = parse_bot_command(
            "@incle-issue-to-pr-bot main 반영하고 충돌 해결해줘",
            BotConfig(),
        )

        assert command is not None
        options = resolve_runtime_options(command, BotConfig())

        self.assertEqual(options.effort, "high")

    def test_resolve_runtime_options_infers_high_for_large_scope_wording(self) -> None:
        command = parse_bot_command(
            "@incle-issue-to-pr-bot 전역적으로 여러 파일을 대대적으로 정리해줘",
            BotConfig(),
        )

        assert command is not None
        options = resolve_runtime_options(command, BotConfig())

        self.assertEqual(options.effort, "high")

    def test_resolve_runtime_options_infers_low_for_small_scope_wording(self) -> None:
        command = parse_bot_command(
            "@incle-issue-to-pr-bot 문구 한 줄만 간단히 바꿔줘",
            BotConfig(),
        )

        assert command is not None
        options = resolve_runtime_options(command, BotConfig())

        self.assertEqual(options.effort, "low")

    def test_parse_bot_command_infers_merge_action(self) -> None:
        command = parse_bot_command(
            "@incle-issue-to-pr-bot 승인되면 합쳐줘",
            BotConfig(),
        )

        assert command is not None
        self.assertEqual(command.action, "merge")
        self.assertEqual(command.options["request_merge"], "true")

    def test_parse_bot_command_understands_variant_plan_and_effort_wording(self) -> None:
        command = parse_bot_command(
            "@incle-issue-to-pr-bot 코드 말고 설계만 먼저 잡아줘. 아주 깊게 봐줘",
            BotConfig(),
        )

        assert command is not None
        self.assertEqual(command.action, "plan")
        self.assertEqual(command.options["effort"], "xhigh")

    def test_parse_bot_command_does_not_confuse_help_with_implementation_request(self) -> None:
        command = parse_bot_command(
            "@incle-issue-to-pr-bot README help section 추가해줘",
            BotConfig(),
        )

        assert command is not None
        self.assertEqual(command.action, "run")


if __name__ == "__main__":
    unittest.main()
