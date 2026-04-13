# Contributing

## Development Flow

1. Start from an issue that describes the expected behavior.
2. Create a dedicated branch for the work.
3. Keep the implementation focused on the issue.
4. Run the configured checks.
5. Open a pull request for review.

## Local Setup

Use Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests
```

## Code Style

- Use Python standard library features where they are sufficient.
- Keep modules small and named by responsibility.
- Prefer explicit error messages over silent fallbacks.
- Avoid broad refactors when fixing a narrow issue.
- Add or update tests when behavior changes.

## Automation Conventions

The bot reads this document and applies these patterns automatically when they are present.

- Branch format: `bot/{issue_number}{comment_suffix}-{slug}`
- Commit format: `feat(issue-{issue_number}): {issue_title}`
- PR title format: `[bot] #{issue_number} {issue_title}`

## Safety

- Never commit `.venv/`, `.ruff_cache/`, `__pycache__/`, `.env`, private keys, or tokens.
- Treat `.github/workflows/**` as protected unless the issue explicitly requests workflow changes.
- If a task requires external credentials or private domain documents that are not available, stop and request the missing context.
