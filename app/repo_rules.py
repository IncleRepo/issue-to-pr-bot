"""래퍼가 사용할 저장소 규칙을 추론하는 모듈.

저장소 문서에서 브랜치 규칙, 커밋 규칙, PR 규칙,
Git sync 규칙과 검증 명령 후보를 함께 읽어낸다.
"""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path

from app.config import BotConfig, GitSyncRule
from app.slot_inference import (
    CONFLICT_SLOT_TERMS,
    GIT_ACTION_SLOT_LEXICON,
    GIT_PHASE_SLOT_LEXICON,
    VERIFICATION_SCOPE_SLOT_LEXICON,
    contains_any_term,
    pick_best_slot,
    split_text_segments,
)
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
CODE_BLOCK_REPLACEMENT_PATTERN = re.compile(r"```[^\n]*\n.*?\n```", re.DOTALL)
COMMAND_PATTERN = re.compile(
    r"^\s*(python|pytest|npm|pnpm|yarn|uv|poetry|go|cargo|dotnet|mvn|gradle)\b",
    re.IGNORECASE,
)

EXPLICIT_BRANCH_PATTERN = re.compile(
    r"(?:branch|branch name|branch format|branch pattern|branch template)\s*[:=-]\s*`([^`]+)`",
    re.IGNORECASE,
)
EXPLICIT_COMMIT_PATTERN = re.compile(
    r"(?:commit|commit message|commit format|commit pattern|commit template)\s*[:=-]\s*`([^`]+)`",
    re.IGNORECASE,
)
EXPLICIT_PR_TITLE_PATTERN = re.compile(
    r"(?:pr|pull request|pr title|pull request title)(?:\s+(?:format|pattern|template))?\s*[:=-]\s*`([^`]+)`",
    re.IGNORECASE,
)

BRANCH_SECTION_KEYWORDS = ("branch", "naming", "pattern", "template", "format")
COMMIT_SECTION_KEYWORDS = ("commit", "message", "format", "template")
PR_SECTION_KEYWORDS = ("pr", "pull request", "checklist", "rules", "guidelines")

PROTECTED_PATHS_HEADER_PATTERN = re.compile(
    r"^(protected paths?|protected files?|do not modify|forbidden paths?|safety|보호 경로|수정 금지)",
    re.IGNORECASE,
)
PROTECTED_PATH_LINE_PATTERN = re.compile(
    r"(do not modify|never commit|protected|forbidden|수정 금지|커밋 금지|보호)\b",
    re.IGNORECASE,
)
BACKTICK_PATTERN = re.compile(r"`([^`]+)`")

VERIFICATION_SECTION_KEYWORDS = (
    "verify",
    "validation",
    "checks",
    "checklist",
    "test",
    "verification",
    "testing",
    "check",
    "검증",
    "체크",
)
BRANCH_CANDIDATE_PATTERN = re.compile(
    r"(?<![A-Za-z0-9._/-])(main|master|develop|dev|release(?:/[a-z0-9._-]+)?|staging|production|prod|trunk)(?![A-Za-z0-9._/-])",
    re.IGNORECASE,
)
MERGE_ACTION_PATTERN = re.compile(r"(merge|merged|merging|병합|반영|합치)", re.IGNORECASE)
REBASE_ACTION_PATTERN = re.compile(r"(rebase|리베이스)", re.IGNORECASE)
SYNC_ACTION_PATTERN = re.compile(r"(sync|align|최신화|동기화|맞추)", re.IGNORECASE)
BEFORE_PR_PATTERN = re.compile(
    r"(before\s+(opening|creating)?\s*(a\s*)?(pull request|pr)|pr\s*전|pr\s*올리기\s*전|pr\s*생성\s*전|pull request\s*전|pull request\s*올리기\s*전)",
    re.IGNORECASE,
)
BEFORE_COMMIT_PATTERN = re.compile(r"(before\s+commit|커밋\s*전)", re.IGNORECASE)
BEFORE_MERGE_PATTERN = re.compile(r"(before\s+merg(?:e|ing)|prior\s+to\s+merge|merge\s*전|병합\s*전|브랜치\s*merge\s*전)", re.IGNORECASE)
CONFLICT_FREE_PATTERN = re.compile(r"(conflict[- ]?free|충돌[^.\n]{0,12}(없는지|없게)|충돌\s*확인)", re.IGNORECASE)
BASE_BRANCH_PATTERN = re.compile(
    r"(?:base branch|기준 브랜치|대상 브랜치)\s*(?:is|=|:|는|은)?\s*`?([A-Za-z0-9._/-]+)`?",
    re.IGNORECASE,
)
BRANCH_EXAMPLE_PATTERN = re.compile(
    r"^(feat|fix|docs|chore|refactor|style|test|perf|build|ci)/(\d+)-([a-z0-9][a-z0-9-]*)$",
    re.IGNORECASE,
)
COMMIT_EXAMPLE_PATTERN = re.compile(
    r"^(feat|fix|docs|chore|refactor|style|test|perf|build|ci):\s+.+$",
    re.IGNORECASE,
)
VERIFICATION_COMMAND_HINT_PATTERN = re.compile(
    r"(lint|test|check|format|build|compile|typecheck|validate|verify)",
    re.IGNORECASE,
)
PR_VERIFICATION_HINT_PATTERN = re.compile(
    r"(before\s+(opening|creating)?\s*(a\s*)?(pull request|pr)|pr\s*전|pr\s*올리기\s*전|확인합니다|확인하세요)",
    re.IGNORECASE,
)


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

    inferred_git_sync_rules = infer_git_sync_rules(documents)
    high_confidence_rules = [rule for rule in inferred_git_sync_rules if rule.confidence in {"high", "explicit"}]
    if high_confidence_rules:
        if not config.default_base_branch:
            before_pr_rule = next((rule for rule in high_confidence_rules if rule.phase == "before_pr"), None)
            replacements["default_base_branch"] = (before_pr_rule or high_confidence_rules[0]).base_branch
        if not config.git_sync_rules:
            replacements["git_sync_rules"] = high_confidence_rules
        if not config.git_sync_rule:
            replacements["git_sync_rule"] = next(
                (rule for rule in high_confidence_rules if rule.phase == "before_pr"),
                high_confidence_rules[0],
            )

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
        return infer_explicit_pattern(EXPLICIT_BRANCH_PATTERN, documents) or infer_branch_template_from_examples(documents)
    if template_key in {"codex_commit_message_template", "test_commit_message_template"}:
        return infer_explicit_pattern(EXPLICIT_COMMIT_PATTERN, documents) or infer_commit_template_from_examples(documents)
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
        for command in infer_yaml_check_commands(text):
            if command not in commands:
                commands.append(command)

        for section in extract_markdown_sections(text):
            heading = section["heading"].lower()
            if not is_verification_section(heading, section["body"]):
                continue

            for block in CODE_BLOCK_PATTERN.findall(section["body"]):
                for command in extract_commands_from_code_block(block):
                    if command not in commands:
                        commands.append(command)

            for command in extract_commands_from_text(section["body"]):
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
            if PROTECTED_PATHS_HEADER_PATTERN.search(section["heading"].strip()):
                for path in infer_paths_from_text(section["body"]):
                    if path not in paths:
                        paths.append(path)

        for path in infer_paths_from_lines(text):
            if path not in paths:
                paths.append(path)
    return paths


def infer_git_sync_rules(documents: dict[str, str]) -> list[GitSyncRule]:
    best_by_phase: dict[str, tuple[int, GitSyncRule]] = {}

    for source, text in documents.items():
        for rule in infer_git_sync_rules_from_text(text, source):
            score = git_sync_confidence_score(rule)
            existing = best_by_phase.get(rule.phase)
            if existing and existing[0] >= score:
                continue
            best_by_phase[rule.phase] = (score, rule)

    return [item[1] for item in sorted(best_by_phase.values(), key=lambda item: item[0], reverse=True)]


def infer_git_sync_rule(documents: dict[str, str]) -> GitSyncRule | None:
    rules = infer_git_sync_rules(documents)
    return rules[0] if rules else None


def infer_git_sync_rules_from_text(text: str, source: str) -> list[GitSyncRule]:
    sanitized = strip_markdown_code_blocks(text)
    candidates: list[GitSyncRule] = []

    for segment in split_text_segments(sanitized):
        phase, action, base_branch, require_conflict_free = infer_best_git_sync_candidate(segment)
        confidence = classify_git_sync_confidence(phase, action, base_branch)
        if confidence == "none":
            continue
        candidates.append(
            GitSyncRule(
                phase=phase or "before_pr",
                action=action or "merge",
                base_branch=base_branch or "main",
                require_conflict_free=require_conflict_free,
                confidence=confidence,
                source=source,
            )
        )

    if candidates:
        return candidates

    phase, action, base_branch, require_conflict_free = infer_best_git_sync_candidate(sanitized)
    confidence = classify_git_sync_confidence(phase, action, base_branch)
    if confidence == "none":
        return []

    return [
        GitSyncRule(
            phase=phase or "before_pr",
            action=action or "merge",
            base_branch=base_branch or "main",
            require_conflict_free=require_conflict_free,
            confidence=confidence,
            source=source,
        )
    ]


def strip_markdown_code_blocks(text: str) -> str:
    return CODE_BLOCK_REPLACEMENT_PATTERN.sub("\n", text)


def infer_best_git_sync_candidate(text: str) -> tuple[str | None, str | None, str | None, bool]:
    best_phase: str | None = None
    best_action: str | None = None
    best_branch: str | None = None
    best_conflict = False
    best_score = -1

    for segment in split_text_segments(text):
        phase = infer_git_sync_phase(segment)
        action = infer_git_sync_action(segment)
        branch = infer_git_sync_base_branch(segment)
        require_conflict_free = contains_any_term(segment, CONFLICT_SLOT_TERMS) or bool(CONFLICT_FREE_PATTERN.search(segment))

        score = 0
        if phase:
            score += 3
        if action:
            score += 3
        if branch:
            score += 3
        if require_conflict_free:
            score += 1
        if phase and action and branch:
            score += 2

        if score > best_score:
            best_phase = phase
            best_action = action
            best_branch = branch
            best_conflict = require_conflict_free
            best_score = score

    if best_score > 0:
        return best_phase, best_action, best_branch, best_conflict

    phase_decision = pick_best_slot(text, GIT_PHASE_SLOT_LEXICON, min_score=2)
    action_decision = pick_best_slot(text, GIT_ACTION_SLOT_LEXICON, min_score=2)
    return (
        phase_decision.value,
        action_decision.value,
        infer_git_sync_base_branch(text),
        contains_any_term(text, CONFLICT_SLOT_TERMS),
    )


def infer_branch_template_from_examples(documents: dict[str, str]) -> str | None:
    for text in documents.values():
        for section in extract_markdown_sections(text):
            heading = section["heading"].lower()
            for block in CODE_BLOCK_PATTERN.findall(section["body"]):
                examples = [line.strip() for line in block.splitlines() if line.strip()]
                if not examples:
                    continue
                heading_match = any(keyword in heading for keyword in BRANCH_SECTION_KEYWORDS)
                example_match = all(BRANCH_EXAMPLE_PATTERN.match(line) for line in examples)
                if example_match and (heading_match or len(examples) >= 2):
                    return "{commit_type}/{issue_number}-{slug}"
    return None


def infer_commit_template_from_examples(documents: dict[str, str]) -> str | None:
    for text in documents.values():
        for section in extract_markdown_sections(text):
            heading = section["heading"].lower()
            for block in CODE_BLOCK_PATTERN.findall(section["body"]):
                examples = [line.strip() for line in block.splitlines() if line.strip()]
                if not examples:
                    continue
                heading_match = any(keyword in heading for keyword in COMMIT_SECTION_KEYWORDS)
                example_match = all(COMMIT_EXAMPLE_PATTERN.match(line) for line in examples)
                if example_match and (heading_match or len(examples) >= 2):
                    return "{commit_type}: {issue_title}"
    return None


def is_verification_section(heading: str, body: str) -> bool:
    if any(keyword in heading for keyword in VERIFICATION_SECTION_KEYWORDS):
        return True
    if any(keyword in heading for keyword in PR_SECTION_KEYWORDS) and PR_VERIFICATION_HINT_PATTERN.search(body):
        return True
    if contains_any_term(heading, VERIFICATION_SCOPE_SLOT_LEXICON["frontend"]) and contains_any_term(body, ("lint", "build", "test", "format")):
        return True
    return False


def infer_git_sync_phase(text: str) -> str | None:
    if BEFORE_PR_PATTERN.search(text):
        return "before_pr"
    if BEFORE_COMMIT_PATTERN.search(text):
        return "before_commit"
    if BEFORE_MERGE_PATTERN.search(text):
        return "before_merge"
    lowered = text.lower()
    has_action = bool(REBASE_ACTION_PATTERN.search(text) or MERGE_ACTION_PATTERN.search(text) or SYNC_ACTION_PATTERN.search(text))
    has_branch = bool(BRANCH_CANDIDATE_PATTERN.search(text))
    if has_action and has_branch:
        if "pr" in lowered or "pull request" in lowered:
            return "before_pr"
        if lowered.count("merge") >= 2 or "merge 하기 전" in text or "병합하기 전" in text:
            return "before_merge"
    return pick_best_slot(text, GIT_PHASE_SLOT_LEXICON, min_score=2).value


def infer_git_sync_action(text: str) -> str | None:
    if REBASE_ACTION_PATTERN.search(text):
        return "rebase"
    if MERGE_ACTION_PATTERN.search(text):
        return "merge"
    decision = pick_best_slot(text, GIT_ACTION_SLOT_LEXICON, min_score=2)
    if decision.value:
        return decision.value
    if SYNC_ACTION_PATTERN.search(text):
        return "merge"
    return None


def infer_git_sync_base_branch(text: str) -> str | None:
    explicit_match = BASE_BRANCH_PATTERN.search(text)
    if explicit_match:
        return explicit_match.group(1).strip("` ").lower()

    for line in (line.strip() for line in text.splitlines() if line.strip()):
        if not (
            BEFORE_PR_PATTERN.search(line)
            or BEFORE_COMMIT_PATTERN.search(line)
            or MERGE_ACTION_PATTERN.search(line)
            or REBASE_ACTION_PATTERN.search(line)
            or SYNC_ACTION_PATTERN.search(line)
        ):
            continue
        branch_match = BRANCH_CANDIDATE_PATTERN.search(line)
        if branch_match:
            return branch_match.group(1).lower()

    branch_match = BRANCH_CANDIDATE_PATTERN.search(text)
    if branch_match:
        return branch_match.group(1).lower()
    return None


def classify_git_sync_confidence(phase: str | None, action: str | None, base_branch: str | None) -> str:
    if phase and action and base_branch:
        return "high"
    if action and base_branch:
        return "medium"
    return "none"


def git_sync_confidence_score(rule: GitSyncRule) -> int:
    return {"explicit": 3, "high": 2, "medium": 1, "low": 0}.get(rule.confidence, -1)


def infer_yaml_check_commands(text: str) -> list[str]:
    return infer_yaml_list(text, "check_commands")


def infer_yaml_list(text: str, key: str) -> list[str]:
    lines = text.splitlines()
    values: list[str] = []
    collecting = False
    base_indent = 0

    for raw_line in lines:
        line = strip_config_comment(raw_line).rstrip()
        if not line.strip():
            continue

        if line.strip().startswith(f"{key}:"):
            collecting = True
            base_indent = len(line) - len(line.lstrip())
            continue

        if not collecting:
            continue

        current_indent = len(line) - len(line.lstrip())
        stripped = line.strip()
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
        if stripped and COMMAND_PATTERN.match(stripped) and is_verification_command(stripped):
            commands.append(stripped)
    return commands


def extract_commands_from_text(text: str) -> list[str]:
    commands: list[str] = []
    text_without_code_blocks = CODE_BLOCK_PATTERN.sub("", text)
    for raw_line in text_without_code_blocks.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        candidate = stripped[2:].strip() if stripped.startswith("- ") else stripped
        if COMMAND_PATTERN.match(candidate) and is_verification_command(candidate):
            commands.append(candidate)
            continue
        for inline in BACKTICK_PATTERN.findall(stripped):
            candidate = inline.strip()
            if COMMAND_PATTERN.match(candidate) and is_verification_command(candidate):
                commands.append(candidate)
    return commands


def is_verification_command(command: str) -> bool:
    if is_setup_command(command):
        return False
    return bool(VERIFICATION_COMMAND_HINT_PATTERN.search(command))


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


def strip_config_comment(line: str) -> str:
    quote: str | None = None
    escaped = False
    for index, char in enumerate(line):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if quote:
            if char == quote:
                quote = None
            continue
        if char in {"'", '"'}:
            quote = char
            continue
        if char == "#":
            return line[:index]
    return line

