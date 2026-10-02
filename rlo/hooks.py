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
- 겨냥은 없다(None): Claude Code 도구에는 MS 세계의 실체가 없다. 결정 문맥은 모형의 도구를 모두 겨냥 없이 내놓는다(F4) --
  모형에 없는 도구는 A1 로 막힌다(enforce).
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import time

PRE, POST, POST_FAIL, STOP, SESSION_END = "PreToolUse", "PostToolUse", "PostToolUseFailure", "Stop", "SessionEnd"
SHADOW, ENFORCE = "shadow", "enforce"
POLICY = "rlo-hooks@1"
PURPOSE = "agent_tool_call"


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
    """

    def __init__(self, model, *, grants=(), risky=None, purpose=PURPOSE, capabilities=None, sensor_config=None,
                 clock=None, policy: str = POLICY):
        from guard import GuardModel
        from guard.views import DEFAULT_RISKY
        try:
            import llmsensor  # noqa: F401
        except ImportError as e:
            raise ImportError("훅 판정은 Sensor 가 필요하다 -- pip install \"rlo-sdk[sensor] @ git+…\"") from e
        self.gmodel = GuardModel.from_action_model(model, grants, DEFAULT_RISKY if risky is None else risky)
        self.purpose, self.capabilities, self.policy = purpose, dict(capabilities or {}), policy
        self.sensor_config = sensor_config
        self.clock = clock or (lambda: time.time() * 1000)

    def collect(self, input_data: dict, *, exclude_current: bool = False):
        """transcript -> L0 사건 -> Sensor RunState. (run_id, RunState). exclude_current 면 지금 호출을 뺀다(PreToolUse)."""
        from llmsensor.run_state import from_l0
        from telemetry.collect import from_cc_jsonl
        path = input_data.get("transcript_path")
        if not path:
            raise FileNotFoundError("transcript_path 가 없다")
        run = run_id_of(input_data)
        kw = {} if self.sensor_config is None else {"config": self.sensor_config}
        events = from_cc_jsonl(path, run)
        if exclude_current:
            events = without_call(events, input_data.get("tool_use_id"))
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


class HookAdapter:
    def __init__(self, judge, *, mode: str = SHADOW, record=None, observe=None, collected=None):
        """judge(input_data, mode) -> (ActionIntent, ValidationResult, GuardResult, DCView) · 거두기는 judge.collect 가 있으면.
        record(kind, dict) 기록(없으면 버림) · observe(input_data) 실행 뒤 관측(없으면 버림) ·
        collected(event, run_id, run_state) Stop · SessionEnd 에서 거둔 상태(없으면 버림)."""
        if mode not in (SHADOW, ENFORCE):
            raise ValueError(f"모르는 모드 {mode!r}")
        self.judge, self.mode = judge, mode
        self.record = record or (lambda kind, d: None)
        self.observe = observe or (lambda d: None)
        self.collected = collected or (lambda ev, run, rs: None)

    def pre_tool_use(self, input_data: dict) -> dict:
        try:
            it, v, res, dcv = self.judge(input_data, self.mode)
            self.record("guard", {"tool_use_id": input_data.get("tool_use_id"), "tool_name": it.action,
                                  "tool_input_keys": sorted(it.args),          # 칸 이름만(값은 싣지 않는다) -- 모형을 넓힐 근거
                                  "dc_id": dcv.dc_id, "complete": dcv.complete,
                                  "missing_required": list(dcv.missing_required), "result": res.to_dict()})
            if self.mode == ENFORCE and res.verdict != "ALLOW":
                return deny(f"guard {res.verdict}({res.rule}): " + "; ".join(res.reasons)[:500])
            return {}
        except Exception as e:                     # 닫는 쪽: enforce 면 막는다. 메시지는 종류만
            self.record("guard_error", {"tool_use_id": input_data.get("tool_use_id"), "exception": type(e).__name__})
            return deny(f"guard error: {type(e).__name__}") if self.mode == ENFORCE else {}

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

    def handle(self, input_data: dict) -> dict:
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


def guard_hooks(model, *, mode: str = SHADOW, record=None, observe=None, collected=None, **judge_kw) -> HookAdapter:
    """한 줄로: transcript 판정을 단 훅 어댑터. judge_kw 는 `TranscriptJudge` 의 인자(grants · purpose · …)."""
    return HookAdapter(TranscriptJudge(model, **judge_kw), mode=mode, record=record, observe=observe,
                       collected=collected)


def deny(reason: str) -> dict:
    return {"hookSpecificOutput": {"hookEventName": PRE, "permissionDecision": "deny",
                                   "permissionDecisionReason": reason}}


def run_command_hook(adapter: HookAdapter, stdin, stdout) -> int:
    """Claude Code 명령 훅: 표준입력의 JSON 하나 → 표준출력의 JSON 하나, 종료 코드 0(막기는 JSON 의 deny 로)."""
    out = adapter.handle(json.load(stdin))
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
    return ap


def _adapter_from_args(a) -> HookAdapter:
    from action.spec import ActionModel
    with open(a.model, encoding="utf-8") as f:
        model = ActionModel.from_dict(json.load(f))
    cfg = None
    if a.stall_threshold is not None:
        from llmsensor.state import DEFAULT_CONFIG
        cfg = DEFAULT_CONFIG.with_(stall_repeat_threshold=a.stall_threshold)
    record = None
    if a.record:
        def record(kind, d, path=a.record):
            with open(path, "a", encoding="utf-8") as out:
                out.write(json.dumps({"kind": kind, **d}, ensure_ascii=False) + "\n")
    clock = None if a.now_ms is None else (lambda now=a.now_ms: now)
    return guard_hooks(model, mode=a.mode, record=record, grants=a.grant, purpose=a.purpose, sensor_config=cfg,
                       clock=clock)


def main(argv=None, stdin=None, stdout=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ("install-hook", "uninstall-hook"):      # 깔기 · 떼기(CMD-K6) -- rlo/install.py
        from .install import main as install_main
        return install_main(argv[0], argv[1:])
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    a = _parser().parse_args(argv)
    raw = stdin.read()
    try:
        adapter = _adapter_from_args(a)
    except Exception as e:                         # 설정이 틀려도 enforce 의 PreToolUse 는 닫는다
        try:
            ev = json.loads(raw).get("hook_event_name")
        except Exception:
            ev = None
        if a.mode == ENFORCE and ev == PRE:
            json.dump(deny(f"rlo hook config error: {type(e).__name__}"), stdout)
        return 0
    import io
    return run_command_hook(adapter, io.StringIO(raw), stdout)


if __name__ == "__main__":
    sys.exit(main())
