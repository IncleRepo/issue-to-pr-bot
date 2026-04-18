from pathlib import Path
import unittest


class WorkerTemplateTests(unittest.TestCase):
    def test_trigger_association_allowlist_includes_public_commenters(self) -> None:
        template = (
            Path(__file__).resolve().parent.parent
            / "app"
            / "worker_templates"
            / "src"
            / "index.js.example"
        ).read_text(encoding="utf-8")

        self.assertIn('"CONTRIBUTOR"', template)
        self.assertIn('"FIRST_TIMER"', template)
        self.assertIn('"FIRST_TIME_CONTRIBUTOR"', template)
        self.assertIn('"NONE"', template)


if __name__ == "__main__":
    unittest.main()
