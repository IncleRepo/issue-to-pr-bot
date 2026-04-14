"""Lightweight entrypoint for approved-review auto-merge handling."""

from app.auto_merge import handle_pull_request_review_event
from app.runtime.comments import configure_output_encoding
from app.runtime.orchestrator import load_event_payload


def main() -> None:
    configure_output_encoding()
    payload = load_event_payload()
    handle_pull_request_review_event(payload)


if __name__ == "__main__":
    main()
