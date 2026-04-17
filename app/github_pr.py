"""기존 공개 모듈 경로를 새 서비스 모듈에 연결하는 호환 진입점."""

from __future__ import annotations

import sys

from app.github_ops import service as _service

sys.modules[__name__] = _service

