"""rlo SDK -- 일곱 층을 한 입구로 묶는다(BD-119 · BD-120). 설계: action `9642bb3` docs/SDK.md.

공개(SDK.md §3):
    Autonomy · Result       입구(rlo.autonomy)
    versions()              고정 목록 · 동결 계약 판본(rlo.versions)
    rlo.hooks               에이전트 SDK 훅 어댑터 -- guard_hooks(model, mode=…) · python -m rlo.hooks
    Governor                분당 한도 지킴이(rlo.governor, CMD-K12) -- Autonomy(governor=) 또는 Scheduler 가 쓴다
    Scheduler · Step · load_step_kinds   걸음 차례(rlo.scheduler, CMD-K12) -- Autonomy 없이 제어기가 바로 부르는 얇은 입구
    PromptSpec · compile_prompt          prompt-spec/1 · 결정론적 컴파일러 · 같은 명세의 답 검사기(rlo.prompt_spec, CMD-K15)
    optimize · Metric                    프롬프트 변형 탐색(rlo.optimize, CMD-K15) -- 고르기만 하고 쓰지 않는다
그 밖(MS Runtime 속 · DC builder · Sensor 엔진 …)은 내부다. 일곱 저장소는 내부를 자유롭게 바꾼다.
"""
from .autonomy import Autonomy, Result
from .governor import Governor
from .optimize import Metric, optimize
from .prompt_spec import PromptSpec, compile_prompt
from .scheduler import Scheduler, Step, load_step_kinds
from .versions import __version__, versions

__all__ = ["Autonomy", "Result", "Governor", "Scheduler", "Step", "load_step_kinds", "PromptSpec", "compile_prompt",
           "optimize", "Metric", "versions", "__version__"]
