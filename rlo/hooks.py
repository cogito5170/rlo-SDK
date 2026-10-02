"""에이전트 SDK 훅 어댑터 -- 시제품 action `9642bb3` `sdk_draft/hooks.py` 를 옮겼다(BD-120 (5), 설계는 action docs/SDK.md §5).
공식 문서로 확인한 입출력 꼴만 쓴다. 판정 `judge` 를 실제 상태로 잇는 일은 S2-7b 다 -- 지금은 주입받는다.

    PreToolUse   입력 {session_id, transcript_path, cwd, permission_mode, hook_event_name, tool_name, tool_input,
                       tool_use_id}                       (code.claude.com/docs/en/hooks.md "PreToolUse input")
                 막기  {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                        "permissionDecisionReason": …}}   ("PreToolUse decision control")
    PostToolUse  입력 … + tool_response · duration_ms     ("PostToolUse input")
    PostToolUseFailure 입력 … + error · is_interrupt · duration_ms   ("PostToolUseFailure input")
    Agent SDK(Python) 콜백  async (input_data, tool_use_id, context) -> dict   (agent-sdk/hooks)

규칙
- **허가를 넓히지 않는다.** Guard ALLOW 에도 `"allow"` 를 내지 않고 `{}` 를 낸다. 공식 문서상 `"allow"` 는 권한 확인을
  건너뛴다(hooks.md "PreToolUse decision control") -- Guard 는 닫는 쪽으로만 쓴다(BD-07).
- shadow 는 판정을 기록만 하고 늘 `{}`. enforce 는 ALLOW 가 아니면 deny. 판정 중 예외는 enforce 에서 deny(닫는 쪽), shadow 에서 `{}`.
- 판정(`judge`)은 주입한다: `judge(intent) -> GuardResult`. 무엇으로 DC · 상태를 짓는지는 S2-7b 에서 잇는다.
- 실행 뒤 훅은 L0 를 **직접 쓰지 않는다**(SDK.md §5 권고 (b)): 같은 도구 호출을 Telemetry 의 cc_jsonl 수집기도 적으므로
  둘 다 쓰면 한 사실이 두 사건이 된다(BD-97 Q3). 여기서는 받은 것을 `observe(input_data)` 에 넘기기만 한다.
"""
from __future__ import annotations

import json

PRE, POST, POST_FAIL = "PreToolUse", "PostToolUse", "PostToolUseFailure"
SHADOW, ENFORCE = "shadow", "enforce"


def intent_material(input_data: dict, dc_id: str, policy: str) -> dict:
    """PreToolUse 입력 → ActionIntent 칸. 도구 이름이 행동 이름, 도구 입력이 인자. 겨냥은 없다(None) --
    Claude Code 도구에는 MS 세계의 실체가 없다. 까닭 글은 훅 입력에 없다(지어내지 않는다)."""
    return {"dc_id": dc_id, "policy": policy, "action": input_data["tool_name"], "target": None,
            "args": dict(input_data.get("tool_input") or {}), "rationale": "", "used_keys": (), "author_kind": "llm"}


class HookAdapter:
    def __init__(self, judge, *, dc_id_of, policy: str, mode: str = SHADOW, record=None, observe=None):
        """judge(intent) -> GuardResult · dc_id_of(input_data) -> 그 순간의 결정 문맥 id ·
        record(kind, dict) 원장(없으면 버림) · observe(input_data) 실행 뒤 관측 넘김(없으면 버림)."""
        if mode not in (SHADOW, ENFORCE):
            raise ValueError(f"모르는 모드 {mode!r}")
        self.judge, self.dc_id_of, self.policy, self.mode = judge, dc_id_of, policy, mode
        self.record = record or (lambda kind, d: None)
        self.observe = observe or (lambda d: None)

    def pre_tool_use(self, input_data: dict) -> dict:
        from action.forms import ActionIntent
        try:
            it = ActionIntent(**intent_material(input_data, self.dc_id_of(input_data), self.policy))
            res = self.judge(it)
            self.record("guard", {"tool_use_id": input_data.get("tool_use_id"), "intent": it.to_dict(),
                                  "result": res.to_dict()})
            if self.mode == ENFORCE and res.verdict != "ALLOW":
                return deny(f"guard {res.verdict}({res.rule}): " + "; ".join(res.reasons)[:500])
            return {}
        except Exception as e:                     # 닫는 쪽: enforce 면 막는다. 메시지는 종류만
            self.record("guard_error", {"tool_use_id": input_data.get("tool_use_id"), "exception": type(e).__name__})
            return deny(f"guard error: {type(e).__name__}") if self.mode == ENFORCE else {}

    def post_tool_use(self, input_data: dict) -> dict:
        self.observe(input_data)
        return {}

    def handle(self, input_data: dict) -> dict:
        ev = input_data.get("hook_event_name")
        if ev == PRE:
            return self.pre_tool_use(input_data)
        if ev in (POST, POST_FAIL):
            return self.post_tool_use(input_data)
        return {}

    # Agent SDK(Python) 콜백 꼴: async (input_data, tool_use_id, context) -> dict
    async def callback(self, input_data, tool_use_id, context):
        return self.handle(input_data)


def deny(reason: str) -> dict:
    return {"hookSpecificOutput": {"hookEventName": PRE, "permissionDecision": "deny",
                                   "permissionDecisionReason": reason}}


def run_command_hook(adapter: HookAdapter, stdin, stdout) -> int:
    """Claude Code 명령 훅: 표준입력의 JSON 하나 → 표준출력의 JSON 하나, 종료 코드 0(막기는 JSON 의 deny 로)."""
    out = adapter.handle(json.load(stdin))
    if out:
        json.dump(out, stdout)
    return 0
