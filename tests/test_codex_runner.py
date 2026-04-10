import unittest
from pathlib import Path

from app.codex_runner import build_codex_command


class CodexRunnerTest(unittest.TestCase):
    def test_build_codex_command_uses_ephemeral_noninteractive_exec(self) -> None:
        workspace = Path("/workspace")
        command = build_codex_command(workspace)

        self.assertEqual(command[:2], ["codex", "exec"])
        self.assertIn("--ephemeral", command)
        self.assertIn("--sandbox", command)
        self.assertIn("workspace-write", command)
        self.assertIn("--cd", command)
        self.assertEqual(command[command.index("--cd") + 1], str(workspace))
        self.assertEqual(command[-1], "-")


if __name__ == "__main__":
    unittest.main()
