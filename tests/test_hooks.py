"""훅 어댑터(BD-120 (5)) -- 시제품 action `720b7d9` tests/test_sdk_draft.py::Hooks 를 옮겼다. 진짜 guard · 공식 문서의 입력 예 그대로."""
import asyncio
import io
import json
import unittest

import guard
from action.spec import ActionModel, ActionSpec

from rlo import hooks

# ── 훅 어댑터 ────────────────────────────────────────────────────────────────

BASH = ActionSpec("Bash", "1", None, params={"command": {"type": "string"},
                                              "description": {"type": "string", "required": False},
                                              "timeout": {"type": "number", "required": False},
                                              "run_in_background": {"type": "bool", "required": False}},
                  risk="external")
PRE_INPUT = {   # code.claude.com/docs/en/hooks.md "PreToolUse input" 의 예 그대로
    "session_id": "abc123", "transcript_path": "/home/user/.claude/projects/.../transcript.jsonl",
    "cwd": "/home/user/my-project", "permission_mode": "default", "hook_event_name": "PreToolUse",
    "tool_name": "Bash", "tool_input": {"command": "npm test", "description": "Run test suite", "timeout": 120000,
                                        "run_in_background": False},
    "tool_use_id": "toolu_01ABC123..."}
POST_FAIL_INPUT = {  # 같은 문서 "PostToolUseFailure input" 의 예 그대로
    "session_id": "abc123", "transcript_path": "/Users/.../.claude/projects/.../00893aaf-19fa-41d2-8238-13269b9b3ca0.jsonl",
    "cwd": "/Users/...", "permission_mode": "default", "hook_event_name": "PostToolUseFailure", "tool_name": "Bash",
    "tool_input": {"command": "npm test", "description": "Run test suite"}, "tool_use_id": "toolu_01ABC123...",
    "error": "Exit code 1\nError: Cannot find module 'express'", "is_interrupt": False, "duration_ms": 4187}


class Hooks(unittest.TestCase):
    def setUp(self):
        self.g = guard
        self.H = hooks
        self.records = []

    def adapter(self, mode, grants=(), judge=None, observe=None):
        g = self.g
        model = g.GuardModel.from_action_model(ActionModel("cc-tools-1", (BASH,)), grants=grants)
        dc = g.DCView("dc-hook-test", offers={"Bash": [None]}, seen={})
        state = g.StateView({})
        judge = judge or (lambda it: g.evaluate(it, dc, state, model, mode)[1])
        return self.H.HookAdapter(judge, dc_id_of=lambda d: "dc-hook-test", policy="cc-hook@0", mode=mode,
                                  record=lambda k, d: self.records.append((k, d)), observe=observe)

    def test_enforce_denies_in_the_documented_shape(self):
        out = self.adapter("enforce").handle(PRE_INPUT)                   # 허가 없는 external → A7
        self.assertEqual(set(out), {"hookSpecificOutput"})
        hso = out["hookSpecificOutput"]
        self.assertEqual((hso["hookEventName"], hso["permissionDecision"]), ("PreToolUse", "deny"))
        self.assertIn("A7", hso["permissionDecisionReason"])
        self.assertEqual(self.records[0][1]["result"]["verdict"], "DENY")

    def test_allow_never_widens_permission(self):
        out = self.adapter("enforce", grants=("Bash",)).handle(PRE_INPUT)
        self.assertEqual(out, {})                                            # "allow" 를 내지 않는다
        self.assertEqual(self.records[0][1]["result"]["verdict"], "ALLOW")

    def test_shadow_records_but_never_blocks(self):
        out = self.adapter("shadow").handle(PRE_INPUT)
        self.assertEqual(out, {})
        self.assertEqual(self.records[0][1]["result"]["verdict"], "DENY")

    def test_unknown_tool_argument_is_a4(self):
        bad = dict(PRE_INPUT, tool_input={"command": "ls", "dangerously": True})
        out = self.adapter("enforce", grants=("Bash",)).handle(bad)
        self.assertIn("A4", out["hookSpecificOutput"]["permissionDecisionReason"])

    def test_judge_error_closes_only_in_enforce(self):
        def boom(it):
            raise RuntimeError("secret detail")
        out = self.adapter("enforce", judge=boom).handle(PRE_INPUT)
        self.assertEqual(out["hookSpecificOutput"]["permissionDecisionReason"], "guard error: RuntimeError")
        self.assertEqual(self.adapter("shadow", judge=boom).handle(PRE_INPUT), {})

    def test_post_hooks_only_observe(self):
        seen = []
        a = self.adapter("enforce", observe=seen.append)
        post = dict(PRE_INPUT, hook_event_name="PostToolUse", tool_response={"stdout": "ok"}, duration_ms=12)
        self.assertEqual(a.handle(post), {})
        self.assertEqual(a.handle(POST_FAIL_INPUT), {})
        self.assertEqual([d["hook_event_name"] for d in seen], ["PostToolUse", "PostToolUseFailure"])
        self.assertEqual(self.records, [])                                   # 실행 뒤 훅은 판정하지 않는다

    def test_command_hook_and_sdk_callback(self):
        a = self.adapter("enforce")
        out = io.StringIO()
        self.assertEqual(self.H.run_command_hook(a, io.StringIO(json.dumps(PRE_INPUT)), out), 0)
        self.assertEqual(json.loads(out.getvalue())["hookSpecificOutput"]["permissionDecision"], "deny")
        got = asyncio.run(a.callback(PRE_INPUT, "toolu_01ABC123...", None))
        self.assertEqual(got["hookSpecificOutput"]["permissionDecision"], "deny")
        empty = io.StringIO()
        self.H.run_command_hook(self.adapter("shadow"), io.StringIO(json.dumps(PRE_INPUT)), empty)
        self.assertEqual(empty.getvalue(), "")                               # 허락이면 아무것도 쓰지 않는다

    def test_unknown_mode_is_refused(self):
        with self.assertRaises(ValueError):
            self.H.HookAdapter(lambda it: None, dc_id_of=lambda d: "x", policy="p@0", mode="audit")


if __name__ == "__main__":
    unittest.main()
