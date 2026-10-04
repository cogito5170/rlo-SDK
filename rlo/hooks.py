"""에이전트 SDK 훅 어댑터 -- 남의 에이전트(Claude Code · Agent SDK)의 도구 호출을 Guard 로 판정한다(BD-120 (5) · BD-122).
처음 꼴은 action `9642bb3` `sdk_draft/hooks.py`(설계 docs/SDK.md §5). 공식 문서로 확인한 입출력 꼴만 쓴다.

    PreToolUse          입력 {session_id, transcript_path, cwd, permission_mode, hook_event_name, tool_name, tool_input,
                              tool_use_id}                      (code.claude.com/docs/en/hooks.md "PreToolUse input")
                        막기 {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                              "permissionDecisionReason": …}}  ("PreToolUse decision control")
    PostToolUse(Failure) 입력 … + tool_response | error · is_interrupt · duration_ms
    Stop · SessionEnd   공통 칸(transcript_path …). Stop 은 matcher 를 받지 않는다. SessionEnd 는 막지 못하고 출력이 버려진다
    Agent SDK(Python)   콜백 async (input_data, tool_use_id, context) -> dict. `{}` = 바꾸지 않고 허락.
                        Python SDK 의 HookEvent 에는 SessionEnd 가 없다 -- 설정 파일의 명령 훅으로만 건다 (agent-sdk/hooks)

판정 길 (`TranscriptJudge`, BD-122):
    PreToolUse -> transcript 를 **다시 거둔다**(Telemetry `from_cc_jsonl`) -> **지금 호출을 뺀다**(BD-124) -> Sensor `from_l0` ->
    DC `SensorSource` · `agent_tool_call`(기본, BD-123) 문맥 -> guard `dcview_from_dc` -> guard `evaluate` -> 훅 응답
    지금 호출 = 훅 입력 `tool_use_id` 와 같은 `tool.start` 와 그 `tool.end`(같은 `tool_index`). 그 줄이 훅 순간 transcript 에
    있을지는 결정적이지 않다(T19) -- 판정하려는 호출은 그 호출의 근거가 아니다. 나란히 부른 **다른** 호출은 남긴다(닫는 쪽).
    Stop · SessionEnd 에서도 거둔다(판정 없음, `collected` 로 넘긴다). PostToolUse 는 관측만 하고 거두지 않는다 --
    그 순간 transcript 에는 그 도구의 결과가 아직 없다(T18: 0/5). 훅 입력의 `tool_response` 는 L0 로 들이지 않는다.

규칙
- **허가를 넓히지 않는다.** Guard ALLOW 에도 `"allow"` 를 내지 않고 `{}` 를 낸다. 공식 문서상 `"allow"` 는 권한 확인을
  건너뛴다 -- Guard 는 닫는 쪽으로만 쓴다(BD-07).
- shadow 는 판정을 기록만 하고 늘 `{}`. enforce 는 ALLOW 가 아니면 deny. 판정 중 예외(transcript 없음 · 꼴 오류 …)는
  enforce 에서 deny(닫는 쪽), shadow 에서 `{}`. 까닭에는 예외 **종류만** 싣는다.
- **꼴이 틀린 입력도 닫는다**(CMD-K10 S1). 빈 입력 · JSON 아님 · 꼴 위반(객체 아님, `hook_event_name` 없음, PreToolUse 의
  `tool_name` · `tool_input` · `transcript_path` 없음 · 틀린 타입)은 enforce 에서 PreToolUse deny(`rlo hook input error: <문제>`),
  shadow 에서 `{}`. 둘 다 종료 0, 기록 한 줄(`input_error`). 명령 훅은 그 밖의 예상 못 한 예외도 종료 0 으로 닫는다 --
  0 아닌 종료를 막지 않음으로 보는 호스트가 있다.
- **낡음만으로 생긴 D 에는 되살리는 법을 붙인다**(CMD-K10 S2). D 의 쓸 수 없는 필수 키가 모두 STALE(UNKNOWN · 없음이
  하나도 없음)이고 걸린 규칙이 D 뿐이면, 까닭 끝에 정해진 안내 `STALE_HINT` 를 붙인다. 판정은 그대로 deny 다.
- **거부마다 닫힌 대안 하나**(CMD-K11, `rlo/react.py`). 까닭의 마지막 줄은 `-- react: {json}`(kind · rule · cause ·
  tool · attempt · of · escalate), 기록 줄에도 같은 객체. 같은 (도구, 규칙, 원인) 거부의 세 번째는 report · escalate.
  shadow 는 기록만 한다. 대안은 허가를 넓히지 않는다.
- **D 는 세션을 통로에서 끊지 않는다**(CMD-K13). D 의 까닭이 낡음뿐이면 모형 파일에 선언된, 인자가 고정된 통로 호출
  (channels: 통로 이슈의 add_issue_comment · issue_read, `ga mail` Bash, send_message)은 지나간다(기록 allowed_while_stale).
  다른 external 호출은 D 그대로. 상태를 모르면(UNKNOWN · 없음) 통로도 막힌다. report 대안은 지금 부를 수 있는 통로 도구를
  이름 짓는다. 낡음은 판정마다 transcript 전체를 다시 거둬 정한다 -- 더 새 도구 결과가 있으면 모형이 아무것도 하지 않아도 풀린다.
- **훅에서는 두 건강 상태에 TTL 이 없다**(CMD-K13 S6, BD-246 선택 B). 판정마다 transcript 전체에서 다시 세우므로 알려진 값의
  나이는 모르는 것이 아니다. Sensor 의 시각 규칙(BD-57 · BD-63)은 그대로 -- 풀리지 않은 옛 실패의 UNRESOLVED_FAILURES 는 값으로
  DC · guard 에 간다(값만으로는 막지 않는다, BD-123 B2). D 는 상태를 모를 때(UNKNOWN · 없음)만 막는다. health_ttl=True 로 되돌린다.
- **판정 기한**(CMD-K13 S7): 판정이 deadline_s(기본 120 초, 호스트 기본 600 초보다 훨씬 짧게) 안에 끝나지 않으면 enforce 에서
  막는다(닫는 쪽) -- 시간이 다 된 훅을 호스트는 막지 않고 지나가게 하기 때문이다. 선언된 통로 호출만은 지나간다.
- **이어 받기**(CMD-K14 S2, Sensor `extend` · `evaluate="once"`): transcript 마다 RunState 를 두고 새 사건만 넣는다. 처음 ·
  다른 파일 · 지금 호출이 이미 들어 있으면 once(선형)로 새로 짓는다. 명령 훅은 부를 때마다 새 프로세스라 늘 once 다.
  값은 전부 넣은 것과 같다. 설정이 이력에 기대면(Sensor 가 전부 다시 짓는다) **사건 창**(CMD-K14 S1)으로 간다: 최근 window 개
  L0 사건만, 대기 중인 호출과 그 응답은 창 밖이라도 둔다. 기록 줄의 window 에 길(mode) · 넣은 수 · 버린 수.
- 겨냥은 없다(None): Claude Code 도구에는 MS 세계의 실체가 없다. 결정 문맥은 모형의 도구를 모두 겨냥 없이 내놓는다(F4) --
  모형에 없는 도구는 A1 로 막힌다(enforce).
"""
from __future__ import annotations

import argparse
import collections
import dataclasses
import json
import os
import sys
import threading
import time

from . import react as R

PRE, POST, POST_FAIL, STOP, SESSION_END = "PreToolUse", "PostToolUse", "PostToolUseFailure", "Stop", "SessionEnd"
SHADOW, ENFORCE = "shadow", "enforce"
POLICY = "rlo-hooks@1"
PURPOSE = "agent_tool_call"
HEALTH_STATES = ("execution_health", "tool_execution_health")    # 훅에서 TTL 을 두지 않는 상태(S6)
DEADLINE_S = 120.0                                              # 판정 기한(S7). None 이면 기한 없음
WINDOW = 400                                                    # 대체 길의 사건 창(CMD-K14 S1). None 이면 전부
CACHE_MAX = 8                                                   # 이어 받는 transcript 수(CMD-K14 S2, 오래된 것부터 버린다)
HEAD_BYTES = 4096                                               # 같은 파일인가를 보는 머리 바이트
# 낡음만으로 생긴 D 의 안내(정해진 글 하나). 읽기만 하는 호출(위험 등급 밖, D 가 보지 않는다)의 결과가 상태를 새로 관측한다
STALE_HINT = (" -- hint: the decision state is stale, not unavailable; make one read-only tool call to refresh it, "
              "then retry")


def intent_material(input_data: dict, dc_id: str, policy: str = POLICY) -> dict:
    """PreToolUse 입력 → ActionIntent 칸. 도구 이름이 행동 이름, 도구 입력이 인자. 겨냥은 없다(None).
    까닭 글은 훅 입력에 없다(지어내지 않는다)."""
    return {"dc_id": dc_id, "policy": policy, "action": input_data["tool_name"], "target": None,
            "args": dict(input_data.get("tool_input") or {}), "rationale": "", "used_keys": (), "author_kind": "llm"}


def without_call(events, tool_use_id) -> list:
    """L0 사건에서 그 호출(`tool.start.tool_use_id` 가 같은 것)의 tool.start · tool.end 를 뺀다(BD-124).
    tool.end 는 tool_index 로 짝짓는다(Telemetry 수집기). id 가 없으면 아무것도 빼지 않는다."""
    if not tool_use_id:
        return list(events)
    drop = {e["data"].get("tool_index") for e in events
            if e["type"] == "tool.start" and e["data"].get("tool_use_id") == tool_use_id}
    return [e for e in events if not (e["type"] in ("tool.start", "tool.end") and e["data"].get("tool_index") in drop)]


def window_events(events, k) -> "tuple[list, int]":
    """최근 k 개 L0 사건만 남긴다(CMD-K14 S1). (남긴 사건, 버린 수). k 가 None 이면 그대로.
    창 밖이라도 둔다: 결과가 없는 tool.start(대기 중 -- UNKNOWN 을 그대로 본다), 창 안 tool.end 의 짝 tool.start, 그리고
    남는 tool.start(창 안 것도)를 낸 llm.response(같은 call_index -- 그것 없이는 Sensor 가 호출을 세지 않는다). 차례는 원래 차례 그대로다."""
    events = list(events)
    if k is None or len(events) <= k:
        return events, 0
    cut = len(events) - k
    ended = {e["data"].get("tool_index") for e in events if e["type"] == "tool.end"}
    late = {e["data"].get("tool_index") for e in events[cut:] if e["type"] == "tool.end"}
    starts = [e for e in events[:cut] if e["type"] == "tool.start"
              and (e["data"].get("tool_index") not in ended or e["data"].get("tool_index") in late)]
    calls = {e["data"].get("call_index") for e in starts + events[cut:] if e["type"] == "tool.start"} - {None}
    ids = {id(e) for e in starts}
    kept = [e for e in events[:cut] if id(e) in ids or (e["type"] == "llm.response" and e["data"].get("call_index") in calls)]
    out = kept + events[cut:]
    return out, len(events) - len(out)


def file_identity(path) -> tuple:
    """(장치, inode, 크기, 머리 바이트). 같은 transcript 가 자라기만 했나를 본다(CMD-K14 S2)."""
    st = os.stat(path)
    with open(path, "rb") as f:
        head = f.read(HEAD_BYTES)
    return st.st_dev, st.st_ino, st.st_size, head


def same_transcript(old: tuple, new: tuple) -> bool:
    """new 가 old 의 뒤에 덧붙기만 한 같은 파일인가: 같은 장치 · inode, 줄지 않음, 머리가 같다."""
    return old[:2] == new[:2] and new[2] >= old[2] and new[3][:len(old[3])] == old[3]


class _Cached:
    __slots__ = ("ident", "rs", "ids")

    def __init__(self, ident, rs, ids):
        self.ident, self.rs, self.ids = ident, rs, ids


def check_window(k):
    if k is not None and (not isinstance(k, int) or isinstance(k, bool) or k <= 0):
        raise ValueError(f"window: 0 보다 큰 정수 또는 None ({k!r})")
    return k


def input_problem(input_data) -> "str | None":
    """훅 입력의 꼴 문제(없으면 None). 칸 이름 · 타입만 말하고 값은 싣지 않는다(CMD-K10 S1)."""
    if not isinstance(input_data, dict):
        return f"input is not a JSON object ({type(input_data).__name__})"
    ev = input_data.get("hook_event_name")
    if not isinstance(ev, str) or not ev:
        return "missing field hook_event_name" if ev is None else "field hook_event_name is not a non-empty string"
    if ev == PRE:
        for name, kind in (("tool_name", str), ("tool_input", dict), ("transcript_path", str)):
            v = input_data.get(name)
            if v is None:
                return f"missing field {name}"
            if not isinstance(v, kind) or (kind is str and not v):
                return f"field {name} is not a {'non-empty string' if kind is str else 'JSON object'}"
    return None


def stale_only(res, dcv) -> bool:
    """걸린 규칙이 D 뿐이고, D 의 쓸 수 없는 필수 키가 모두 STALE 인가(UNKNOWN · 없음은 하나도 없다)."""
    return (res.rule == "D" and all(r.startswith("[D]") for r in res.reasons)
            and set(dcv.missing_required) <= set(dcv.stale_keys))


def run_id_of(input_data: dict) -> str:
    return f"cc:{input_data.get('session_id') or 'session'}"


class TranscriptJudge:
    """훅 입력의 transcript 로 Guard 판정을 짓는다. 부를 때마다 transcript 전체를 다시 거둔다(BD-122 (5): 늘려 읽기 없음).

    model        에이전트 도구의 `ActionModel`(action-spec/1). 인자 · 위험 등급은 운영자 설정이다
    grants       external · irreversible 을 허락한 도구 이름(A7)
    risky        D 가 막는 위험 등급(없으면 guard 기본)
    purpose      DC 목적 이름 또는 DC `Purpose`(기본 agent_tool_call, BD-123)
    capabilities DC 능력(예: {"human_reviewer": True})
    sensor_config  Sensor `StateConfig`(문턱. 예: stall_repeat_threshold)
    clock        지금(unix ms)을 주는 함수. 기본은 벽시계 -- transcript 시각과 같은 기준이다
    substitutes  운영자의 대체표 {도구: [같은 목적의 도구, …]}(CMD-K11). A1 의 use_tool 대안이 여기서만 나온다
    channels     운영자의 통로 선언(CMD-K13, rlo.react.channels_of 의 꼴)
    health_ttl   False(기본): execution_health · tool_execution_health 에 TTL 을 두지 않는다(S6). True: Sensor 설정 그대로
    window       대체 길(아래)에서 Sensor 에 넣는 최근 L0 사건 수(CMD-K14 S1, 기본 WINDOW). 대기 중인 호출은 늘 둔다. None 이면 전부
    incremental  True(기본, CMD-K14 S2): Sensor 의 `extend` 로 이어 받는다 -- transcript 마다 RunState 를 두고 새 사건만 넣는다.
                 처음 · 다른 파일이면 `evaluate="once"`(선형)로 짓는다. 값은 전부 넣은 것과 같다(Sensor 의 보장). 설정이 이력에
                 기대면(Sensor 가 전부 다시 짓는 경우) 또는 False 면 사건 창(window)으로 간다
    """

    def __init__(self, model, *, grants=(), risky=None, purpose=PURPOSE, capabilities=None, sensor_config=None,
                 clock=None, policy: str = POLICY, substitutes=None, channels=None, health_ttl: bool = False,
                 window: "int | None" = WINDOW, incremental: bool = True):
        from guard import GuardModel
        from guard.views import DEFAULT_RISKY
        try:
            import llmsensor  # noqa: F401
        except ImportError as e:
            raise ImportError("훅 판정은 Sensor 가 필요하다 -- pip install \"rlo-sdk[sensor] @ git+…\"") from e
        self.gmodel = GuardModel.from_action_model(model, grants, DEFAULT_RISKY if risky is None else risky)
        self.purpose, self.capabilities, self.policy = purpose, dict(capabilities or {}), policy
        if not health_ttl:                           # S6: 판정마다 다시 세우는 훅에서는 알려진 값의 나이로 막지 않는다
            from llmsensor.state import DEFAULT_CONFIG
            base = sensor_config or DEFAULT_CONFIG
            sensor_config = base.with_(ttl_ms={**base.ttl_ms, **{k: None for k in HEALTH_STATES}})
        self.sensor_config = sensor_config
        self.clock = clock or (lambda: time.time() * 1000)
        self.substitutes = {k: list(v) for k, v in (substitutes or {}).items()}
        self.channels = list(channels or [])             # CMD-K13: rlo.react.channels_of 의 꼴
        self.window = check_window(window)
        self.incremental = incremental
        self.last_window = None                          # (tool_use_id, {mode, size, fed, dropped}) -- 마지막 거둠
        self._cache: "collections.OrderedDict[str, _Cached]" = collections.OrderedDict()
        self._lock = threading.Lock()                    # 기한을 넘긴 판정이 아직 돌아도 캐시를 함께 고치지 않게

    def can_extend(self) -> bool:
        """이어 받기를 쓰나: 켜져 있고, Sensor 에 extend 가 있고, 설정이 이력에 기대지 않는다(Sensor 와 같은 조건)."""
        if not self.incremental:
            return False
        try:
            from llmsensor.run_state import RunState, _history_free
            from llmsensor.state import DEFAULT_CONFIG
        except ImportError:
            return False
        return hasattr(RunState, "extend") and _history_free(self.sensor_config or DEFAULT_CONFIG)

    def _extended(self, path: str, events: list, current: set, kw: dict):
        """(RunState, 길). 같은 transcript 가 자랐으면 extend, 아니면 once 로 새로 짓는다. 지금 호출의 사건이 이미 캐시에
        들어 있으면(나란히 부른 앞 호출 때 들어감) 뺄 수 없으므로 새로 짓는다."""
        from llmsensor.run_state import from_l0
        key, ident = os.path.realpath(path), file_identity(path)
        c = self._cache.get(key)
        why = ("first" if c is None else "other_file" if not same_transcript(c.ident, ident)
               else "current_seen" if current & c.ids else None)
        if why is None:
            c.rs.extend(events)
            c.ident, mode = ident, c.rs.last_extend["mode"]
            c.ids.update(e["id"] for e in events)
            mode = "incremental" if mode in ("incremental", "unchanged") else mode
        else:
            c = self._cache[key] = _Cached(ident, from_l0(events, clock=self.clock, evaluate="once", **kw),
                                           {e["id"] for e in events})
            mode = f"once:{why}"
        self._cache.move_to_end(key)
        while len(self._cache) > CACHE_MAX:
            self._cache.popitem(last=False)
        return c.rs, mode

    def collect(self, input_data: dict, *, exclude_current: bool = False):
        """transcript -> L0 사건 -> Sensor RunState. (run_id, RunState). exclude_current 면 지금 호출을 뺀다(PreToolUse)."""
        from llmsensor.run_state import from_l0
        from telemetry.collect import from_cc_jsonl
        path = input_data.get("transcript_path")
        if not path:
            raise FileNotFoundError("transcript_path 가 없다")
        run = run_id_of(input_data)
        kw = {} if self.sensor_config is None else {"config": self.sensor_config}
        tid = input_data.get("tool_use_id")
        events = from_cc_jsonl(path, run)
        current: set = set()
        if exclude_current:
            kept = without_call(events, tid)
            current = {e["id"] for e in events} - {e["id"] for e in kept}
            events = kept
        if self.can_extend():
            with self._lock:
                rs, mode = self._extended(path, events, current, kw)
            self.last_window = (tid, {"mode": mode, "size": None, "fed": len(events), "dropped": 0})
            return run, rs
        events, dropped = window_events(events, self.window)
        self.last_window = (tid, {"mode": "window", "size": self.window, "fed": len(events), "dropped": dropped})
        return run, from_l0(events, clock=self.clock, **kw)

    def view(self, input_data: dict):
        """(DCView, DC 문맥 dict, RunState)."""
        from dc import DecisionContextBuilder, SensorSource
        from dc.purpose import PURPOSES
        from guard.dc_adapter import dcview_from_dc
        run, rs = self.collect(input_data, exclude_current=True)
        P = self.purpose if not isinstance(self.purpose, str) else PURPOSES[self.purpose]
        src = SensorSource(rs.engine, run_id=run)
        ctx = DecisionContextBuilder([src]).build(P, src.subject(), now_ms=self.clock(), capabilities=self.capabilities)
        record = ctx.to_dict()
        offers = {name: [None] for name in self.gmodel.specs}
        return dcview_from_dc(record, dataclasses.asdict(P), offers=offers), record, rs

    def __call__(self, input_data: dict, mode: str):
        """(ActionIntent, ValidationResult, GuardResult, DCView)."""
        from action.forms import ActionIntent
        from guard import StateView, evaluate
        dcv, _, _ = self.view(input_data)
        it = ActionIntent(**intent_material(input_data, dcv.dc_id, self.policy))
        v, res = evaluate(it, dcv, StateView({}), self.gmodel, mode)
        return it, v, res, dcv


class DeadlineExceeded(RuntimeError):
    """판정이 기한 안에 끝나지 않았다(CMD-K13 S7)."""


class HookAdapter:
    def __init__(self, judge, *, mode: str = SHADOW, record=None, observe=None, collected=None,
                 deadline_s: "float | None" = None, context_budget=None):
        """judge(input_data, mode) -> (ActionIntent, ValidationResult, GuardResult, DCView) · 거두기는 judge.collect 가 있으면.
        record(kind, dict) 기록(없으면 버림) · observe(input_data) 실행 뒤 관측(없으면 버림) ·
        collected(event, run_id, run_state) Stop · SessionEnd 에서 거둔 상태(없으면 버림)."""
        if mode not in (SHADOW, ENFORCE):
            raise ValueError(f"모르는 모드 {mode!r}")
        self.judge, self.mode = judge, mode
        self.record = record or (lambda kind, d: None)
        self.observe = observe or (lambda d: None)
        self.collected = collected or (lambda ev, run, rs: None)
        self.deadline_s = deadline_s             # 판정 기한(초). None · 0 이면 기한 없음
        from .ctxbudget import Budget
        self.budget = Budget.of(context_budget)  # CMD-K17: 컨텍스트 예산(없으면 꺼짐 -- 기본 예산은 없다)

    def react_for(self, input_data: dict, rule: str, cause: str, tool=None, dcv=None, stale=False) -> dict:
        """닫힌 대안(CMD-K11). 되풀이는 transcript 의 같은 거부로 센다 -- 읽지 못하면 첫 번째로 본다.
        report 면 지금 부를 수 있는 통로 도구를 이름 짓는다(CMD-K13 S3) -- 없으면 이름 짓지 않는다."""
        try:
            prior = R.prior_denies(input_data["transcript_path"], input_data["tool_name"], rule, cause)
        except Exception:
            prior = 0
        obj = R.react(rule, cause, tool, prior)
        if obj["kind"] == "report":
            ch = R.report_channel(getattr(self.judge, "gmodel", None), getattr(self.judge, "channels", None), dcv, stale)
            if ch is not None:
                obj["tool"] = ch
        return obj

    def _judged(self, input_data: dict):
        """판정. 기한이 있으면 따로 된 스레드에서 돌리고 기한까지만 기다린다 -- 판정은 부수 효과가 없어 버려도 된다."""
        if not self.deadline_s:
            return self.judge(input_data, self.mode)
        box = {}

        def work():
            try:
                box["v"] = self.judge(input_data, self.mode)
            except BaseException as e:           # noqa: BLE001 -- 이 스레드 밖으로 옮긴다
                box["e"] = e
        t = threading.Thread(target=work, name="rlo-judge", daemon=True)
        t.start()
        t.join(self.deadline_s)
        if t.is_alive():
            raise DeadlineExceeded(self.deadline_s)
        if "e" in box:
            raise box["e"]
        return box["v"]

    def past_deadline(self, input_data: dict) -> dict:
        """기한을 넘었다: 닫는 쪽(enforce deny) -- 다만 선언된 통로 호출은 지나간다(통로를 끊지 않는다)."""
        ch = R.channel_of(input_data.get("tool_name"), input_data.get("tool_input"), getattr(self.judge, "channels", None))
        base = {"tool_use_id": input_data.get("tool_use_id"), "deadline_s": self.deadline_s}
        if ch is not None:
            self.record("guard_deadline", dict(base, allowed=f"channel:{ch['tool']}"))
            return {}
        obj = self.react_for(input_data, "hook", "deadline")
        self.record("guard_deadline", dict(base, react=obj))
        return deny(f"rlo hook deadline exceeded ({self.deadline_s:g}s)" + R.line(obj)) if self.mode == ENFORCE else {}

    def pre_tool_use(self, input_data: dict) -> dict:
        """컨텍스트 예산(있으면) 다음 가드 판정. 가드의 deny 가 늘 이긴다 -- 예산은 deny 를 allow 로 바꾸지 않는다."""
        if self.budget is None:
            return self._guard_pre_tool_use(input_data)
        from . import ctxbudget as CB
        b = self.budget
        try:
            ctx = b.context(input_data["transcript_path"])
            stage, out = CB.decide(ctx, input_data.get("tool_name"), input_data.get("tool_input"), b)
        except Exception as e:                     # 예산이 고장 나도 가드는 돈다(모름처럼)
            ctx, stage, out = None, "unknown", {}
            self.record("context_budget_error", {"tool_use_id": input_data.get("tool_use_id"), "exception": type(e).__name__})
        denies = out.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"
        self.record("context_budget", {"tool_use_id": input_data.get("tool_use_id"), "tool_name": input_data.get("tool_name"),
                                       "stage": stage, "ctx": ctx, "soft": b.soft, "hard": b.hard, "mode": b.mode,
                                       "runtime": b.runtime or CB.DEFAULT_RUNTIME,
                                       "enforced": b.mode == "enforce" and bool(out), "denied": b.mode == "enforce" and denies})
        if b.mode == "enforce" and denies:
            return out                             # hard 를 넘었고 checkpoint 도구가 아니다 -- 가드를 물을 것도 없다
        guard = self._guard_pre_tool_use(input_data)
        if guard or b.mode != "enforce" or not out:
            return guard                           # 가드가 무엇이든 내면(deny) 가드가 이긴다 · shadow 는 아무것도 바꾸지 않는다
        return out                                 # 가드가 막지 않았다 -- 예산의 알림(additionalContext)만, allow 는 없다

    def _guard_pre_tool_use(self, input_data: dict) -> dict:
        try:
            try:
                it, v, res, dcv = self._judged(input_data)
            except DeadlineExceeded:
                return self.past_deadline(input_data)
            row = {"tool_use_id": input_data.get("tool_use_id"), "tool_name": it.action,
                   "tool_input_keys": sorted(it.args),          # 칸 이름만(값은 싣지 않는다) -- 모형을 넓힐 근거
                   "dc_id": dcv.dc_id, "complete": dcv.complete,
                   "missing_required": list(dcv.missing_required), "result": res.to_dict()}
            lw = getattr(self.judge, "last_window", None)
            if lw and lw[0] == input_data.get("tool_use_id"):
                row["window"] = lw[1]                # CMD-K14: 창 크기 · 넣은 수 · 버린 수
            obj, stale = None, res.verdict != "ALLOW" and stale_only(res, dcv)
            ch = R.channel_of(it.action, it.args, getattr(self.judge, "channels", None)) if stale else None
            if ch is not None:                       # CMD-K13 S1: 낡음뿐인 D 는 고정된 통로 호출을 막지 않는다
                self.record("guard", dict(row, allowed_while_stale=f"channel:{ch['tool']}"))
                return {}
            if res.verdict != "ALLOW":
                rule, cause, tool = R.classify(it, res, dcv, getattr(self.judge, "gmodel", None),
                                               getattr(self.judge, "substitutes", None))
                obj = self.react_for(input_data, rule, cause, tool, dcv, stale)
            self.record("guard", dict(row, react=obj) if obj else row)
            if self.mode == ENFORCE and res.verdict != "ALLOW":
                hint = STALE_HINT if stale_only(res, dcv) else ""                 # 자르기 뒤에 붙인다 -- 잘리지 않게
                return deny(f"guard {res.verdict}({res.rule}): " + "; ".join(res.reasons)[:500] + hint + R.line(obj))
            return {}
        except Exception as e:                     # 닫는 쪽: enforce 면 막는다. 메시지는 종류만
            obj = self.react_for(input_data, "E", "guard_error")
            self.record("guard_error", {"tool_use_id": input_data.get("tool_use_id"), "exception": type(e).__name__,
                                        "react": obj})
            return deny(f"guard error: {type(e).__name__}" + R.line(obj)) if self.mode == ENFORCE else {}

    def post_tool_use(self, input_data: dict) -> dict:
        self.observe(input_data)                   # 거두지 않는다 -- 결과는 다음 훅 때 transcript 에서 온다(BD-122 (2))
        return {}

    def end(self, input_data: dict) -> dict:
        """Stop · SessionEnd: 거두기만 한다. 막지 않는다(Stop 을 막으면 대화가 이어진다)."""
        ev = input_data.get("hook_event_name")
        try:
            run, rs = self.judge.collect(input_data)
            self.collected(ev, run, rs)
        except Exception as e:
            self.record("collect_error", {"event": ev, "exception": type(e).__name__})
        return {}

    def bad_input(self, problem: str, input_data=None) -> dict:
        """꼴이 틀린 입력(CMD-K10 S1): 기록 한 줄, enforce 면 PreToolUse deny(무슨 사건인지 믿을 수 없으니 닫는다)."""
        ev = input_data.get("hook_event_name") if isinstance(input_data, dict) else None
        obj = R.react("input", "malformed_input")
        self.record("input_error", {"event": ev if isinstance(ev, str) else None, "problem": problem, "react": obj})
        return deny(f"rlo hook input error: {problem}" + R.line(obj)) if self.mode == ENFORCE else {}

    def handle(self, input_data: dict) -> dict:
        problem = input_problem(input_data)
        if problem:
            return self.bad_input(problem, input_data)
        ev = input_data.get("hook_event_name")
        if ev == PRE:
            return self.pre_tool_use(input_data)
        if ev in (POST, POST_FAIL):
            return self.post_tool_use(input_data)
        if ev in (STOP, SESSION_END):
            return self.end(input_data)
        return {}

    # Agent SDK(Python) 콜백 꼴: async (input_data, tool_use_id, context) -> dict
    async def callback(self, input_data, tool_use_id, context):
        return self.handle(input_data)


def guard_hooks(model, *, mode: str = SHADOW, record=None, observe=None, collected=None,
                deadline_s: "float | None" = DEADLINE_S, context_budget=None, **judge_kw) -> HookAdapter:
    """한 줄로: transcript 판정을 단 훅 어댑터. judge_kw 는 `TranscriptJudge` 의 인자(grants · purpose · …).
    deadline_s: 판정 기한(초, 기본 120). None 이면 기한 없음.
    context_budget: CMD-K17 컨텍스트 예산 {soft, hard, state_paths?, mode?}(mode 기본 shadow). None 이면 꺼짐."""
    return HookAdapter(TranscriptJudge(model, **judge_kw), mode=mode, record=record, observe=observe,
                       collected=collected, deadline_s=deadline_s, context_budget=context_budget)


def deny(reason: str) -> dict:
    return {"hookSpecificOutput": {"hookEventName": PRE, "permissionDecision": "deny",
                                   "permissionDecisionReason": reason}}


def read_input(stdin) -> "tuple[object, str | None]":
    """표준입력 → (JSON 값, 문제). 빈 입력 · UTF-8 아님 · JSON 아님은 문제로 낸다(값은 None)."""
    try:
        raw = stdin.read()
    except UnicodeDecodeError:
        return None, "input is not UTF-8 text"
    if not raw.strip():
        return None, "empty input"
    try:
        return json.loads(raw), None
    except ValueError:
        return None, "input is not JSON"


def run_command_hook(adapter: HookAdapter, stdin, stdout) -> int:
    """Claude Code 명령 훅: 표준입력의 JSON 하나 → 표준출력의 JSON 하나, 종료 코드 0(막기는 JSON 의 deny 로)."""
    data, problem = read_input(stdin)
    out = adapter.bad_input(problem) if problem else adapter.handle(data)
    if out:
        json.dump(out, stdout)
    return 0


# ── 명령 훅 입구: python -m rlo.hooks --model <action-model/1 JSON> [--mode enforce] [--grant Bash] … ──────────────
#    깔기 · 떼기: python -m rlo.hooks install-hook | uninstall-hook [--settings …] (rlo/install.py)

def _parser():
    ap = argparse.ArgumentParser(prog="python -m rlo.hooks", description="Claude Code 명령 훅 -- Guard 판정(rlo)")
    ap.add_argument("--model", required=True, help="에이전트 도구의 ActionModel(action-model/1) JSON 경로")
    ap.add_argument("--mode", default=SHADOW, choices=(SHADOW, ENFORCE))
    ap.add_argument("--grant", action="append", default=[], help="external · irreversible 을 허락할 도구 이름(되풀이)")
    ap.add_argument("--purpose", default=PURPOSE, help="DC 목적 이름")
    ap.add_argument("--stall-threshold", type=int, default=None, help="Sensor stall_repeat_threshold(운영자 문턱)")
    ap.add_argument("--record", default=None, help="판정 기록을 덧붙일 JSONL 경로(없으면 쓰지 않는다)")
    ap.add_argument("--now-ms", type=float, default=None, help="지금(unix ms)을 고정한다 -- 기록된 transcript 를 다시 돌릴 때만")
    ap.add_argument("--deadline-s", type=float, default=DEADLINE_S,
                    help=f"판정 기한(초, 기본 {DEADLINE_S:g}). 넘으면 enforce 에서 막는다(통로 호출만 지나간다). 0 이면 기한 없음")
    ap.add_argument("--health-ttl", action="store_true",
                    help="두 건강 상태에도 Sensor TTL 을 둔다(CMD-K13 S6 이전 동작 -- 재생 · 비교용)")
    ap.add_argument("--window", type=int, default=WINDOW,
                    help=f"대체 길의 사건 창(기본 {WINDOW}, CMD-K14). 대기 중인 호출은 늘 넣는다. 0 이면 전부")
    ap.add_argument("--budget-soft", type=int, default=None, help="컨텍스트 예산 soft(토큰, CMD-K17). soft · hard 가 없으면 꺼짐")
    ap.add_argument("--budget-hard", type=int, default=None, help="컨텍스트 예산 hard(토큰)")
    ap.add_argument("--budget-state", action="append", default=[], help="checkpoint 로 쓸 상태 파일(되풀이, 기본 STATE.md)")
    ap.add_argument("--budget-runtime", default=None,
                    help="transcript 읽개(rlo.transcripts) 이름(기본 claude_code). python -m rlo.plugins list 로 본다")
    ap.add_argument("--budget-usage-format", default=None, help="usage 꼴(rlo.usage) 이름(기본: 그 읽개의 꼴)")
    ap.add_argument("--budget-mode", default="shadow", choices=("shadow", "enforce"),
                    help="예산 모드(기본 shadow -- 기록만). 가드 모드(--mode)와 따로다")
    ap.add_argument("--no-incremental", action="store_true",
                    help="Sensor 이어 받기(extend · once) 대신 사건 창으로 넣는다(CMD-K14 S1 동작 -- 비교용)")
    return ap


def _recorder(path):
    if not path:
        return None

    def record(kind, d):
        with open(path, "a", encoding="utf-8") as out:
            out.write(json.dumps({"kind": kind, **d}, ensure_ascii=False) + "\n")
    return record


def _adapter_from_args(a, record=None) -> HookAdapter:
    model, substitutes, channels = R.load_hook_model(a.model)   # 모형 파일 옆 칸(action-model/1 밖, CMD-K11 · K13)
    cfg = None
    if a.stall_threshold is not None:
        from llmsensor.state import DEFAULT_CONFIG
        cfg = DEFAULT_CONFIG.with_(stall_repeat_threshold=a.stall_threshold)
    clock = None if a.now_ms is None else (lambda now=a.now_ms: now)
    return guard_hooks(model, mode=a.mode, record=record, grants=a.grant, purpose=a.purpose, sensor_config=cfg,
                       clock=clock, substitutes=substitutes, channels=channels, health_ttl=a.health_ttl,
                       window=a.window or None, incremental=not a.no_incremental,
                       deadline_s=a.deadline_s or None, context_budget=_budget_from_args(a))


def _budget_from_args(a):
    if a.budget_soft is None and a.budget_hard is None:
        if a.budget_state or a.budget_mode != "shadow" or a.budget_runtime or a.budget_usage_format:
            raise ValueError("--budget-state / --budget-mode need --budget-soft and --budget-hard")
        return None                                # 기본 예산은 없다(BD-289)
    if a.budget_soft is None or a.budget_hard is None:
        raise ValueError("context budget needs both --budget-soft and --budget-hard")
    return {"soft": a.budget_soft, "hard": a.budget_hard, "state_paths": a.budget_state or ["STATE.md"],
            "mode": a.budget_mode, "runtime": a.budget_runtime, "usage_format": a.budget_usage_format}


def main(argv=None, stdin=None, stdout=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ("install-hook", "uninstall-hook"):      # 깔기 · 떼기(CMD-K6) -- rlo/install.py
        from .install import main as install_main
        return install_main(argv[0], argv[1:])
    if argv and argv[0] == "claude-plugin":                      # Claude Code 플러그인 꼴 짓기(CMD-K18 S5)
        from .install import plugin_main
        return plugin_main(argv[1:])
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    a = _parser().parse_args(argv)
    data, problem = read_input(stdin)
    if problem is None:
        problem = input_problem(data)
    try:
        record = _recorder(a.record)
        if problem:                                # 꼴이 틀린 입력은 모형을 읽기 전에 닫는다(CMD-K10 S1)
            out = HookAdapter(None, mode=a.mode, record=record).bad_input(problem, data)
        else:
            try:
                adapter = _adapter_from_args(a, record)
            except Exception as e:                 # 설정이 틀려도 enforce 의 PreToolUse 는 닫는다
                if a.mode == ENFORCE and data.get("hook_event_name") == PRE:
                    json.dump(deny(f"rlo hook config error: {type(e).__name__}"
                                   + R.line(R.react("config", "config_error"))), stdout)
                return 0
            out = adapter.handle(data)
    except Exception as e:                         # 예상 못 한 예외(기록 쓰기 실패 …)도 종료 0 으로 닫는다
        ev = data.get("hook_event_name") if isinstance(data, dict) else None
        out = (deny(f"rlo hook error: {type(e).__name__}" + R.line(R.react("hook", "hook_error")))
               if a.mode == ENFORCE and ev in (PRE, None) else {})
    if out:
        json.dump(out, stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
