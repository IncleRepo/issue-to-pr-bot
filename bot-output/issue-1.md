# Issue #1

- Repository: IncleRepo/issue-to-pr-bot
- Title: 테스트 이슈
- Comment author: IncleRepo
- Comment id: 4221377703
- Branch: bot/issue-1-comment-4221377703-issue

## Issue Body

테스트이슈

## Trigger Comment

/bot run

## Generated Task Prompt

```text
You are working in the IncleRepo/issue-to-pr-bot repository.
Implement the requested change from this GitHub issue.

Repository: IncleRepo/issue-to-pr-bot
Issue: #1
Title: 테스트 이슈
Author: IncleRepo
Created at: 2026-04-10T06:11:12+00:00

Issue body:
테스트이슈

Trigger comment:
/bot run

Rules:
- Create changes on a dedicated branch only.
- Do not push directly to main.
- Keep the change focused on the issue request.
- Run this verification command before opening a PR: python -m unittest discover -s tests
```
