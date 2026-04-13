import re
from dataclasses import replace
from pathlib import Path

from app.config import BotConfig
from app.verification_policy import is_setup_command


RULE_SOURCES = [
    "AGENTS.md",
    "CONTRIBUTING.md",
    "README.md",
    ".github/pull_request_template.md",
    ".github/PULL_REQUEST_TEMPLATE.md",
]

TEMPLATE_KEY_PATTERNS = {
    "branch_name_template": re.compile(r"branch_name_template:\s*([\"']?)(.+?)\1\s*$", re.MULTILINE),
    "pr_title_template": re.compile(r"pr_title_template:\s*([\"']?)(.+?)\1\s*$", re.MULTILINE),
    "codex_commit_message_template": re.compile(
        r"codex_commit_message_template:\s*([\"']?)(.+?)\1\s*$",
        re.MULTILINE,
    ),
    "test_commit_message_template": re.compile(
        r"test_commit_message_template:\s*([\"']?)(.+?)\1\s*$",
        re.MULTILINE,
    ),
}

SECTION_HEADING_PATTERN = re.compile(r"^#{1,6}\s+(.*)$", re.MULTILINE)
CODE_BLOCK_PATTERN = re.compile(r"```[^\n]*\n(.*?)\n```", re.DOTALL)
COMMAND_PATTERN = re.compile(r"^\s*(python|pytest|npm|pnpm|yarn|uv|poetry|go|cargo|dotnet|mvn|gradle)\b", re.IGNORECASE)
EXPLICIT_BRANCH_PATTERN = re.compile(r"branch(?: name)?(?: format| pattern| template)?\s*[:=-]\s*`([^`]+)`", re.IGNORECASE)
EXPLICIT_COMMIT_PATTERN = re.compile(r"commit(?: message)?(?: format| pattern| template)?\s*[:=-]\s*`([^`]+)`", re.IGNORECASE)
EXPLICIT_PR_TITLE_PATTERN = re.compile(
    r"(?:pr|pull request)(?: title)?(?: format| pattern| template)?\s*[:=-]\s*`([^`]+)`",
    re.IGNORECASE,
)
PROTECTED_PATHS_HEADER_PATTERN = re.compile(
    r"^(protected paths?|protected files?|do not modify|forbidden paths?|safety|보호 경로|수정 금지)",
    re.IGNORECASE,
)
PROTECTED_PATH_LINE_PATTERN = re.compile(
    r"(do not modify|never commit|protected|forbidden|수정 금지|커밋 금지|보호)\b",
    re.IGNORECASE,
)
REQUIRED_LINE_PATTERN = re.compile(r"(required|requires|must provide|필수|반드시)\b", re.IGNORECASE)
BACKTICK_PATTERN = re.compile(r"`([^`]+)`")


def resolve_bot_config(workspace: Path, config: BotConfig) -> BotConfig:
    documents = load_rule_documents(workspace)

    replacements: dict[str, object] = {}

    branch_name_template = infer_template_value("branch_name_template", documents)
    if branch_name_template:
        replacements["branch_name_template"] = branch_name_template

    pr_title_template = infer_template_value("pr_title_template", documents)
    if pr_title_template:
        replacements["pr_title_template"] = pr_title_template

    codex_commit_template = infer_template_value("codex_commit_message_template", documents)
    if codex_commit_template:
        replacements["codex_commit_message_template"] = codex_commit_template

    test_commit_template = infer_template_value("test_commit_message_template", documents)
    if test_commit_template:
        replacements["test_commit_message_template"] = test_commit_template

    inferred_checks = infer_verification_commands(documents)
    if inferred_checks:
        replacements["check_commands"] = inferred_checks

    inferred_protected_paths = infer_protected_paths(documents)
    if inferred_protected_paths:
        replacements["protected_paths"] = merge_unique(config.protected_paths, inferred_protected_paths)

    if not replacements:
        return config
    return replace(config, **replacements)


def load_rule_documents(workspace: Path) -> dict[str, str]:
    documents: dict[str, str] = {}
    for relative_path in RULE_SOURCES:
        path = workspace / relative_path
        if path.exists() and path.is_file():
            documents[relative_path] = path.read_text(encoding="utf-8", errors="replace")
    return documents


def infer_template_value(template_key: str, documents: dict[str, str]) -> str | None:
    explicit = infer_yaml_template_value(template_key, documents)
    if explicit:
        return explicit

    if template_key == "branch_name_template":
        return infer_explicit_pattern(EXPLICIT_BRANCH_PATTERN, documents)
    if template_key in {"codex_commit_message_template", "test_commit_message_template"}:
        return infer_explicit_pattern(EXPLICIT_COMMIT_PATTERN, documents)
    if template_key == "pr_title_template":
        return infer_explicit_pattern(EXPLICIT_PR_TITLE_PATTERN, documents)
    return None


def infer_yaml_template_value(template_key: str, documents: dict[str, str]) -> str | None:
    pattern = TEMPLATE_KEY_PATTERNS[template_key]
    for text in documents.values():
        match = pattern.search(text)
        if match:
            return match.group(2).strip()
    return None


def infer_explicit_pattern(pattern: re.Pattern[str], documents: dict[str, str]) -> str | None:
    for text in documents.values():
        match = pattern.search(text)
        if match:
            return match.group(1).strip()
    return None


def infer_verification_commands(documents: dict[str, str]) -> list[str]:
    commands: list[str] = []
    for text in documents.values():
        explicit = infer_yaml_check_commands(text)
        for command in explicit:
            if command not in commands:
                commands.append(command)

        for section in extract_markdown_sections(text):
            heading = section["heading"].lower()
            if not any(keyword in heading for keyword in ("verification", "verify", "검증", "test", "테스트")):
                continue
            for block in CODE_BLOCK_PATTERN.findall(section["body"]):
                for command in extract_commands_from_code_block(block):
                    if command not in commands:
                        commands.append(command)
    return commands


def infer_protected_paths(documents: dict[str, str]) -> list[str]:
    paths: list[str] = []
    for text in documents.values():
        for path in infer_yaml_list(text, "protected_paths"):
            if is_path_pattern(path) and path not in paths:
                paths.append(path)

        for section in extract_markdown_sections(text):
            heading = section["heading"].strip()
            if PROTECTED_PATHS_HEADER_PATTERN.search(heading):
                for path in infer_paths_from_text(section["body"]):
                    if path not in paths:
                        paths.append(path)

        for path in infer_paths_from_lines(text):
            if path not in paths:
                paths.append(path)

    return paths


def infer_yaml_check_commands(text: str) -> list[str]:
    return infer_yaml_list(text, "check_commands")


def infer_yaml_list(text: str, key: str) -> list[str]:
    lines = text.splitlines()
    values: list[str] = []
    collecting = False
    base_indent = 0

    for raw_line in lines:
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped:
            continue

        if stripped.startswith(f"{key}:"):
            collecting = True
            base_indent = len(line) - len(line.lstrip())
            continue

        if collecting:
            current_indent = len(line) - len(line.lstrip())
            if current_indent <= base_indent and not stripped.startswith("- "):
                collecting = False
                continue

            if stripped.startswith("- "):
                value = stripped[2:].strip().strip('"').strip("'")
                if value:
                    values.append(value)

    return values


def extract_markdown_sections(text: str) -> list[dict[str, str]]:
    matches = list(SECTION_HEADING_PATTERN.finditer(text))
    sections: list[dict[str, str]] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        sections.append({"heading": match.group(1).strip(), "body": text[start:end]})
    return sections


def extract_commands_from_code_block(block: str) -> list[str]:
    commands: list[str] = []
    for line in block.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if COMMAND_PATTERN.match(stripped) and not is_setup_command(stripped):
            commands.append(stripped)
    return commands


def infer_paths_from_text(text: str) -> list[str]:
    paths: list[str] = []
    for path in infer_paths_from_lines(text):
        if path not in paths:
            paths.append(path)
    return paths


def infer_paths_from_lines(text: str) -> list[str]:
    paths: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if not PROTECTED_PATH_LINE_PATTERN.search(line) and not line.startswith("- "):
            continue
        for candidate in BACKTICK_PATTERN.findall(line):
            candidate = candidate.strip().strip("`")
            if is_path_pattern(candidate) and candidate not in paths:
                paths.append(candidate)
        if line.startswith("- "):
            candidate = line[2:].strip().strip('"').strip("'").strip("`")
            if is_path_pattern(candidate) and candidate not in paths:
                paths.append(candidate)
    return paths


def is_path_pattern(value: str) -> bool:
    if not value or " " in value:
        return False
    if any(token in value for token in ("{issue_", "{slug", "{branch_", "{{")):
        return False
    return (
        value.startswith(".")
        or value.startswith("*")
        or "/" in value
        or "\\" in value
        or value.endswith((".env", ".pem", ".key", ".p12", ".pfx"))
    )


def merge_unique(base_values: list[str], extra_values: list[str]) -> list[str]:
    merged: list[str] = []
    for value in [*base_values, *extra_values]:
        if value not in merged:
            merged.append(value)
    return merged
