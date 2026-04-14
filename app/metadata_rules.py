"""Metadata inference for issues and pull requests."""

from __future__ import annotations

import re
from fnmatch import fnmatch
from pathlib import Path

from app.domain.models import IssueRequest, MetadataPlan
from app.repo_rules import extract_markdown_sections, load_rule_documents


LABEL_SYNONYMS: dict[str, tuple[str, ...]] = {
    "bug": ("bug", "fix", "error", "issue", "regression", "broken"),
    "enhancement": ("enhancement", "feature", "implement", "add", "new feature"),
    "documentation": ("documentation", "docs", "readme", "guide"),
    "refactor": ("refactor", "cleanup", "restructure"),
    "tests": ("tests", "test", "qa"),
    "automation": ("automation", "bot", "workflow", "github actions", "ci"),
    "dependencies": ("dependencies", "dependency", "requirements", "package", "lockfile"),
}

DOC_LINE_PATTERNS = {
    "issue_labels": re.compile(r"(?:issue labels?|issue metadata labels?)\s*[:=-]\s*(.+)$", re.IGNORECASE),
    "pr_labels": re.compile(r"(?:pr labels?|pull request labels?)\s*[:=-]\s*(.+)$", re.IGNORECASE),
    "labels": re.compile(r"(?<!issue )(?:labels?)\s*[:=-]\s*(.+)$", re.IGNORECASE),
    "assignees": re.compile(r"assignees?\s*[:=-]\s*(.+)$", re.IGNORECASE),
    "reviewers": re.compile(r"reviewers?\s*[:=-]\s*(.+)$", re.IGNORECASE),
    "team_reviewers": re.compile(r"(?:team reviewers?|review teams?)\s*[:=-]\s*(.+)$", re.IGNORECASE),
    "milestone": re.compile(r"milestone\s*[:=-]\s*(.+)$", re.IGNORECASE),
}

HEADING_MATCHERS = {
    "issue_labels": ("issue labels", "issue label"),
    "pr_labels": ("pr labels", "pull request labels"),
    "labels": ("labels",),
    "assignees": ("assignees", "owners"),
    "reviewers": ("reviewers",),
    "team_reviewers": ("team reviewers", "review teams"),
    "milestone": ("milestone",),
}


def infer_issue_metadata(workspace: Path, request: IssueRequest) -> MetadataPlan:
    documents = load_rule_documents(workspace)
    explicit = infer_explicit_metadata(documents)

    issue_labels = dedupe(
        explicit["issue_labels"] + explicit["shared_labels"] + infer_fallback_issue_labels(request, changed_files=[])
    )
    milestone_title = explicit["milestone"] or infer_fallback_milestone(request)
    return MetadataPlan(
        issue_labels=issue_labels,
        pr_labels=[],
        assignees=explicit["assignees"],
        reviewers=[],
        team_reviewers=[],
        milestone_title=milestone_title,
    )


def infer_pull_request_metadata(workspace: Path, request: IssueRequest, changed_files: list[str]) -> MetadataPlan:
    documents = load_rule_documents(workspace)
    explicit = infer_explicit_metadata(documents)
    codeowners_users, codeowners_teams = infer_codeowners_reviewers(workspace, changed_files)

    issue_labels = dedupe(explicit["issue_labels"] + infer_fallback_issue_labels(request, changed_files))
    pr_labels = dedupe(
        explicit["pr_labels"]
        + explicit["shared_labels"]
        + infer_fallback_pr_labels(request, changed_files)
    )
    reviewers = dedupe(explicit["reviewers"] + codeowners_users)
    team_reviewers = dedupe(explicit["team_reviewers"] + codeowners_teams)
    milestone_title = explicit["milestone"] or infer_fallback_milestone(request)
    return MetadataPlan(
        issue_labels=issue_labels,
        pr_labels=pr_labels,
        assignees=explicit["assignees"],
        reviewers=reviewers,
        team_reviewers=team_reviewers,
        milestone_title=milestone_title,
    )


def infer_explicit_metadata(documents: dict[str, str]) -> dict[str, list[str] | str | None]:
    values = {
        "issue_labels": [],
        "pr_labels": [],
        "shared_labels": [],
        "assignees": [],
        "reviewers": [],
        "team_reviewers": [],
        "milestone": None,
    }
    for text in documents.values():
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            for key, pattern in DOC_LINE_PATTERNS.items():
                match = pattern.search(stripped)
                if not match:
                    continue
                parsed = parse_metadata_values(match.group(1))
                if key == "milestone":
                    values["milestone"] = parsed[0] if parsed else None
                elif key == "labels":
                    values["shared_labels"] = dedupe(values["shared_labels"] + parsed)
                else:
                    values[key] = dedupe(values[key] + parsed)

        for section in extract_markdown_sections(text):
            heading = section["heading"].strip().lower()
            parsed_values = parse_metadata_values(section["body"])
            for key, candidates in HEADING_MATCHERS.items():
                if any(candidate in heading for candidate in candidates):
                    if key == "milestone":
                        values["milestone"] = parsed_values[0] if parsed_values else values["milestone"]
                    elif key == "labels":
                        values["shared_labels"] = dedupe(values["shared_labels"] + parsed_values)
                    else:
                        values[key] = dedupe(values[key] + parsed_values)
    return values


def parse_metadata_values(raw_text: str) -> list[str]:
    values: list[str] = []
    for line in raw_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("- "):
            stripped = stripped[2:].strip()
        if stripped.startswith("`") and stripped.endswith("`") and len(stripped) >= 2:
            stripped = stripped[1:-1].strip()
        for token in re.split(r"[,\n;]", stripped):
            candidate = token.strip().strip("`")
            if not candidate:
                continue
            if candidate.lower().startswith("and "):
                candidate = candidate[4:].strip()
            if candidate.lower().startswith("or "):
                candidate = candidate[3:].strip()
            if candidate and candidate not in values:
                values.append(candidate)
    return values


def infer_fallback_issue_labels(request: IssueRequest, changed_files: list[str]) -> list[str]:
    text = " ".join(
        part
        for part in (request.issue_title, request.issue_body, request.comment_body)
        if part
    ).lower()
    labels: list[str] = []
    if any(word in text for word in ("bug", "fix", "error", "broken", "회귀", "오류", "버그")):
        labels.append("bug")
    if any(word in text for word in ("doc", "docs", "readme", "문서")) or is_docs_only_change(changed_files):
        labels.append("documentation")
    if any(word in text for word in ("refactor", "cleanup", "리팩토링")):
        labels.append("refactor")
    if any(word in text for word in ("test", "테스트")) or is_test_heavy_change(changed_files):
        labels.append("tests")
    if any(word in text for word in ("workflow", "action", "automation", "bot", "자동화")) or touches_automation(changed_files):
        labels.append("automation")
    if any(word in text for word in ("dependency", "dependencies", "requirements", "패키지", "의존성")) or touches_dependencies(changed_files):
        labels.append("dependencies")
    if not labels and any(word in text for word in ("implement", "add", "feature", "추가", "구현")):
        labels.append("enhancement")
    return dedupe(labels)


def infer_fallback_pr_labels(request: IssueRequest, changed_files: list[str]) -> list[str]:
    labels = infer_fallback_issue_labels(request, changed_files)
    labels.append("automation")
    if changed_files and is_docs_only_change(changed_files):
        labels.append("documentation")
    return dedupe(labels)


def infer_fallback_milestone(request: IssueRequest) -> str | None:
    text = " ".join(
        part
        for part in (request.issue_title, request.issue_body, request.comment_body)
        if part
    )
    match = re.search(r"(?:milestone|마일스톤)\s*[:=-]\s*([^\n\r]+)", text, re.IGNORECASE)
    if not match:
        return None
    return match.group(1).strip().strip("`").strip()


def infer_codeowners_reviewers(workspace: Path, changed_files: list[str]) -> tuple[list[str], list[str]]:
    codeowners = load_codeowners_entries(workspace)
    if not codeowners or not changed_files:
        return [], []

    matched_owners: list[str] = []
    for path in changed_files:
        matched_owners_for_path: list[str] = []
        normalized_path = path.replace("\\", "/")
        for pattern, owners in codeowners:
            if codeowners_pattern_matches(pattern, normalized_path):
                matched_owners_for_path = owners
        matched_owners.extend(matched_owners_for_path)

    users: list[str] = []
    teams: list[str] = []
    for owner in dedupe(matched_owners):
        if not owner.startswith("@"):
            continue
        if "/" in owner:
            teams.append(owner[1:])
        else:
            users.append(owner[1:])
    return users, teams


def load_codeowners_entries(workspace: Path) -> list[tuple[str, list[str]]]:
    for relative_path in (".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS"):
        path = workspace / relative_path
        if not path.exists() or not path.is_file():
            continue
        entries: list[tuple[str, list[str]]] = []
        for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            stripped = raw_line.split("#", 1)[0].strip()
            if not stripped:
                continue
            parts = stripped.split()
            if len(parts) < 2:
                continue
            entries.append((parts[0], parts[1:]))
        return entries
    return []


def codeowners_pattern_matches(pattern: str, path: str) -> bool:
    normalized = pattern.strip().replace("\\", "/")
    if normalized.startswith("/"):
        normalized = normalized[1:]
    if normalized.endswith("/"):
        normalized = normalized + "**"

    candidates = [normalized]
    if "/" not in normalized and not normalized.startswith("**/"):
        candidates.append(f"**/{normalized}")
    return any(fnmatch(path, candidate) for candidate in candidates)


def is_docs_only_change(changed_files: list[str]) -> bool:
    if not changed_files:
        return False
    return all(path.lower().startswith(("docs/", ".github/")) or path.lower().endswith((".md", ".txt")) for path in changed_files)


def is_test_heavy_change(changed_files: list[str]) -> bool:
    return any("/tests" in f"/{path.lower()}" or path.lower().startswith("tests/") for path in changed_files)


def touches_automation(changed_files: list[str]) -> bool:
    return any(path.lower().startswith(".github/") or "workflow" in path.lower() or "bot" in path.lower() for path in changed_files)


def touches_dependencies(changed_files: list[str]) -> bool:
    dependency_files = {
        "requirements.txt",
        "package.json",
        "package-lock.json",
        "pnpm-lock.yaml",
        "poetry.lock",
        "pyproject.toml",
    }
    return any(Path(path).name.lower() in dependency_files for path in changed_files)


def dedupe(values: list[str]) -> list[str]:
    seen: list[str] = []
    for value in values:
        candidate = value.strip()
        if not candidate or candidate in seen:
            continue
        seen.append(candidate)
    return seen
