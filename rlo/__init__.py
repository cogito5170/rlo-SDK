"""rlo SDK -- 일곱 층을 한 입구로 묶는다(BD-119 · BD-120). 설계: action `9642bb3` docs/SDK.md.

공개(SDK.md §3):
    Autonomy · Result       입구(rlo.autonomy)
    versions()              고정 목록 · 동결 계약 판본(rlo.versions)
    rlo.hooks               에이전트 SDK 훅 어댑터 -- guard_hooks(model, mode=…) · python -m rlo.hooks
그 밖(MS Runtime 속 · DC builder · Sensor 엔진 …)은 내부다. 일곱 저장소는 내부를 자유롭게 바꾼다.
"""
from .autonomy import Autonomy, Result
from .versions import __version__, versions

__all__ = ["Autonomy", "Result", "versions", "__version__"]
