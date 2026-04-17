# 기여 가이드

## 작업 흐름

1. 먼저 기대 동작이 정리된 이슈를 기준으로 작업합니다.
2. 이슈마다 전용 브랜치를 사용합니다.
3. 수정 범위는 요청된 내용에 맞게 좁게 유지합니다.
4. 설정된 검증 명령을 모두 실행합니다.
5. 검증이 끝나면 PR을 올려 리뷰를 받습니다.

## 로컬 준비

Windows PowerShell 기준:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests
```

## 코드 작성 원칙

- 표준 라이브러리로 충분하면 외부 의존보다 표준 라이브러리를 우선합니다.
- 모듈은 책임이 드러나게 작게 유지합니다.
- 침묵하는 우회보다 원인이 드러나는 오류 메시지를 선호합니다.
- 좁은 수정 요청에 넓은 리팩토링을 섞지 않습니다.
- 동작이 바뀌면 테스트도 같이 보강합니다.

## 자동화 규칙

봇은 이 문서에 적힌 규칙을 읽고 기본 형식을 맞춥니다.

- 브랜치 형식: `bot/{issue_number}{comment_suffix}-{slug}`
- 커밋 형식: `{commit_type}(issue-{issue_number}): {issue_title}`
- PR 제목 형식: `[bot] #{issue_number} {issue_title}`

## 보호 경로

아래 경로는 기본적으로 보호 대상으로 봅니다.

- `.github/workflows/**`
- `.env`
- `.env.*`
- `*.pem`
- `*.key`
- `*.p12`
- `*.pfx`

## 안전 수칙

- `.venv/`, `.ruff_cache/`, `__pycache__/`, `.env`, 개인 키, 토큰은 커밋하지 않습니다.
- `.github/workflows/**`는 이슈에서 명시적으로 요구하지 않는 한 수정하지 않습니다.
- 외부 자격 증명이나 비공개 문서가 필요한데 제공되지 않았다면 추측하지 말고 중단합니다.
