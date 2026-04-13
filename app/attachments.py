import re
import tempfile
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from app.bot import IssueRequest


MAX_ATTACHMENTS = 5
MAX_ATTACHMENT_BYTES = 3_000_000
MAX_TEXT_ATTACHMENT_CHARS = 6_000

TEXT_EXTENSIONS = {".md", ".txt", ".json", ".yaml", ".yml", ".csv", ".log", ".xml"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
TEXT_CONTENT_TYPES = {
    "application/json",
    "application/xml",
    "text/csv",
    "text/markdown",
    "text/plain",
    "text/xml",
}

MARKDOWN_LINK_PATTERN = re.compile(r"!?\[[^\]]*\]\((https?://[^)\s]+)\)")
PLAIN_URL_PATTERN = re.compile(r"https?://[^\s<>()\[\]]+")


@dataclass(frozen=True)
class AttachmentInfo:
    source: str
    url: str
    filename: str
    local_path: str
    kind: str
    content: str = ""
    error: str | None = None


@dataclass(frozen=True)
class AttachmentContext:
    attachments: list[AttachmentInfo]
    skipped_urls: list[str]


def collect_attachment_context(request: IssueRequest) -> AttachmentContext:
    attachment_dir = prepare_attachment_dir(request)
    attachments: list[AttachmentInfo] = []
    skipped_urls: list[str] = []

    for source, text in (("issue", request.issue_body), ("comment", request.comment_body)):
        for url in extract_attachment_urls(text):
            if any(existing.url == url for existing in attachments):
                continue
            if len(attachments) >= MAX_ATTACHMENTS:
                skipped_urls.append(url)
                continue

            try:
                attachments.append(download_attachment(source, url, attachment_dir))
            except Exception:
                skipped_urls.append(url)

    return AttachmentContext(attachments=attachments, skipped_urls=skipped_urls)


def extract_attachment_urls(text: str) -> list[str]:
    if not text.strip():
        return []

    urls: list[str] = []
    for match in MARKDOWN_LINK_PATTERN.findall(text):
        urls.append(clean_url(match))
    for match in PLAIN_URL_PATTERN.findall(text):
        cleaned = clean_url(match)
        if cleaned not in urls:
            urls.append(cleaned)
    return [url for url in urls if url]


def clean_url(url: str) -> str:
    return url.rstrip(".,;:!?)]}")


def prepare_attachment_dir(request: IssueRequest) -> Path:
    root = Path(tempfile.gettempdir()) / "issue-to-pr-bot-attachments"
    directory = root / f"issue-{request.issue_number}-comment-{request.comment_id or 'none'}"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def download_attachment(source: str, url: str, attachment_dir: Path) -> AttachmentInfo:
    request = urllib.request.Request(url, headers={"User-Agent": "issue-to-pr-bot"})
    with urllib.request.urlopen(request, timeout=20) as response:
        content_type = response.headers.get_content_type()
        filename = determine_filename(url, response.headers)
        data = read_limited(response)

    kind = classify_attachment(filename, content_type)
    if kind == "unsupported":
        raise RuntimeError(f"Unsupported attachment type: {content_type}")

    local_path = attachment_dir / filename
    local_path.write_bytes(data)
    content = ""
    if kind == "text":
        content = data.decode("utf-8", errors="replace")[:MAX_TEXT_ATTACHMENT_CHARS]

    return AttachmentInfo(
        source=source,
        url=url,
        filename=filename,
        local_path=str(local_path),
        kind=kind,
        content=content,
    )


def determine_filename(url: str, headers) -> str:
    parsed = urllib.parse.urlparse(url)
    filename = Path(parsed.path).name or "attachment"

    disposition = headers.get("Content-Disposition", "")
    if "filename=" in disposition:
        candidate = disposition.split("filename=", 1)[1].strip().strip('"')
        if candidate:
            filename = Path(candidate).name

    if not Path(filename).suffix:
        content_type = headers.get_content_type()
        if content_type.startswith("image/"):
            filename += "." + content_type.split("/", 1)[1]
        elif content_type == "application/pdf":
            filename += ".pdf"
        elif content_type in TEXT_CONTENT_TYPES:
            filename += ".txt"

    return filename


def read_limited(response) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = response.read(65_536)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_ATTACHMENT_BYTES:
            raise RuntimeError("Attachment exceeded maximum allowed size.")
        chunks.append(chunk)
    return b"".join(chunks)


def classify_attachment(filename: str, content_type: str) -> str:
    extension = Path(filename).suffix.lower()
    if content_type.startswith("text/") or content_type in TEXT_CONTENT_TYPES or extension in TEXT_EXTENSIONS:
        return "text"
    if content_type.startswith("image/") or extension in IMAGE_EXTENSIONS:
        return "image"
    if content_type == "application/pdf" or extension == ".pdf":
        return "pdf"
    return "unsupported"


def format_attachment_context(context: AttachmentContext) -> str:
    if not context.attachments and not context.skipped_urls:
        return "No supported issue or comment attachments were collected."

    sections = ["Issue and comment attachments:"]
    for attachment in context.attachments:
        sections.extend(
            [
                "",
                f"- {attachment.source}: {attachment.filename} ({attachment.kind})",
                f"  - saved at: {attachment.local_path}",
                f"  - source url: {attachment.url}",
            ]
        )
        if attachment.kind == "text":
            sections.extend(
                [
                    "  - content:",
                    "```text",
                    attachment.content.strip() or "(empty)",
                    "```",
                ]
            )

    if context.skipped_urls:
        sections.extend(
            [
                "",
                "Skipped attachment URLs:",
                *[f"- {url}" for url in context.skipped_urls],
            ]
        )

    return "\n".join(sections)
