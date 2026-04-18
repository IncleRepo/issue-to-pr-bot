import html
import re
import shutil
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from app.bot import IssueRequest
from app.output_artifacts import get_workspace_attachment_root, resolve_workspace_root


MAX_ATTACHMENTS = 5
MAX_ATTACHMENT_BYTES = 3_000_000
MAX_TEXT_ATTACHMENT_CHARS = 6_000

TEXT_EXTENSIONS = {
    ".cfg",
    ".csv",
    ".env",
    ".htm",
    ".html",
    ".ini",
    ".json",
    ".log",
    ".md",
    ".py",
    ".sql",
    ".toml",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
TEXT_CONTENT_TYPES = {
    "application/json",
    "application/xml",
    "text/csv",
    "text/html",
    "text/markdown",
    "text/plain",
    "text/xml",
}

MARKDOWN_LINK_PATTERN = re.compile(r"!?\[[^\]]*\]\((https?://[^)\s]+)\)")
PLAIN_URL_PATTERN = re.compile(r"https?://[^\s<>()\[\]]+")
HTML_TITLE_PATTERN = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
HTML_SCRIPT_STYLE_PATTERN = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
HTML_TAG_PATTERN = re.compile(r"<[^>]+>")
WHITESPACE_PATTERN = re.compile(r"\s+")


@dataclass(frozen=True)
class AttachmentInfo:
    source: str
    url: str
    filename: str
    local_path: str
    kind: str
    content: str = ""
    summary: str = ""
    error: str | None = None


@dataclass(frozen=True)
class AttachmentSkip:
    url: str
    reason: str


@dataclass(frozen=True)
class AttachmentContext:
    attachments: list[AttachmentInfo]
    skipped: list[AttachmentSkip]


def collect_attachment_context(request: IssueRequest, workspace: Path | None = None) -> AttachmentContext:
    attachment_dir = prepare_attachment_dir(request, workspace)
    attachments: list[AttachmentInfo] = []
    skipped: list[AttachmentSkip] = []

    for source, text in (("issue", request.issue_body), ("comment", request.comment_body)):
        for url in extract_attachment_urls(text):
            if any(existing.url == url for existing in attachments):
                continue
            if len(attachments) >= MAX_ATTACHMENTS:
                skipped.append(AttachmentSkip(url=url, reason="attachment limit exceeded"))
                continue

            try:
                attachments.append(download_attachment(source, url, attachment_dir))
            except Exception as error:
                skipped.append(AttachmentSkip(url=url, reason=str(error)))

    return AttachmentContext(attachments=attachments, skipped=skipped)


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


def prepare_attachment_dir(request: IssueRequest, workspace: Path | None = None) -> Path:
    root = get_workspace_attachment_root(resolve_workspace_root(workspace))
    directory = root / f"comment-{request.comment_id or 'none'}"
    shutil.rmtree(directory, ignore_errors=True)
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
        raise RuntimeError(f"unsupported attachment type: {content_type}")

    local_path = attachment_dir / filename
    local_path.write_bytes(data)
    content, summary = extract_attachment_content(kind, data)

    return AttachmentInfo(
        source=source,
        url=url,
        filename=filename,
        local_path=str(local_path),
        kind=kind,
        content=content,
        summary=summary,
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
            raise RuntimeError("attachment exceeded maximum allowed size")
        chunks.append(chunk)
    return b"".join(chunks)


def classify_attachment(filename: str, content_type: str) -> str:
    extension = Path(filename).suffix.lower()
    if content_type == "text/html" or extension in {".html", ".htm"}:
        return "web"
    if content_type.startswith("text/") or content_type in TEXT_CONTENT_TYPES or extension in TEXT_EXTENSIONS:
        return "text"
    if content_type.startswith("image/") or extension in IMAGE_EXTENSIONS:
        return "image"
    if content_type == "application/pdf" or extension == ".pdf":
        return "pdf"
    return "unsupported"


def extract_attachment_content(kind: str, data: bytes) -> tuple[str, str]:
    if kind == "text":
        content = data.decode("utf-8", errors="replace")[:MAX_TEXT_ATTACHMENT_CHARS]
        return content, summarize_text(content)
    if kind == "web":
        text = extract_html_text(data.decode("utf-8", errors="replace"))
        content = text[:MAX_TEXT_ATTACHMENT_CHARS]
        return content, summarize_text(content)
    return "", ""


def extract_html_text(raw_html: str) -> str:
    title_match = HTML_TITLE_PATTERN.search(raw_html)
    title = html.unescape(title_match.group(1)).strip() if title_match else ""

    without_scripts = HTML_SCRIPT_STYLE_PATTERN.sub(" ", raw_html)
    text = HTML_TAG_PATTERN.sub(" ", without_scripts)
    text = html.unescape(text)
    text = WHITESPACE_PATTERN.sub(" ", text).strip()
    if title and not text.startswith(title):
        text = f"{title}\n\n{text}"
    return text


def summarize_text(content: str) -> str:
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    if not lines:
        return "(empty)"
    summary = " ".join(lines[:3])
    return summary[:220]


def format_attachment_context(context: AttachmentContext) -> str:
    if not context.attachments and not context.skipped:
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
        if attachment.summary:
            sections.append(f"  - summary: {attachment.summary}")
        if attachment.kind in {"text", "web"}:
            sections.extend(
                [
                    "  - content:",
                    "```text",
                    attachment.content.strip() or "(empty)",
                    "```",
                ]
            )

    if context.skipped:
        sections.extend(["", "Skipped attachment URLs:"])
        for skipped in context.skipped:
            sections.append(f"- {skipped.url} ({skipped.reason})")

    return "\n".join(sections)
