"""입구 `Autonomy` -- MS `Runtime` 을 감싸는 얇은 층(BD-120 (1)). 시제품 action `9642bb3` `sdk_draft/autonomy.py` 를 옮겼다.

    a = Autonomy.from_spec(spec, observations, actions=[...], llm=provider)    # 세계 · 행동 · LLM 은 반드시 받는다
    a.open_session("s", {"token_budget": 1000})
    out = a.handle("srv07 을 throttle", queries=spec["queries"])               # 결정 → Guard → 실행기 → VERIFY

기본으로 채우는 것:
    DC 길      MSStateReader(목적 context_runtime). 의도 · Guard · 실행기 · VERIFY 는 DC 길에서만 돈다(ms/intent.py:48-52 ·
               runtime.py:152). **SDK 는 snapshot 길을 내지 않는다**(BD-46 · BD-114 (2) · BD-120 (1)) -- state_reader 를 끄는
               자리가 없고, 누가 끄면 handle 이 거절한다
    guard_mode "shadow"(BD-118). "enforce" 는 설정으로 켠다
    L0         끔. `l0=` 경로나 sink 를 주면 켠다
    원장       끔. `ledger=` 경로를 주면 켠다
닫는 쪽: guard · action 실행기 · health VERIFY 가운데 하나라도 불러오지 못하면 입구가 서지 않는다(shadow 에서도).
MS 런타임 자체는 그것들이 없으면 조용히 빼고 돈다(ms/runtime.py:90-96) -- SDK 에서는 그 갈래를 막는다(BD-120 (3)).
부작용: 처리기를 주지 않은 행동은 MS 의 effect 틀(모의)로 돈다. 이 모듈 자신은 파일 · 네트워크 · 환경을 건드리지 않는다.
"""
from __future__ import annotations

import copy
import dataclasses


@dataclasses.dataclass
class Result:
    """handle 의 결과. MS Runtime.handle 의 dict 를 그대로 싣고(`raw`), 자주 보는 것만 이름으로 꺼낸다."""
    raw: dict

    @property
    def outcome(self) -> str:
        return self.raw["result"]["outcome"]

    @property
    def decision_id(self) -> str:
        return self.raw["decision"]["id"]

    @property
    def executions(self) -> list:
        return self.raw.get("executions", [])

    @property
    def guards(self) -> list:
        return self.raw.get("guards", [])

    @property
    def verifications(self) -> list:
        return self.raw.get("verifications", [])


class Autonomy:
    """일곱 패키지를 조립하는 입구 하나. MS Runtime 이 이미 조립자다 -- 이 층은 꽂는 자리를 한 곳에 모으고 DC 길을 기본으로 묶는다."""

    def __init__(self, world, *, actions, llm, purpose: str = "context_runtime", guard_mode: str = "shadow",
                 grants=(), l0=None, ledger: "str | None" = None, run_state=None):
        from dc import DecisionContextBuilder, MSGraphSource, MSStateReader, MSUsageSource
        from ms import usage_model as U
        from ms.query import StateQuery, run_query
        from ms.runtime import Runtime
        from ms.tools import ToolRegistry

        registry = actions if isinstance(actions, ToolRegistry) else ToolRegistry(copy.deepcopy(list(actions)))
        kw = {"l0_sink": l0} if (l0 is not None and not isinstance(l0, str)) else ({"l0_ledger": l0} if l0 else {})
        self.runtime = Runtime(world, registry, {"llm": llm}, grants=grants, ledger_path=ledger, guard_mode=guard_mode,
                               run_state=run_state, **kw)
        missing = [name for name, part in (("guard", self.runtime.guard), ("action 실행기", self.runtime.dispatch),
                                           ("health VERIFY", self.runtime.verifier)) if part is None]
        if missing:
            raise ImportError(f"{' · '.join(missing)} 를 불러올 수 없다 -- 입구를 세우지 않는다(rlo-sdk 는 guard · health 가 필수다)")
        builder = DecisionContextBuilder([MSUsageSource(self.runtime.um, U.MODEL_VERSION),
                                          MSGraphSource(world, run_query, StateQuery.from_dict)])
        self.runtime.state_reader = MSStateReader(builder, purpose)
        self.session: "str | None" = None

    @classmethod
    def from_spec(cls, spec: dict, observations=(), *, clock=None, actions=None, **kw) -> "Autonomy":
        """MS 세계 명세(dict) + 관측(dict 목록)에서. actions 를 안 주면 명세의 `tools` 를 쓴다(handler 없음 = effect 틀)."""
        from ms.cli import clock_for
        from ms.manager import StateManager
        world = StateManager.from_spec(spec, clock=clock or clock_for(spec))
        for o in observations:
            world.ingest(o)
        return cls(world, actions=spec["tools"] if actions is None else actions, **kw)

    def open_session(self, name: str, budgets: dict) -> str:
        self.session = name
        return self.runtime.open_session(name, budgets)

    def handle(self, task: str, queries=(), session: "str | None" = None, **request) -> Result:
        s = session or self.session
        if s is None:
            raise ValueError("세션이 없다 -- open_session 먼저")
        if self.runtime.state_reader is None:
            raise RuntimeError("결정 문맥(state_reader)이 없다 -- SDK 는 snapshot 길을 내지 않는다(BD-120 (1))")
        return Result(self.runtime.handle({"session": s, "task": task, "queries": list(queries), **request}))

    def close_windows(self) -> list:
        """창이 닫힌 VERIFY 를 마감한다(handle 마다 처음에도 돈다)."""
        return self.runtime.close_windows()

    @staticmethod
    def versions() -> dict:
        from .versions import versions
        return versions()
