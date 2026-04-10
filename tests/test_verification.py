import tempfile
import unittest
from pathlib import Path

from app.config import BotConfig
from app.verification import VerificationError, run_verification


class VerificationTest(unittest.TestCase):
    def test_run_verification_runs_all_configured_commands(self) -> None:
        config = BotConfig(
            check_commands=[
                'python -c "print(\'first\')"',
                'python -c "print(\'second\')"',
            ]
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            results = run_verification(config, Path(temp_dir))

        self.assertEqual([result.command for result in results], config.check_commands)
        self.assertIn("first", results[0].output)
        self.assertIn("second", results[1].output)

    def test_run_verification_raises_on_failure(self) -> None:
        config = BotConfig(
            check_commands=[
                'python -c "import sys; print(\'bad\'); sys.exit(3)"',
            ]
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaises(VerificationError) as context:
                run_verification(config, Path(temp_dir))

        self.assertEqual(context.exception.returncode, 3)
        self.assertIn("bad", context.exception.output)


if __name__ == "__main__":
    unittest.main()
