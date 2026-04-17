import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from app.workspace_state import (
    CODEX_SESSION_REUSE_TTL,
    cleanup_stale_workspaces,
    infer_scope_from_workspace,
    mark_codex_session_ready,
    mark_workspace_linked_pull_request,
    read_workspace_metadata,
    should_resume_codex_session,
    touch_workspace_metadata,
)


class WorkspaceStateTest(unittest.TestCase):
    def test_infer_scope_from_workspace(self) -> None:
        scope_type, scope_number = infer_scope_from_workspace(Path(r"C:\work\IncleRepo__repo\issue-17"))
        self.assertEqual(scope_type, "issue")
        self.assertEqual(scope_number, 17)

    def test_should_resume_codex_session_requires_recent_ready_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "IncleRepo__repo" / "issue-17"
            workspace.mkdir(parents=True)
            touch_workspace_metadata(workspace, repository="IncleRepo/repo", scope_type="issue", scope_number=17)
            self.assertFalse(should_resume_codex_session(workspace))

            codex_home = workspace.parent.parent / ".issue-to-pr-bot-runtime" / workspace.parent.name / workspace.name / "codex-home-root" / ".codex"
            codex_home.mkdir(parents=True, exist_ok=True)
            mark_codex_session_ready(workspace, resumed=False)
            self.assertTrue(should_resume_codex_session(workspace))

            stale_time = read_workspace_metadata(workspace)["codex_last_run_at"]
            with patch("app.workspace_state.now_utc") as now_mock:
                from app.workspace_state import parse_iso_datetime

                now_mock.return_value = parse_iso_datetime(stale_time) + CODEX_SESSION_REUSE_TTL + timedelta(minutes=1)
                self.assertFalse(should_resume_codex_session(workspace))

    def test_cleanup_stale_workspaces_uses_shorter_ttl_after_issue_links_pr(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace_root = Path(temp_dir)
            issue_workspace = workspace_root / "IncleRepo__repo" / "issue-15"
            issue_workspace.mkdir(parents=True)
            touch_workspace_metadata(issue_workspace, repository="IncleRepo/repo", scope_type="issue", scope_number=15)
            mark_workspace_linked_pull_request(issue_workspace, 22)

            metadata = read_workspace_metadata(issue_workspace)
            from app.workspace_state import parse_iso_datetime

            with patch("app.workspace_state.now_utc") as now_mock:
                now_mock.return_value = parse_iso_datetime(metadata["last_used_at"]) + timedelta(days=4)
                removed = cleanup_stale_workspaces(workspace_root)

            self.assertEqual(len(removed), 1)
            self.assertFalse(issue_workspace.exists())


if __name__ == "__main__":
    unittest.main()
