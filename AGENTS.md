# Repository Agent Guide

This repository is maintained with an issue-driven automation workflow. Agents must follow these rules before changing code.

## Working Rules

- Keep changes focused on the GitHub issue that triggered the run.
- Prefer small, reviewable patches over broad rewrites.
- Preserve existing project structure and naming conventions unless the issue explicitly asks for a refactor.
- Read `.issue-to-pr-bot.yml`, `CONTRIBUTING.md`, `README.md`, and GitHub templates before implementing.
- Do not commit secrets, tokens, private keys, local virtual environments, caches, or generated runner state.
- Do not modify `.github/workflows/**` unless the issue explicitly asks for workflow changes and the bot configuration allows it.
- If required context is missing, stop and explain what is needed instead of guessing.

## Verification

Run the configured verification command before opening a PR:

```powershell
python -m unittest discover -s tests
```

If verification fails, do not open a PR. Report the failing command and the relevant output in the issue comment.

## Pull Request Expectations

- The PR must describe what changed and how it was verified.
- The PR should reference the issue number.
- The changed files should match the requested scope.
- If no code change is needed, leave an issue comment explaining why.
