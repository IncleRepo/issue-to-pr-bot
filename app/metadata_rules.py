"""이슈와 PR에 붙일 메타데이터를 추론하는 도우미."""

from __future__ import annotations

import re
from fnmatch import fnmatch
from pathlib import Path

from app.domain.models import IssueRequest, MetadataPlan
from app.repo_rules import extract_markdown_sections, load_rule_documents
from app.slot_inference import (
    METADATA_HEADING_SLOT_LEXICON,
    METADATA_LABEL_SLOT_LEXICON,
    contains_any_term,
    pick_best_slot,
    score_slot_values,
    strip_code_blocks,
)


DOC_LINE_PATTERNS = {
    "issue_labels": re.compile(r"(?:issue labels?|issue metadata labels?|이슈 라벨)\s*[:=-]\s*(.+)$", re.IGNORECASE),
    "pr_labels": re.compile(r"(?:pr labels?|pull request labels?|pr 라벨)\s*[:=-]\s*(.+)$", re.IGNORECASE),
    "labels": re.compile(r"(?<!issue )(?:labels?|라벨)\s*[:=-]\s*(.+)$", re.IGNORECASE),
    "assignees": re.compile(r"(?:assignees?|owners?|담당자)\s*[:=-]\s*(.+)$", re.IGNORECASE),
    "reviewers": re.compile(r"(?:reviewers?|리뷰어|검토자)\s*[:=-]\s*(.+)$", re.IGNORECASE),
    "team_reviewers": re.compile(r"(?:team reviewers?|review teams?|팀 리뷰어|리뷰 팀)\s*[:=-]\s*(.+)$", re.IGNORECASE),
    "milestone": re.compile(r"(?:milestone|마일스톤)\s*[:=-]\s*(.+)$", re.IGNORECASE),
}

HEADING_MATCHERS = {
    "issue_labels": ("issue labels", "issue label", "이슈 라벨"),
    "pr_labels": ("pr labels", "pull request labels", "pr 라벨"),
    "labels": ("labels", "라벨"),
    "assignees": ("assignees", "owners", "담당자", "오너"),
    "reviewers": ("reviewers", "리뷰어", "검토자"),
    "team_reviewers": ("team reviewers", "review teams", "팀 리뷰어", "리뷰 팀"),
    "milestone": ("milestone", "마일스톤"),
}


def infer_issue_metadata(workspace: Path, request: IssueRequest) -> MetadataPlan:
    """저장소 문서와 보수적인 fallback 규칙으로 이슈 메타데이터를 추론한다."""

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
    """명시 규칙과 CODEOWNERS, fallback 규칙을 합쳐 PR 메타데이터를 추론한다."""

    documents = load_rule_documents(workspace)
    explicit = infer_explicit_metadata(documents)
    codeowners_users, codeowners_teams = infer_codeowners_reviewers(workspace, changed_files)

    issue_labels = dedupe(explicit["issue_labels"] + infer_fallback_issue_labels(request, changed_files))
    pr_labels = dedupe(explicit["pr_labels"] + explicit["shared_labels"] + infer_fallback_pr_labels(request, changed_files))
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
    """규칙 문서에서 명시 메타데이터 줄과 섹션을 파싱한다."""

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
        sanitized = strip_markdown_code_blocks(text)
        for line in sanitized.splitlines():
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

        for section in extract_markdown_sections(sanitized):
            heading = section["heading"].strip().lower()
            parsed_values = parse_metadata_values(section["body"])
            if not parsed_values:
                continue

            for key, candidates in HEADING_MATCHERS.items():
                if any(candidate in heading for candidate in candidates):
                    apply_explicit_metadata_value(values, key, parsed_values)

            heading_decision = pick_best_slot(heading, METADATA_HEADING_SLOT_LEXICON, min_score=2)
            if heading_decision.value:
                apply_explicit_metadata_value(values, heading_decision.value, parsed_values)
    return values


def apply_explicit_metadata_value(values: dict[str, list[str] | str | None], key: str, parsed_values: list[str]) -> None:
    """파싱한 메타데이터 값을 누적 결과 객체에 반영한다."""

    if key == "milestone":
        values["milestone"] = parsed_values[0] if parsed_values else values["milestone"]
    elif key == "labels":
        values["shared_labels"] = dedupe(values["shared_labels"] + parsed_values)
    else:
        values[key] = dedupe(values[key] + parsed_values)


def strip_markdown_code_blocks(text: str) -> str:
    """예시 코드블록이 메타데이터로 오해되지 않도록 fenced block을 제거한다."""

    return strip_code_blocks(text)


def parse_metadata_values(raw_text: str) -> list[str]:
    """불릿, 쉼표 구분, 인라인 목록에서 메타데이터 값을 파싱한다."""

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
    """요청 문맥과 변경 파일 유형을 보고 이슈 라벨 후보를 추론한다."""

    text = " ".join(part for part in (request.issue_title, request.issue_body, request.comment_body) if part).lower()
    labels = [label for label, score in score_slot_values(text, METADATA_LABEL_SLOT_LEXICON).items() if score >= 2]
    if is_docs_only_change(changed_files):
        labels.append("documentation")
    if is_test_heavy_change(changed_files):
        labels.append("tests")
    if touches_automation(changed_files):
        labels.append("automation")
    if touches_dependencies(changed_files):
        labels.append("dependencies")
    if touches_frontend(changed_files):
        labels.append("frontend")
    if touches_backend(changed_files):
        labels.append("backend")
    if touches_infra(changed_files):
        labels.append("infra")
    if not labels and contains_any_term(text, METADATA_LABEL_SLOT_LEXICON["enhancement"]):
        labels.append("enhancement")
    return dedupe(labels)


def infer_fallback_pr_labels(request: IssueRequest, changed_files: list[str]) -> list[str]:
    """이슈 라벨 추론 결과에 PR용 보조 라벨을 더해 PR 라벨을 만든다."""

    labels = infer_fallback_issue_labels(request, changed_files)
    labels.append("automation")
    if changed_files and is_docs_only_change(changed_files):
        labels.append("documentation")
    return dedupe(labels)


def infer_fallback_milestone(request: IssueRequest) -> str | None:
    """자유 형식 본문에서 간단한 milestone 언급을 찾는다."""

    text = " ".join(part for part in (request.issue_title, request.issue_body, request.comment_body) if part)
    match = re.search(r"(?:milestone|마일스톤)\s*[:=-]\s*([^\n\r]+)", text, re.IGNORECASE)
    if not match:
        return None
    return match.group(1).strip().strip("`").strip()


def infer_codeowners_reviewers(workspace: Path, changed_files: list[str]) -> tuple[list[str], list[str]]:
    """CODEOWNERS 매칭 결과에서 사용자 리뷰어와 팀 리뷰어를 추론한다."""

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
    """자주 쓰는 위치에서 CODEOWNERS 항목을 읽어온다."""

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
    """정규화한 파일 경로가 CODEOWNERS 패턴과 맞는지 확인한다."""

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
    """모든 변경 파일이 문서 수정으로 보이는지 확인한다."""

    if not changed_files:
        return False
    return all(
        path.lower().startswith(("docs/", ".github/")) or path.lower().endswith((".md", ".txt", ".rst"))
        for path in changed_files
    )


def is_test_heavy_change(changed_files: list[str]) -> bool:
    """변경 대부분이 테스트 코드인지 확인한다."""

    return any("/tests" in f"/{path.lower()}" or path.lower().startswith("tests/") for path in changed_files)


def touches_automation(changed_files: list[str]) -> bool:
    """자동화나 CI 관련 파일을 건드렸는지 확인한다."""

    return any(path.lower().startswith(".github/") or "workflow" in path.lower() or "bot" in path.lower() for path in changed_files)


def touches_dependencies(changed_files: list[str]) -> bool:
    """의존성 선언 파일이나 lockfile을 건드렸는지 확인한다."""

    dependency_files = {
        "requirements.txt",
        "package.json",
        "package-lock.json",
        "pnpm-lock.yaml",
        "poetry.lock",
        "pyproject.toml",
    }
    return any(Path(path).name.lower() in dependency_files for path in changed_files)


def touches_frontend(changed_files: list[str]) -> bool:
    """프론트엔드 파일을 주로 건드렸는지 확인한다."""

    return any(
        path.lower().endswith((".html", ".css", ".scss", ".sass", ".jsx", ".tsx", ".vue"))
        or "/frontend/" in f"/{path.lower()}"
        or "/ui/" in f"/{path.lower()}"
        or path.lower().startswith(("frontend/", "ui/", "web/"))
        for path in changed_files
    )


def touches_backend(changed_files: list[str]) -> bool:
    """백엔드 파일을 주로 건드렸는지 확인한다."""

    return any(
        path.lower().endswith((".py", ".java", ".kt", ".go", ".rb", ".cs"))
        or "/backend/" in f"/{path.lower()}"
        or "/api/" in f"/{path.lower()}"
        or path.lower().startswith(("backend/", "api/", "app/"))
        for path in changed_files
    )


def touches_infra(changed_files: list[str]) -> bool:
    """인프라나 배포 관련 파일을 건드렸는지 확인한다."""

    infra_files = {"dockerfile", "compose.yml", "compose.yaml", "terraform.tfvars"}
    return any(
        path.lower().startswith((".github/", "infra/", "deploy/", "ops/"))
        or Path(path).name.lower() in infra_files
        or Path(path).suffix.lower() in {".tf", ".yml", ".yaml"}
        for path in changed_files
    )


def dedupe(values: list[str]) -> list[str]:
    """순서를 유지한 채 메타데이터 값을 중복 제거한다."""

    seen: list[str] = []
    for value in values:
        candidate = value.strip()
        if not candidate or candidate in seen:
            continue
        seen.append(candidate)
    return seen
