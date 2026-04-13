# Install Guide

이 문서는 `issue-to-pr-bot`을 다른 저장소에 붙이는 최소 설치 절차를 정리한다.

## 1. 준비물

- GitHub App 1개
- self-hosted runner 1대
- Docker 사용 가능 환경
- Codex CLI 인증이 완료된 계정

## 2. 대상 저장소에 필요한 파일

아래 파일을 대상 저장소에 복사한다.

- `.github/workflows/issue-comment.yml`
- `.issue-to-pr-bot.yml`
- `AGENTS.md`
- 필요하면 `CONTRIBUTING.md`
- 필요하면 `.github/pull_request_template.md`

이 저장소의 예시는 아래 템플릿을 기준으로 시작하면 된다.

- `templates/.issue-to-pr-bot.yml.example`
- `templates/issue-comment.yml.example`
- `templates/AGENTS.md.example`

## 3. GitHub App 권한

Repository permissions:

- `Contents`: Read and write
- `Issues`: Read and write
- `Pull requests`: Read and write
- `Metadata`: Read-only
- `Workflows`: Read and write

Subscribe to events:

- `Issue comment`

설치 후 저장소에 App을 설치하고 다음 값을 등록한다.

- Repository variable: `BOT_APP_ID`
- Repository secret: `BOT_APP_PRIVATE_KEY`

## 4. Runner 준비

Windows self-hosted runner 기준 권장 준비:

- Docker 설치
- Git 설치
- runner 서비스 또는 상시 실행 세션 준비
- Codex 인증 파일 준비

권장 경로:

- Codex home: `C:\Users\<user>\.codex`
- External context root: `C:\bot-context\<repo-name>`
- Secret env file: `C:\bot-secrets\<repo-name>.env`

필요한 Repository variables:

- `CODEX_HOME_HOST`
- `BOT_CONTEXT_DIR_HOST`
- `BOT_SECRETS_FILE_HOST`

## 5. Workflow 배치

대상 저장소의 workflow는 다음 조건을 만족해야 한다.

- `issue_comment` 이벤트로 실행
- `OWNER`, `MEMBER`, `COLLABORATOR`만 허용
- self-hosted runner 사용
- Docker 이미지 빌드 후 컨테이너에서 `python -m app.main` 실행
- GitHub App token 사용

## 6. 첫 설정

최소 `.issue-to-pr-bot.yml` 예시:

```yaml
bot:
  command: "/bot run"
  plan_command: "/bot plan"
  help_command: "/bot help"
  status_command: "/bot status"
  mention: "@your-bot"
  provider: "codex"
  mode: "codex"
  check_commands:
    - "python -m compileall -q app tests"
    - "python -m unittest discover -s tests"
```

## 7. 첫 검증

1. 이슈 생성
2. 댓글에 `/bot status`
3. 누락된 context / secret 확인
4. 댓글에 `/bot plan`
5. 마지막으로 `/bot run`

## 8. 권장 순서

새 저장소에서는 아래 순서가 가장 안전하다.

1. `mode=test-pr`로 브랜치/PR 생성만 검증
2. `status`로 context / secrets 확인
3. `plan`으로 프롬프트 품질 확인
4. `mode=codex` 전환

