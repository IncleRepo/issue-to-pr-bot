# 🤖 issue-to-pr-bot

GitHub 이슈를 기반으로 코드 작성 및 Pull Request를 자동 생성하는 LLM 기반 개발 자동화 봇

## 🚀 프로젝트 개요

이 프로젝트는 GitHub 이슈 또는 댓글을 입력으로 받아
LLM(Codex)을 활용해 코드를 생성하고, 자동으로 PR까지 생성하는 개인용 개발 자동화 도구입니다.

주요 목적은 반복적인 개발 작업을 줄이고,
AI 기반 개발 워크플로우를 실험하는 데 있습니다.

## 🧠 동작 흐름

이슈 / 댓글
      ↓
LLM (Codex CLI)
      ↓
코드 생성 및 수정
      ↓
Git 커밋 & 푸시
      ↓
Pull Request 생성

## ⚙️ 기술 스택

- Python
- GitHub Actions
- Self-hosted Runner
- Docker sandbox
- Codex CLI (ChatGPT Plus)
- GitHub REST API

## 🎯 주요 기능 (MVP)

- [ ] 이슈 댓글로 봇 실행 (`/bot run`)
- [ ] 이슈 내용 및 요구사항 파싱
- [ ] Codex CLI를 통한 코드 생성
- [ ] 브랜치 생성 및 커밋
- [ ] Pull Request 자동 생성

## 🔒 안전 장치

- 모든 작업은 별도의 브랜치에서 수행
- main 브랜치 직접 수정 금지
- PR 생성 후 수동 리뷰 필수
- 최소 권한 기반 실행
- 봇 실행은 Docker 컨테이너 내부에서 격리

## 🛠️ 저장소별 설정

`.issue-to-pr-bot.yml`에서 저장소별 실행 옵션을 조정합니다.

```yaml
bot:
  command: "/bot run"
  plan_command: "/bot plan"
  mention: "@incle-issue-to-pr-bot"
  branch_prefix: "bot"
  output_dir: "bot-output"
  test_command: "python -m unittest discover -s tests"
  check_commands:
    - "python -m compileall -q app tests"
    - "python -m unittest discover -s tests"
  mode: "codex"
```

## 📌 향후 확장 계획

- PR 리뷰 코멘트 자동 반영
- 멀티 LLM 구조 (Claude + Codex)
- 테스트 자동 실행 및 검증
- 변경 코드(diff) 분석 기능
- 조건부 자동 merge 기능
