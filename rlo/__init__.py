"""rlo SDK -- 일곱 층을 한 입구로 묶는다(BD-119 · BD-120). 설계: action `9642bb3` docs/SDK.md.

공개(SDK.md §3):
    Autonomy · Result       입구(rlo.autonomy)
    versions()              고정 목록 · 동결 계약 판본(rlo.versions)
    rlo.hooks               에이전트 SDK 훅 어댑터 -- guard_hooks(model, mode=…) · python -m rlo.hooks
    Governor                분당 한도 지킴이(rlo.governor, CMD-K12) -- Autonomy(governor=) 또는 Scheduler 가 쓴다
    Scheduler · Step · load_step_kinds   걸음 차례(rlo.scheduler, CMD-K12) -- Autonomy 없이 제어기가 바로 부르는 얇은 입구
    rlo.ctxbudget           context-budget/1 -- 훅의 컨텍스트 예산(CMD-K17, guard_hooks(context_budget=)) · simulate
    rlo.pspec               prompt-spec/1, 우리 프롬프트 언어(CMD-K15, baseline PROMPT_SPEC.md) -- load · compile · check · tokens
그 밖(MS Runtime 속 · DC builder · Sensor 엔진 …)은 내부다. 일곱 저장소는 내부를 자유롭게 바꾼다.
"""
from .autonomy import Autonomy, Result
from .governor import Governor
from .scheduler import Scheduler, Step, load_step_kinds
from .versions import __version__, versions

__all__ = ["Autonomy", "Result", "Governor", "Scheduler", "Step", "load_step_kinds", "versions", "__version__"]
