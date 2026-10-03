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
    governor   끔. `governor=Governor(...)` 를 주면 LLM 을 감싼다(CMD-K12): 분당 예산이 비었거나 429 면 handle 이 실패하지 않고
               **미룬 결과**(outcome "deferred", wait_s · step_id)를 낸다. 미룬 걸음은 차례대로 세워 두었다가 `tick()` · `close_windows()`
               가 창이 열리면 다시 보낸다(같은 step_id). 세운 걸음마다 health VERIFY 가 "창 안에 보냈다" 를 판정한다
닫는 쪽: guard · action 실행기 · health VERIFY 가운데 하나라도 불러오지 못하면 입구가 서지 않는다(shadow 에서도).
MS 런타임 자체는 그것들이 없으면 조용히 빼고 돈다(ms/runtime.py:90-96) -- SDK 에서는 그 갈래를 막는다(BD-120 (3)).
부작용: 처리기를 주지 않은 행동은 MS 의 effect 틀(모의)로 돈다. 이 모듈 자신은 파일 · 네트워크 · 환경을 건드리지 않는다.
"""
from __future__ import annotations

import copy
import dataclasses
import json
import math


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

    @property
    def deferred(self) -> bool:
        """분당 한도로 미뤘다(CMD-K12). 실패가 아니다 -- step_id 로 다시 보낸 결과를 tick() 이 낸다."""
        return "deferred" in self.raw

    @property
    def wait_s(self) -> "float | None":
        return self.raw["deferred"]["wait_s"] if self.deferred else None

    @property
    def step_id(self) -> "str | None":
        return self.raw["deferred"]["step_id"] if self.deferred else None


def governed_provider(llm, governor, model: "str | None" = None, estimate=None, sleep=None,
                      max_inline_wait_s: float = 90.0, max_inline_retries: int = 3):
    """LLM 을 지킴이로 감싼다(MS LLMProvider 꼴 그대로). 부른 뒤 사용량을 지킴이에 적는다.

    한 실행(handle)의 **첫 부름** 앞에서 예산이 없거나 429 면 RateDeferred · 원래 예외를 올린다 -- 실행이 아무것도 하지 않은 채
    끝나므로 Autonomy 가 그 걸음을 세워 두었다가 처음부터 다시 보낸다. **둘째 부름부터**(같은 실행 안의 다음 판)는 다시 보내면 앞의
    판을 되풀이하게 되므로, 창이 열릴 때까지 한 번 잔다(sleep, 바꿔 끼울 수 있다 -- 바쁘게 돌지 않는다). 대기가
    max_inline_wait_s 보다 길거나(하루 할당 등) 429 가 max_inline_retries 번을 넘으면 올린다. `deferred` 에 마지막 대기가 남는다."""
    import time as _time
    from ms.providers import CallableProvider, LLMProvider
    from .governor import Governor, RateDeferred
    inner = llm if isinstance(llm, LLMProvider) else CallableProvider(llm)
    est = estimate or (lambda req: len(req.prompt.text()) // 4)
    nap = sleep or _time.sleep

    class GovernedProvider(LLMProvider):
        name = inner.name
        supports_stream = inner.supports_stream
        simulated = getattr(inner, "simulated", False)

        def __init__(self):                                   # 키 · 전송은 안쪽 provider 의 것
            self.inner, self.model, self.api_key, self.transport, self.clock = inner, inner.model, "-", None, inner.clock
            self.deferred = None
            self.calls_in_run = 0                             # Autonomy 가 실행마다 0 으로
            self.inline_waits = []                            # 실행 안에서 잔 시간(초) -- 기록 · 시험용

        def capabilities(self):
            return inner.capabilities()

        def generate(self, req):
            return self._call(inner.generate, req)

        def collect(self, req):
            return self._call(inner.collect, req)

        def _inline(self, wait_s) -> bool:
            """실행 안(둘째 부름부터)이고 대기가 짧으면 한 번 자고 True."""
            if self.calls_in_run == 0 or not (0 < wait_s <= max_inline_wait_s):
                return False
            nap(wait_s)
            self.inline_waits.append(wait_s)
            return True

        def _call(self, fn, req):
            retries = 0
            while True:
                g = governor.try_acquire(est(req), model)
                if not g.ok:
                    if self._inline(g.wait_s):
                        continue
                    self.deferred = g.wait_s
                    raise RateDeferred(g.wait_s)
                try:
                    resp = fn(req)
                except Exception as e:
                    if Governor.is_rate_limit(e):
                        w = max(governor.on_rate_limit(e, model=model), governor.wait_s(model=model))
                        retries += 1
                        if retries <= max_inline_retries and self._inline(w):
                            continue
                        self.deferred = w
                    raise
                self.calls_in_run += 1
                governor.observe(g.ticket, getattr(resp, "usage", None), model)
                return resp

    return GovernedProvider()


class Autonomy:
    """일곱 패키지를 조립하는 입구 하나. MS Runtime 이 이미 조립자다 -- 이 층은 꽂는 자리를 한 곳에 모으고 DC 길을 기본으로 묶는다."""

    def __init__(self, world, *, actions, llm, purpose: str = "context_runtime", guard_mode: str = "shadow",
                 grants=(), l0=None, ledger: "str | None" = None, run_state=None, risky=None, governor=None,
                 governor_model: "str | None" = None, grace_s: float = 5.0, governor_sleep=None):
        from dc import DecisionContextBuilder, MSGraphSource, MSStateReader, MSUsageSource
        from ms import usage_model as U
        from ms.query import StateQuery, run_query
        from ms.runtime import Runtime
        from ms.tools import ToolRegistry

        registry = actions if isinstance(actions, ToolRegistry) else ToolRegistry(copy.deepcopy(list(actions)))
        self.governor, self.governor_model = governor, governor_model
        self._gp = governed_provider(llm, governor, governor_model, sleep=governor_sleep) if governor is not None else None
        llm = self._gp or llm
        kw = {"l0_sink": l0} if (l0 is not None and not isinstance(l0, str)) else ({"l0_ledger": l0} if l0 else {})
        self.runtime = Runtime(world, registry, {"llm": llm}, grants=grants, ledger_path=ledger, guard_mode=guard_mode,
                               run_state=run_state, risky=risky, **kw)
        missing = [name for name, part in (("guard", self.runtime.guard), ("action 실행기", self.runtime.dispatch),
                                           ("health VERIFY", self.runtime.verifier)) if part is None]
        if missing:
            raise ImportError(f"{' · '.join(missing)} 를 불러올 수 없다 -- 입구를 세우지 않는다(rlo-sdk 는 guard · health 가 필수다)")
        builder = DecisionContextBuilder([MSUsageSource(self.runtime.um, U.MODEL_VERSION),
                                          MSGraphSource(world, run_query, StateQuery.from_dict)])
        self.runtime.state_reader = MSStateReader(builder, purpose)
        self.session: "str | None" = None
        self.parked: list = []                       # 미룬 걸음(차례대로): {step_id, job}
        self._steps = 0
        self.ledger_path = ledger
        if governor is not None:
            from .scheduler import ParkVerify
            self._verify = ParkVerify("autonomy", grace_s)
            self._rec = None
            if l0 is not None:
                from telemetry.ledger import JsonlSink
                from telemetry.recorder import Recorder
                sink = JsonlSink(l0) if isinstance(l0, str) else l0
                self._rec = Recorder("autonomy-governor", sink, source="inproc:rlo.autonomy",
                                     wall=lambda: governor.clock() * 1000)

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
        job = {"session": s, "task": task, "queries": list(queries), **request}
        if self.governor is None:
            return Result(self.runtime.handle(job))
        self.tick()                                  # 앞서 세운 걸음이 먼저다(차례)
        if self.parked:
            return self._park(job, self.governor.wait_s(model=self.governor_model), "queued")
        return self._run(job)

    # ── 분당 한도(CMD-K12 S4) ──
    def _run(self, job: dict, step_id: "str | None" = None) -> Result:
        w = self.governor.wait_s(model=self.governor_model)
        if w > 0:
            return self._park(job, w, "budget", step_id)
        self._gp.deferred, self._gp.calls_in_run = None, 0
        res = Result(self.runtime.handle(dict(job)))
        if self._gp.deferred is not None and res.outcome == "llm_error":
            # 한도 때문에 이 실행의 LLM 호출이 일어나지 않았거나 429 였다 -- 실패가 아니라 미룸. 다시 보낼 때 처음부터 돈다
            wait = max(self._gp.deferred, self.governor.wait_s(model=self.governor_model))   # 서버 대기 · 창 가운데 늦은 쪽
            return self._park(job, wait, "rate_limit", step_id)
        return res

    def _row(self, row: dict) -> None:
        if self.ledger_path:
            with open(self.ledger_path, "a", encoding="utf-8") as f:
                f.write(json.dumps({**row, "at_ms": round(self.governor.clock() * 1000, 3)}, ensure_ascii=False,
                                   sort_keys=True) + "\n")

    def _park(self, job: dict, wait_s: float, why: str, step_id: "str | None" = None) -> Result:
        if step_id is None:
            self._steps += 1
            step_id = f"step-{self._steps}"
            self.parked.append({"step_id": step_id, "job": job})
        else:                                        # 다시 세운다 -- 차례의 맨 앞(먼저 세운 걸음이 먼저)
            self.parked.insert(0, {"step_id": step_id, "job": job})
        now = self.governor.clock()
        self._verify.park(step_id, now, wait_s)
        wait = None if not math.isfinite(wait_s) else round(wait_s, 3)
        self._row({"kind": "step_park", "step": step_id, "why": why, "wait_s": wait})
        if self._rec is not None:
            self._rec.emit("runtime.status", declared_status=f"parked_{why}")
        return Result({"deferred": {"step_id": step_id, "wait_s": wait_s, "why": why, "parked_at_ms": now * 1000},
                       "result": {"outcome": "deferred"}})

    def tick(self) -> list:
        """세운 걸음을 차례대로 다시 보낸다(창이 열렸으면). 창이 닫히도록 보내지 못한 걸음은 health VERIFY 가 NOT_VERIFIED.
        [{decision_ref, when: deferred_window_close | deferred_dispatch, record} | {step_id, when: redispatch, result}]."""
        if self.governor is None:
            return []
        now = self.governor.clock()
        out = [{"decision_ref": f"step:{sid}", "when": "deferred_window_close", "record": rec.to_dict()}
               for sid, rec in self._verify.close(now)]
        while self.parked and self.governor.wait_s(model=self.governor_model) <= 0:
            p = self.parked.pop(0)
            rec = self._verify.dispatched(p["step_id"], self.governor.clock())
            if rec is not None:
                out.append({"decision_ref": f"step:{p['step_id']}", "when": "deferred_dispatch", "record": rec.to_dict()})
            self._row({"kind": "step_dispatch", "step": p["step_id"]})
            res = self._run(p["job"], p["step_id"])
            out.append({"step_id": p["step_id"], "when": "redispatch", "result": res})
            if res.deferred:
                break
        return out

    def close_windows(self) -> list:
        """창이 닫힌 VERIFY 를 마감한다(handle 마다 처음에도 돈다). governor 가 있으면 세운 걸음도 다시 보낸다(tick)."""
        return self.runtime.close_windows() + self.tick()

    @staticmethod
    def versions() -> dict:
        from .versions import versions
        return versions()
