"""걸음 차례 정하기(CMD-K12 S2 · S3 · S5) -- LLM 없는 제어기가 걸음마다 모형이 필요한지 보고, 분당 한도가 찼으면 도구 걸음을 계속
돌리며 모형 걸음을 세워 두었다가(park) 창이 열리면 차례대로 다시 보낸다.

    kinds = load_step_kinds({"schema": "rlo-step-kinds/1", "steps": {"plan": "model", "read": "tool", "render": "tool"}})
    s = Scheduler([Step("s1", "read", fn=read), Step("s2", "plan", payload=prompt, after=("s1",)), ...],
                  Governor({"gemini-3.1-flash-lite": {"rpm": 15}}), provider, kinds=kinds)
    report = s.run()          # 실패 0 이 목표: 429 는 실패가 아니라 미룸이다

걸음 종류(S2): **닫힌 표** 하나, 걸음 이름 -> model | tool. 모형 공급자를 부르는 걸음만 model 이다. 표에 없는 이름은 싣는 순간
StepKindError(시작하지 않는다). 표는 rlo 의 것이다(action-model/1 밖, substitutes 와 같은 자리 -- 동결 계약은 그대로).

돌리는 법(S3):
- 도구 걸음은 앞 걸음(after)이 끝났고 not_before 가 지났으면 언제나 돈다 -- 세워 둔 모형 걸음 뒤에 막히지 않는다.
- 모형 걸음은 큐 차례대로 하나씩, 지킴이(Governor)가 허락할 때만 보낸다. 허락하지 않으면 세워 두고 도구 걸음을 계속 돈다.
  429 를 받으면 서버가 말한 대기(retryDelay)까지 세워 두고 같은 걸음을 다시 보낸다(attempt + 1).
- 바쁘게 기다리지 않는다: 할 수 있는 것이 없을 때만, 다음 도구 걸음이 준비되는 때와 창이 열리는 때 가운데 이른 쪽까지 잔다.
- 세움 · 보냄 · 429 마다 L0 사건(닫힌 목록 안: runtime.status · llm.request · llm.response · llm.error · tool.start/end)과
  원장 줄 하나. 걸음 id 는 원장에 있다(L0 사건 목록에 걸음 칸이 없다 -- 모형 걸음은 call_index 로 잇는다).
- VERIFY(health): 세운 걸음마다 "창(대기 + 여유) 안에 보냈다" 를 health `verify` 로 판정해 원장에 남긴다.

저장 · 상태(S6, BD-221):
- `state=` 경로를 주면 바뀔 때마다 JSON(`rlo-scheduler-state/1`: 걸음마다 상태 · 시도 수, 끝난 걸음의 결과(JSON 이 되는 것만),
  지킴이 창, 세운 걸음의 VERIFY 창)으로 저장하고, 그 파일이 있으면 시작할 때 읽어 이어 간다(프로세스를 다시 띄워도).
  다시 띄울 때는 같은 걸음(id · 이름)을 다시 싣는다 -- 함수는 저장되지 않는다. 결과가 JSON 이 아니어서 저장되지 않은 끝난 걸음은
  다시 돈다(원장 rerun_after_restart).
- `status()` = {resumes_in_s, done, running, parked, next} -- ga 의 감독(CMD-GA21)이 그대로 찍고 남긴다.
- `run(wait=False)`: 자야 할 때 자지 않고 저장한 뒤 돌아온다(감독이 기다렸다가 다시 부른다).
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import os
import time
from typing import Any, Callable

from .governor import Governor, usage_tokens
from .react import LABEL

STEP_KINDS_SCHEMA = "rlo-step-kinds/1"
KINDS = ("model", "tool")
DONE, FAILED, SKIPPED, PENDING, PARKED = "done", "failed", "skipped", "pending", "parked"
TERMINAL = (DONE, FAILED, SKIPPED)
STATE_SCHEMA = "rlo-scheduler-state/1"


class StepKindError(ValueError):
    pass


def load_step_kinds(src) -> dict:
    """{"schema": "rlo-step-kinds/1", "steps": {이름: "model" | "tool"}} (dict 또는 JSON 파일 경로) -> {이름: 종류}."""
    if isinstance(src, (str, os.PathLike)):
        with open(src, encoding="utf-8") as f:
            src = json.load(f)
    if not isinstance(src, dict) or src.get("schema") != STEP_KINDS_SCHEMA or set(src) - {"schema", "steps"} \
            or not isinstance(src.get("steps"), dict):
        raise StepKindError(f'걸음 표는 {{"schema": "{STEP_KINDS_SCHEMA}", "steps": {{이름: "model" | "tool"}}}} 이어야 한다')
    bad = [n for n, k in src["steps"].items() if not (isinstance(n, str) and LABEL.match(n)) or k not in KINDS]
    if bad:
        raise StepKindError(f"걸음 표: 이름은 라벨, 종류는 {KINDS} 가운데 하나 -- {sorted(map(str, bad))}")
    return dict(src["steps"])


@dataclasses.dataclass
class Step:
    id: str
    name: str                                  # 걸음 표의 열쇠
    fn: "Callable | None" = None               # tool: fn(results) -> 값
    payload: Any = None                        # model: provider(payload) -- callable 이면 payload(results)
    after: tuple = ()                          # 먼저 끝나야 할 걸음 id
    est_tokens: int = 0                        # model: 추정 토큰(입력 + 출력)
    model: "str | None" = None                 # model: 지킴이의 모형 열쇠(없으면 기본)
    not_before: "float | None" = None          # tool: 이 시각(초) 전에는 돌지 않는다


@dataclasses.dataclass
class Report:
    done: dict                                 # id -> 결과
    failed: dict                               # id -> 까닭(예외 종류 이름 · rate_limited)
    skipped: dict                              # id -> 앞 걸음 id
    parked: list                               # 끝났을 때도 세워진 걸음(예: 하루 할당 소진)
    sleeps: int
    slept_s: float
    provider_calls: int
    ledger: list
    events: list                               # L0 사건(메모리 sink 일 때)
    status: dict = dataclasses.field(default_factory=dict)     # 끝났을 때의 status()

    @property
    def ok(self) -> bool:
        return not self.failed and not self.skipped and not self.parked


class ParkVerify:
    """세운 걸음 VERIFY(health): 명령 = "이 걸음을 보낸다"(세운 때 발행), 사후조건 = dispatched == True, 창 = 대기 + 여유."""

    SPEC = "dispatch_parked_step@1"
    POST = ({"entity": "$target", "pred": ["dispatched", "==", True]},)

    def __init__(self, run: str, grace_s: float = 5.0):
        self.run, self.grace_s = run, grace_s
        self.pending: dict = {}                  # step id -> (ActionCommand, window_ms)
        self._n = 0

    def park(self, step_id: str, at_s: float, wait_s: float) -> None:
        if not math.isfinite(wait_s):            # 창이 없다(하루 할당) -- 지킬 수 없는 약속을 적지 않는다
            self.pending.pop(step_id, None)
            return
        from action.forms import ActionCommand
        self._n += 1
        iid = "int-" + hashlib.sha256(f"{self.run}/{step_id}/{self._n}".encode()).hexdigest()[:16]
        cmd = ActionCommand(intent_id=iid, decision_ref=f"step:{step_id}", action="dispatch_model_step",
                            target=f"step:{self.run}:{step_id}", args={}, issued_at=at_s * 1000, deadline=None)
        self.pending[step_id] = (cmd, (wait_s + self.grace_s) * 1000)

    def _verify(self, step_id, value: bool, at_s: float):
        from health.verification import verify
        cmd, window_ms = self.pending.pop(step_id)
        read = {"value": value, "observed_at": at_s * 1000, "status": "OBSERVED", "freshness": "FRESH",
                "time_base": "unix_ms"}
        return verify(cmd, run=self.run, subjects={}, spec=self.SPEC, postcondition=self.POST, window_ms=window_ms,
                      reads=lambda ent, st: read, evaluated_at=at_s * 1000)

    def dispatched(self, step_id: str, at_s: float):
        """보냈다 -- 창 안이면 VERIFIED. (창이 먼저 닫혔으면 close 가 이미 NOT_VERIFIED 를 냈다.)"""
        return self._verify(step_id, True, at_s) if step_id in self.pending else None

    def close(self, now_s: float) -> list:
        """창이 닫혔는데 보내지 않은 걸음 -> NOT_VERIFIED."""
        due = [sid for sid, (cmd, w) in self.pending.items() if now_s * 1000 >= cmd.issued_at + w]
        return [(sid, self._verify(sid, False, now_s)) for sid in due]

    def to_dict(self) -> dict:
        return {sid: [cmd.issued_at, w] for sid, (cmd, w) in self.pending.items()}

    def restore(self, d: dict) -> None:
        for sid, (issued_ms, window_ms) in d.items():
            self.park(sid, issued_ms / 1000, window_ms / 1000 - self.grace_s)


def _usage(resp):
    """응답에서 사용량(dict). dict 의 usage · usageMetadata, 또는 .usage 속성. 없으면 None."""
    u = resp.get("usage") or resp.get("usageMetadata") if isinstance(resp, dict) else getattr(resp, "usage", None)
    if u is not None and not isinstance(u, dict) and dataclasses.is_dataclass(u):
        u = dataclasses.asdict(u)
    return u if isinstance(u, dict) else None


class Scheduler:
    def __init__(self, steps, governor: Governor, provider: Callable, *, kinds, clock=None, sleep=None, l0=None,
                 ledger=None, run_id: str = "sched", provider_name: "str | None" = None, usage_format=None,
                 max_rate_limits: int = 10, grace_s: float = 5.0, state=None, on_event=None):
        """kinds: 걸음 표(load_step_kinds 의 꼴 또는 {이름: 종류}). l0: None(메모리) · 경로 · sink. ledger: 경로(JSONL) 또는 None.
        state: 저장 파일 경로(있으면 읽어 이어 간다). on_event(row): 원장 줄마다(감독이 status() 를 찍는 자리)."""
        table = kinds if isinstance(kinds, (str, os.PathLike)) or (isinstance(kinds, dict) and "schema" in kinds) \
            else {"schema": STEP_KINDS_SCHEMA, "steps": kinds}
        self.kinds = load_step_kinds(table)
        self.gov, self.provider = governor, provider
        self.clock = clock or governor.clock
        self.sleep = sleep or time.sleep
        self.provider_name = provider_name or governor.provider
        self.usage_format = usage_format or self.provider_name
        self.max_rate_limits = max_rate_limits
        self.run_id = run_id
        self.ledger_path, self.rows = ledger, []
        from telemetry.ledger import JsonlSink, MemorySink
        from telemetry.recorder import Recorder
        self._mem = MemorySink() if l0 is None else None
        sink = self._mem or (JsonlSink(l0) if isinstance(l0, (str, os.PathLike)) else l0)
        self.rec = Recorder(run_id, sink, source="inproc:rlo.scheduler", wall=lambda: self.clock() * 1000)
        self.verify = ParkVerify(run_id, grace_s)
        self.steps: dict = {}
        self.order: list = []
        self.state: dict = {}
        self.results, self.failed, self.skipped = {}, {}, {}
        self.attempts, self.rate_limits, self.call_index = {}, {}, {}
        self.sleeps, self.slept, self.calls = 0, 0.0, 0
        self.running = None
        self.state_path, self.on_event = state, on_event
        self._loading = True
        for s in steps:
            self.add(s)
        if state and os.path.exists(state):
            with open(state, encoding="utf-8") as f:
                self._load(json.load(f))
        self._loading = False

    # ── 걸음 싣기 ──
    def kind(self, step: Step) -> str:
        k = self.kinds.get(step.name)
        if k is None:
            raise StepKindError(f"걸음 {step.id}: 이름 {step.name!r} 가 걸음 표에 없다 -- 모형인지 도구인지 모른 채 돌리지 않는다")
        return k

    def add(self, step: Step) -> None:
        k = self.kind(step)
        if step.id in self.steps or not (isinstance(step.id, str) and LABEL.match(step.id)):
            raise StepKindError(f"걸음 id {step.id!r}: 겹치거나 라벨이 아니다")
        missing = [a for a in step.after if a not in self.steps]
        if missing:
            raise StepKindError(f"걸음 {step.id}: 앞 걸음 {missing} 이 아직 없다(앞 걸음을 먼저 싣는다)")
        if k == "tool" and not callable(step.fn):
            raise StepKindError(f"도구 걸음 {step.id}: fn 이 없다")
        self.steps[step.id] = step
        self.order.append(step.id)
        self.state[step.id] = PENDING

    # ── 기록 ──
    def _row(self, kind: str, step: Step, **kw) -> None:
        row = {"kind": kind, "step": step.id, "name": step.name, "at_ms": round(self.clock() * 1000, 3), **kw}
        self.rows.append(row)
        if self.ledger_path:
            with open(self.ledger_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False, sort_keys=True, default=str) + "\n")
        if self.state_path and not self._loading:
            self.save()
        if self.on_event is not None:
            self.on_event(row)

    # ── 저장 · 상태(S6) ──
    def save(self, path=None) -> None:
        """지금 상태를 JSON 으로(임시 파일에 쓰고 바꿔 끼운다 -- 쓰다 죽어도 앞 저장이 남는다)."""
        path = path or self.state_path
        results = {}
        for sid, v in self.results.items():
            try:
                json.dumps(v)
            except (TypeError, ValueError):
                continue
            results[sid] = v
        d = {"schema": STATE_SCHEMA, "run_id": self.run_id, "saved_at": self.clock(),
             "steps": {sid: {"name": self.steps[sid].name, "state": self.state[sid],
                             "attempts": self.attempts.get(sid, 0), "rate_limits": self.rate_limits.get(sid, 0),
                             "call_index": self.call_index.get(sid), "failed": self.failed.get(sid),
                             "skipped": self.skipped.get(sid)} for sid in self.order},
             "results": results, "governor": self.gov.to_dict(), "verify": self.verify.to_dict()}
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, sort_keys=True)
        os.replace(tmp, path)

    def _load(self, d: dict) -> None:
        if not isinstance(d, dict) or d.get("schema") != STATE_SCHEMA:
            raise StepKindError(f"저장 상태가 {STATE_SCHEMA} 가 아니다")
        for sid, st in d["steps"].items():
            if sid not in self.steps or self.steps[sid].name != st["name"]:
                raise StepKindError(f"저장 상태의 걸음 {sid}({st['name']}) 이 실은 걸음과 다르다 -- 같은 걸음을 다시 싣는다")
        self.gov.load(d["governor"])
        for sid, st in d["steps"].items():
            self.attempts[sid], self.rate_limits[sid] = st["attempts"], st["rate_limits"]
            if st["call_index"] is not None:
                self.call_index[sid] = st["call_index"]
            if st["state"] == DONE and sid not in d["results"]:
                self._row("rerun_after_restart", self.steps[sid])         # 결과를 저장하지 못했다 -- 다시 돈다
                continue
            self.state[sid] = st["state"]
            if st["state"] == DONE:
                self.results[sid] = d["results"][sid]
            elif st["state"] == FAILED:
                self.failed[sid] = st["failed"]
            elif st["state"] == SKIPPED:
                self.skipped[sid] = st["skipped"]
        self.verify.restore(d["verify"])
        self._loading = False
        self._row("resume", self.steps[self.order[0]], saved_at=d["saved_at"]) if self.order else None

    def status(self) -> dict:
        """{resumes_in_s, done, running, parked, next}. resumes_in_s: 세운 걸음이 있으면 창이 열릴 때까지(초), 기다려도 열리지 않으면
        (하루 할당) None, 세운 걸음이 없으면 None. next: 끝나지 않은 걸음(지금 도는 것 빼고)의 차례 [{id, kind}]."""
        parked = [sid for sid in self.order if self.state[sid] == PARKED]
        resumes = None
        m = self._next_model()
        if parked and m is not None:
            w = self.gov.wait_s(m.est_tokens, m.model)
            resumes = round(w, 3) if math.isfinite(w) else None
        return {"resumes_in_s": resumes, "done": [sid for sid in self.order if self.state[sid] == DONE],
                "running": self.running, "parked": parked,
                "next": [{"id": sid, "kind": self.kinds[self.steps[sid].name]} for sid in self.order
                         if self.state[sid] not in TERMINAL and sid != self.running]}

    # ── 돌리기 ──
    def _deps(self, s: Step) -> "str | None":
        """앞 걸음이 모두 끝났으면 'ok', 실패 · 건너뜀이 있으면 'dead', 아직이면 None."""
        st = [self.state[a] for a in s.after]
        if any(x in (FAILED, SKIPPED) for x in st):
            return "dead"
        return "ok" if all(x == DONE for x in st) else None

    def _run_tool(self, s: Step) -> None:
        self.running = s.id
        try:
            with self.rec.tool(s.name, {}) as t:
                v = s.fn(self.results)
                t.result(is_error=False)
        except Exception as e:
            self.running = None
            self.state[s.id], self.failed[s.id] = FAILED, type(e).__name__
            self._row("failed", s, exception=type(e).__name__)
            return
        self.running = None
        self.state[s.id], self.results[s.id] = DONE, v
        self._row("done", s, step_kind="tool")

    def _park(self, s: Step, wait_s: float, why: str) -> None:
        if self.state[s.id] == PARKED and why == "budget":
            return                               # 이미 세웠다 -- 다시 적지 않는다
        self.state[s.id] = PARKED
        self.verify.park(s.id, self.clock(), wait_s)
        self.rec.emit("runtime.status", declared_status=f"parked_{why}")
        self._row("park", s, wait_s=None if not math.isfinite(wait_s) else round(wait_s, 3), why=why)

    def _dispatch(self, s: Step, ticket) -> None:
        idx = self.call_index.setdefault(s.id, len(self.call_index))
        attempt = self.attempts.get(s.id, 0)
        was_parked = self.state[s.id] == PARKED
        self.rec.llm_request(idx, self.provider_name, s.model or self.gov.default_model, attempt=attempt)
        self._row("dispatch", s, attempt=attempt, call_index=idx)
        if was_parked:
            rec = self.verify.dispatched(s.id, self.clock())
            if rec is not None:
                self._row("verification", s, result=rec.result, reason=rec.reason, verification_id=rec.id)
        self.calls += 1
        payload = s.payload(self.results) if callable(s.payload) else s.payload
        self.running = s.id
        try:
            resp = self.provider(payload)
        except Exception as e:
            self.running = None
            status, body, headers = Governor.rate_limit_parts(e)
            self.rec.llm_error(idx, self.provider_name, http_status=status, body=body, headers=headers, attempt=attempt,
                               exception=type(e).__name__, table=self.gov.provider)
            if Governor.is_rate_limit(e):
                wait = self.gov.on_rate_limit(e, model=s.model)
                wait = max(wait, self.gov.wait_s(s.est_tokens, s.model))   # 서버 대기와 창 가운데 늦은 쪽이 약속이다
                self.attempts[s.id] = attempt + 1
                self.rate_limits[s.id] = self.rate_limits.get(s.id, 0) + 1
                self._row("rate_limit", s, attempt=attempt, wait_s=None if not math.isfinite(wait) else round(wait, 3))
                if self.rate_limits[s.id] > self.max_rate_limits:
                    self.state[s.id], self.failed[s.id] = FAILED, "rate_limited"
                    self._row("failed", s, reason="rate_limited")
                else:
                    self._park(s, wait, "rate_limit")
                return
            self.state[s.id], self.failed[s.id] = FAILED, type(e).__name__
            self._row("failed", s, exception=type(e).__name__)
            return
        self.running = None
        u = _usage(resp)
        self.gov.observe(ticket, u, model=s.model)
        self.rec.llm_response(idx, self.provider_name, usage=u, usage_format=self.usage_format)
        self.state[s.id], self.results[s.id] = DONE, resp
        self._row("done", s, step_kind="model", tokens=usage_tokens(u))

    def _next_model(self) -> "Step | None":
        """큐 차례로 첫 번째 끝나지 않은 모형 걸음(앞 걸음이 아직이면 None -- 뒤 모형 걸음이 앞지르지 않는다)."""
        for sid in self.order:
            s = self.steps[sid]
            if self.kinds[s.name] == "model" and self.state[sid] not in TERMINAL:
                return s if self._deps(s) == "ok" else None
        return None

    def run(self, wait: bool = True) -> Report:
        """wait=False: 자야 할 때 자지 않고(저장한 뒤) 돌아온다 -- report.status.resumes_in_s 뒤에 다시 부른다."""
        while True:
            now = self.clock()
            for sid, rec in self.verify.close(now):
                self._row("verification", self.steps[sid], result=rec.result, reason=rec.reason,
                          verification_id=rec.id)
            progressed = False
            for sid in list(self.order):         # 앞 걸음이 죽은 걸음은 건너뛴다
                s = self.steps[sid]
                if self.state[sid] not in TERMINAL and self._deps(s) == "dead":
                    self.state[sid], self.skipped[sid] = SKIPPED, next(a for a in s.after
                                                                      if self.state[a] in (FAILED, SKIPPED))
                    self._row("skipped", s, after=self.skipped[sid])
                    progressed = True
            for sid in list(self.order):         # 도구 걸음: 준비됐으면 언제나
                s = self.steps[sid]
                if self.kinds[s.name] == "tool" and self.state[sid] == PENDING and self._deps(s) == "ok" \
                        and (s.not_before is None or s.not_before <= self.clock()):
                    self._run_tool(s)
                    progressed = True
            m = self._next_model()
            if m is not None:
                g = self.gov.try_acquire(m.est_tokens, m.model)
                if g.ok:
                    self._dispatch(m, g.ticket)
                    progressed = True
                else:
                    self._park(m, g.wait_s, "budget")
            if all(st in TERMINAL for st in self.state.values()):
                break
            if progressed:
                continue
            now = self.clock()
            wake = [s.not_before for s in self.steps.values() if self.kinds[s.name] == "tool"
                    and self.state[s.id] == PENDING and self._deps(s) == "ok" and s.not_before is not None]
            m = self._next_model()
            if m is not None:
                w = self.gov.wait_s(m.est_tokens, m.model)
                if math.isfinite(w):
                    wake.append(now + w)
            wake = [t for t in wake if t > now]
            if not wake:
                break                            # 기다려도 바뀌지 않는다(하루 할당 · 풀 수 없는 앞 걸음) -- 세운 채로 끝낸다
            if not wait:
                if self.state_path:
                    self.save()
                break
            d = min(wake) - now
            self.sleep(d)
            self.sleeps, self.slept = self.sleeps + 1, self.slept + d
        parked = [sid for sid in self.order if self.state[sid] not in TERMINAL]
        return Report(dict(self.results), dict(self.failed), dict(self.skipped), parked, self.sleeps,
                      round(self.slept, 6), self.calls, list(self.rows), list(self._mem.events) if self._mem else [],
                      self.status())
