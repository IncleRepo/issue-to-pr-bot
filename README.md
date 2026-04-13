# issue-to-pr-bot

GitHub 이슈 댓글을 트리거로 받아, LLM이 코드를 수정하고 검증한 뒤 브랜치와 PR까지 생성하는 self-hosted automation bot이다.

## 목적

- 이슈 기반 개발 자동화
- 팀 문서 기반 규칙 자동 적용
- self-hosted runner 환경에서 외부 문서와 secret을 함께 사용
- 사람이 최종 리뷰하는 PR 중심 워크플로우 유지

## 현재 지원 범위

- 이슈 댓글 트리거
  - `/bot run`
  - `/bot plan`
  - `/bot help`
  - `/bot status`
  - `@bot ...`
- Codex provider 실행
- 전용 브랜치 생성
- 검증 명령 실행
- PR 생성
- 성공/실패 댓글 작성
- 문서 기반 규칙 자동 추론
- 외부 context / secret env 주입
- 첨부 링크 수집과 텍스트 컨텍스트화

## 동작 구조

1. GitHub 이슈 댓글이 workflow를 트리거한다.
2. self-hosted runner가 Docker 컨테이너를 띄운다.
3. 컨테이너 안에서 봇이 이슈, 댓글, 저장소 문서, 외부 문서, secret env를 읽는다.
4. LLM provider가 코드를 수정한다.
5. 검증 명령을 실행한다.
6. 브랜치를 push하고 PR을 생성한다.
7. 결과를 이슈 댓글로 남긴다.

## 빠른 시작

### 로컬 실행

Windows PowerShell 기준:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m compileall -q app tests
.\.venv\Scripts\python.exe -m unittest discover -s tests
.\.venv\Scripts\python.exe -m app.main
```

### 기본 검증

```powershell
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m compileall -q app tests
.\.venv\Scripts\python.exe -m unittest discover -s tests
```

## 다른 저장소에 붙이는 방법

상세 절차는 아래 문서를 본다.

- `docs/INSTALL.md`
- `docs/OPERATIONS.md`

바로 시작하려면 아래 템플릿을 복사해서 대상 저장소에 맞게 수정한다.

- `templates/.issue-to-pr-bot.yml.example`
- `templates/issue-comment.yml.example`
- `templates/AGENTS.md.example`

## 대상 저장소에 필요한 것

### GitHub App

Repository permissions:

- `Contents`: Read and write
- `Issues`: Read and write
- `Pull requests`: Read and write
- `Metadata`: Read-only
- `Workflows`: Read and write

Event subscriptions:

- `Issue comment`

등록 값:

- Repository variable: `BOT_APP_ID`
- Repository secret: `BOT_APP_PRIVATE_KEY`

### Runner

권장 환경:

- Windows self-hosted runner
- Docker 설치
- Git 설치
- Codex CLI 인증 완료

권장 변수:

- `CODEX_HOME_HOST`
- `BOT_CONTEXT_DIR_HOST`
- `BOT_SECRETS_FILE_HOST`

## 설정 파일

기본 예시:

```yaml
bot:
  command: "/bot run"
  plan_command: "/bot plan"
  help_command: "/bot help"
  status_command: "/bot status"
  mention: "@incle-issue-to-pr-bot"
  provider: "codex"
  mode: "codex"
  check_commands:
    - "python -m compileall -q app tests"
    - "python -m unittest discover -s tests"
```

주요 설정:

- `provider`
- `mode`
- `check_commands`
- `branch_name_template`
- `pr_title_template`
- `codex_commit_message_template`
- `context_paths`
- `external_context_paths`
- `required_context_paths`
- `secret_env_keys`
- `required_secret_env`
- `protected_paths`

## 문서 기반 자동 적용

봇은 먼저 아래 문서를 읽는다.

- `AGENTS.md`
- `CONTRIBUTING.md`
- `README.md`
- `.github/pull_request_template.md`
- `.github/ISSUE_TEMPLATE`
- `.editorconfig`
- `pyproject.toml`
- `package.json`

문서에서 자동 추론하는 항목:

- 브랜치명 규칙
- 커밋 메시지 규칙
- PR 제목 규칙
- 검증 명령
- protected paths
- required context
- required secrets

문서에 없을 때만 `.issue-to-pr-bot.yml` 값이 fallback으로 사용된다.

## 댓글 명령

- `/bot run`
- `/bot plan`
- `/bot help`
- `/bot status`
- `@incle-issue-to-pr-bot ...`

지원 옵션:

- `mode=codex|test-pr`
- `provider=codex`
- `verify=true|false`
- `effort=low|medium|high|xhigh`

예시:

```text
/bot run effort=high README에 로컬 실행 방법 추가
@incle-issue-to-pr-bot verify=false mode=test-pr 브랜치와 PR만 생성해줘
/bot plan DB 마이그레이션 작업 계획
```

## 첨부 링크 처리

이슈 본문과 댓글의 링크를 수집한다.

- 텍스트 파일: 내용 일부와 요약을 프롬프트에 포함
- HTML 링크: 본문 텍스트 추출 후 컨텍스트로 사용
- 이미지 / PDF: 파일 경로와 종류를 전달
- 실패한 링크: 스킵 이유까지 기록

## 보안 원칙

- main 직접 수정 금지
- protected path 수정 차단
- secret 값은 프롬프트에 넣지 않음
- secret key 이름만 노출
- 외부 context와 secret env는 read-only mount
- GitHub App token으로 PR 생성

## Provider 구조

현재 지원 provider:

- `codex`

구조는 이미 provider registry 기반으로 분리되어 있다.

- `app/llm_provider.py`
- `app/codex_provider.py`

다음 provider를 추가할 때는 새 provider 실행기와 registry 등록만 넣으면 된다.

## 운영 순서

1. `/bot status`
2. `/bot plan`
3. `/bot run`
4. PR 리뷰

## 저장소 문서

- `docs/INSTALL.md`: 다른 저장소 설치 절차
- `docs/OPERATIONS.md`: 운영 체크리스트와 실패 유형
- `templates/`: 복사용 템플릿

