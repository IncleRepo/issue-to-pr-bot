# Operations Guide

## 운영 체크리스트

- runner가 online 상태인지 확인
- Docker 이미지 빌드가 되는지 확인
- `CODEX_HOME_HOST` 경로에 `auth.json`, `config.toml` 존재 확인
- `BOT_CONTEXT_DIR_HOST` 경로 존재 확인
- `BOT_SECRETS_FILE_HOST` 파일 존재 확인
- GitHub App 설치와 권한 확인

## 자주 쓰는 댓글 명령

- `/bot help`
- `/bot status`
- `/bot plan`
- `/bot run`
- `@bot README 수정해줘 effort=high`

## 실패 유형

### options

- 잘못된 `mode`, `provider`, `verify`, `effort`

### context

- 필수 문서 누락
- external context mount 누락

### secret

- 필수 env key 누락
- secret env file mount 누락

### verification

- 테스트, 린트, 빌드 실패

### git / github

- push 권한 부족
- App 권한 부족
- workflow 권한 부족

## 운영 원칙

- protected path는 문서와 config 둘 다에서 관리한다.
- required context / required secret은 문서에 명시한다.
- PR 템플릿과 브랜치 규칙은 문서로 고정한다.
- runner에는 필요한 문서와 secret만 올린다.

## 새 저장소 온보딩 순서

1. 템플릿 파일 복사
2. GitHub App 연결
3. runner 연결
4. `/bot status`
5. `/bot plan`
6. `/bot run`

