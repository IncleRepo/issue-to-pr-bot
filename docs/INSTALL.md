# Install Guide

이 문서는 “다른 사람이 최소 설정만 하고 이 봇 엔진을 재사용하는 방식” 기준이다.

대상 저장소 사용자가 직접 해야 하는 일은 아래가 전부다.

1. 네 GitHub App 설치
2. 자기 self-hosted runner 연결
3. 얇은 workflow 1개 추가
4. 필요하면 `.issue-to-pr-bot.yml`, `AGENTS.md` 추가

봇 엔진 코드는 대상 저장소에 복사하지 않는다.

## 1. 필수 준비

- GitHub App 설치 권한
- self-hosted runner 1대
- Docker
- Codex CLI 인증이 완료된 환경

## 2. GitHub App 설치

대상 저장소에 네 GitHub App을 설치한다.

필요 권한:

- `Contents`: Read and write
- `Issues`: Read and write
- `Pull requests`: Read and write
- `Metadata`: Read-only
- `Workflows`: Read and write

필요 event:

- `Issue comment`

대상 저장소에 등록할 값:

- Repository variable: `BOT_APP_ID`
- Repository secret: `BOT_APP_PRIVATE_KEY`

## 3. Runner 준비

사용자는 자기 PC나 서버에 self-hosted runner만 연결하면 된다.

권장 환경:

- Windows self-hosted runner
- Docker 설치
- Git 설치
- Codex 인증 완료

권장 경로:

- `CODEX_HOME_HOST`
- `BOT_CONTEXT_DIR_HOST`
- `BOT_SECRETS_FILE_HOST`

## 4. 대상 저장소에 추가할 파일

최소 필수:

- `.github/workflows/issue-comment.yml`

선택:

- `.issue-to-pr-bot.yml`
- `AGENTS.md`
- `CONTRIBUTING.md`
- `.github/pull_request_template.md`

복사할 템플릿:

- `templates/issue-comment.yml.example`
- `templates/.issue-to-pr-bot.yml.example`
- `templates/AGENTS.md.example`

## 5. 핵심 구조

대상 저장소 workflow는 직접 봇 코드를 실행하지 않는다.

대신 이 저장소의 reusable workflow를 호출한다.

즉:

- 대상 저장소: 얇은 진입점
- 엔진 저장소: 실제 봇 로직, Docker 이미지 빌드, 실행

## 6. 첫 검증

1. 대상 저장소에 workflow 추가
2. runner online 확인
3. 이슈 댓글에 `/bot status`
4. 필요 문서/secret 누락 확인
5. `/bot plan`
6. `/bot run`

## 7. 운영 권장

새 저장소에는 먼저 아래 순서로 붙인다.

1. `mode=test-pr`
2. `/bot status`
3. `/bot plan`
4. `mode=codex`

