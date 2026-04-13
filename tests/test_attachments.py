import functools
import http.server
import tempfile
import threading
import unittest
from pathlib import Path

from app.attachments import collect_attachment_context, extract_attachment_urls, format_attachment_context
from app.bot import IssueRequest


class AttachmentsTest(unittest.TestCase):
    def test_extract_attachment_urls_deduplicates_markdown_and_plain_urls(self) -> None:
        text = (
            "첨부 확인: [guide](https://example.com/guide.md) "
            "그리고 다시 https://example.com/guide.md."
        )

        self.assertEqual(extract_attachment_urls(text), ["https://example.com/guide.md"])

    def test_collect_attachment_context_downloads_text_attachment(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            root.joinpath("guide.md").write_text("# Guide\n\nhello", encoding="utf-8")
            server, base_url = start_static_server(root)
            try:
                request = IssueRequest(
                    repository="IncleRepo/issue-to-pr-bot",
                    issue_number=10,
                    issue_title="Attachment test",
                    issue_body="",
                    comment_body=f"문서 참고: {base_url}/guide.md",
                    comment_author="IncleRepo",
                    comment_id=55,
                )

                context = collect_attachment_context(request)
                formatted = format_attachment_context(context)
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(len(context.attachments), 1)
        self.assertEqual(context.attachments[0].kind, "text")
        self.assertIn("# Guide", context.attachments[0].content)
        self.assertIn("guide.md", formatted)
        self.assertIn("saved at:", formatted)
        self.assertEqual(context.skipped, [])

    def test_collect_attachment_context_extracts_html_page_text(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            root.joinpath("spec.html").write_text(
                "<html><head><title>API Spec</title></head><body><h1>Auth</h1><p>Use bearer token.</p></body></html>",
                encoding="utf-8",
            )
            server, base_url = start_static_server(root)
            try:
                request = IssueRequest(
                    repository="IncleRepo/issue-to-pr-bot",
                    issue_number=11,
                    issue_title="HTML attachment",
                    issue_body=f"{base_url}/spec.html",
                    comment_body="",
                    comment_author="IncleRepo",
                    comment_id=56,
                )

                context = collect_attachment_context(request)
            finally:
                server.shutdown()
                server.server_close()

        self.assertEqual(context.attachments[0].kind, "web")
        self.assertIn("API Spec", context.attachments[0].content)
        self.assertIn("Use bearer token.", context.attachments[0].content)

    def test_collect_attachment_context_records_skip_reasons(self) -> None:
        request = IssueRequest(
            repository="IncleRepo/issue-to-pr-bot",
            issue_number=12,
            issue_title="Bad attachment",
            issue_body="https://127.0.0.1:1/not-found.txt",
            comment_body="",
            comment_author="IncleRepo",
            comment_id=57,
        )

        context = collect_attachment_context(request)

        self.assertEqual(context.attachments, [])
        self.assertEqual(len(context.skipped), 1)
        self.assertIn("127.0.0.1:1", context.skipped[0].url)
        self.assertTrue(context.skipped[0].reason)


def start_static_server(root: Path) -> tuple[http.server.ThreadingHTTPServer, str]:
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(root))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    return server, f"http://{host}:{port}"


if __name__ == "__main__":
    unittest.main()
