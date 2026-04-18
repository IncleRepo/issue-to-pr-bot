"""기존 공개 모듈 경로를 새 서비스 모듈에 연결하는 호환 진입점."""

from __future__ import annotations

import sys

from app.manager import service as _service

sys.modules[__name__] = _service


if __name__ == "__main__":
    raise SystemExit(_service.main())
