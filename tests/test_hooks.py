"""훅 어댑터(BD-120 (5) · BD-122 · BD-123 · BD-124 · CMD-K3) -- 기록된 transcript(`rlo/data/transcripts`)로 판정 길 전체를 돈다:
transcript -> Telemetry cc_jsonl -> Sensor from_l0 -> DC 문맥 -> guard -> 훅 응답. 공식 문서의 응답 꼴 그대로."""
import asyncio
import io
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from action.spec import ActionModel

from rlo import hooks
from rlo.example_hooks import data, hook_input, now_after

try:
    import llmsensor  # noqa: F401
    SENSOR = True
except ImportError:
    SENSOR = False
NEEDS_SENSOR = unittest.skipUnless(SENSOR, "훅 판정은 Sensor 가 필요하다 -- rlo-sdk[sensor] 로 깔면 돈다")

MODEL = ActionModel.from_dict(json.loads(data("cc_tools_model.json").read_text(encoding="utf-8")))
PRE_ALL = ("normal", "normal_no_current_use", "after_failure", "read_after_failure", "parallel", "first_call",
           "first_call_no_current_use", "earlier_pending")


class Base(unittest.TestCase):
    def adapter(self, name, mode, grants=("Bash",), **kw):
        self.records = []
        return hooks.guard_hooks(MODEL, mode=mode, grants=grants, clock=lambda: now_after(name),
                                 record=lambda k, d: self.records.append((k, d)), **kw)

    def run_pre(self, name, mode, **kw):
        return self.adapter(name, mode, **kw).handle(hook_input(name))

    def rule(self, out):
        return out["hookSpecificOutput"]["permissionDecisionReason"].split("(", 1)[1].split(")", 1)[0]

    def states(self, name, **kw):
        """그 훅 순간 판정이 본 DC 문맥의 상태 {키: (값, 유효성)}."""
        j = hooks.TranscriptJudge(MODEL, grants=("Bash",), clock=lambda: now_after(name), **kw)
        _, record, _ = j.view(hook_input(name))
        return {k: tuple(v) for k, v in record["core"]["states"].items()}


@NEEDS_SENSOR
class AgentToolCall(Base):
    """CMD-K3 끝난 기준(기본 목적 agent_tool_call, enforce, 기록된 transcript)."""

    def test_default_purpose(self):
        self.assertEqual(hooks.PURPOSE, "agent_tool_call")
        self.run_pre("normal", "enforce")
        self.assertTrue(self.records[0][1]["complete"])

    def test_normal_after_failure_first_call_are_empty(self):
        for name in ("normal", "normal_no_current_use", "after_failure", "first_call", "first_call_no_current_use"):
            with self.subTest(name):
                self.assertEqual(self.run_pre(name, "enforce"), {})
                self.assertEqual(self.records[0][1]["result"]["verdict"], "ALLOW")

    def test_failure_value_alone_does_not_block(self):
        """BD-123 B2: 실패 뒤 execution_health 는 UNRESOLVED_FAILURES -- 쓸 수 있는 값이라 D 가 아니다."""
        self.assertEqual(self.states("after_failure")["agent.execution_health"], ("UNRESOLVED_FAILURES", "INFERRED"))
        self.assertEqual(self.run_pre("after_failure", "enforce"), {})

    def test_unknown_health_closes_with_d(self):
        for name in ("parallel", "earlier_pending"):
            with self.subTest(name):
                out = self.run_pre(name, "enforce")
                self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")
                self.assertEqual(self.rule(out), "D")
                self.assertIn("agent.execution_health", self.records[0][1]["missing_required"])

    def test_non_risky_tool_is_outside_d(self):
        self.assertEqual(self.run_pre("read_after_failure", "enforce"), {})

    def test_shadow_never_blocks(self):
        for name in PRE_ALL:
            with self.subTest(name):
                self.assertEqual(self.run_pre(name, "shadow"), {})
                self.assertEqual(self.records[0][0], "guard")

    def test_no_grant_is_a7(self):
        self.assertEqual(self.rule(self.run_pre("normal", "enforce", grants=())), "A7")

    def test_unknown_argument_is_a4(self):
        a = self.adapter("normal", "enforce")
        out = a.handle(dict(hook_input("normal"), tool_input={"command": "make", "dangerously": True}))
        self.assertEqual(self.rule(out), "A4")

    def test_tool_outside_the_model_is_a1(self):
        a = self.adapter("normal", "enforce")
        out = a.handle(dict(hook_input("normal"), tool_name="WebFetch", tool_input={"url": "x"}))
        self.assertEqual(self.rule(out), "A1")

    def test_intent_carries_the_context_it_was_judged_on(self):
        self.run_pre("normal", "enforce")
        rec = self.records[0][1]
        self.assertTrue(rec["dc_id"].startswith("dc-"))
        self.assertEqual(rec["tool_use_id"], "tu2")

    def test_execution_control_can_still_be_plugged(self):
        """purpose= 는 그대로 받는다. execution_control 은 transcript 로 완전해질 수 없어(BD-123 B1) 정상도 D 다."""
        out = self.run_pre("normal", "enforce", purpose="execution_control")
        self.assertEqual(self.rule(out), "D")
        self.assertIn("runtime.rate_limit_state", self.records[0][1]["missing_required"])


@NEEDS_SENSOR
class CurrentCallExcluded(Base):
    """BD-124: 판정하려는 호출(훅 입력 tool_use_id)은 그 호출의 근거가 아니다 -- 그 tool.start · tool.end 를 뺀다."""

    def events(self, name):
        from telemetry.collect import from_cc_jsonl
        return from_cc_jsonl(str(data(f"transcripts/{name}.jsonl")), "cc:x")

    def tools(self, events):
        return [(e["type"], e["data"].get("tool_use_id"), e["data"]["tool_index"]) for e in events
                if e["type"].startswith("tool.")]

    def test_drops_only_that_call(self):
        ev = self.events("parallel")
        self.assertEqual(self.tools(hooks.without_call(ev, "tu2")), [("tool.start", "tu1", 0)])     # 나란히 부른 tu1 은 남는다
        ev = self.events("after_failure")
        self.assertEqual(self.tools(hooks.without_call(ev, "tu1")), [("tool.start", "tu2", 1)])     # start 와 그 end 를 함께
        self.assertEqual(hooks.without_call(ev, None), ev)
        self.assertEqual(hooks.without_call(ev, "tu-none"), ev)

    def test_first_call_is_the_same_with_or_without_the_current_line(self):
        a = self.states("first_call")["agent.execution_health"]
        b = self.states("first_call_no_current_use")["agent.execution_health"]
        self.assertEqual(a, b)
        self.assertEqual(a, ("NO_TOOL_RUN_YET", "INFERRED"))

    def test_stop_keeps_every_call(self):
        got = []
        a = self.adapter("first_call", "enforce", collected=lambda ev, run, rs: got.append(rs))
        a.handle(dict(hook_input("first_call"), hook_event_name="Stop"))
        (rs,) = got
        self.assertEqual(rs.read(f"agent:{rs.runs[0]}", "execution_health")["status"], "UNKNOWN")   # 결과를 못 본 호출이 남는다


@NEEDS_SENSOR
class Collecting(Base):
    def test_reads_the_transcript_again_on_every_pre_tool_use(self):
        d = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d)
        path = d / "t.jsonl"
        shutil.copy(str(data("transcripts/normal_no_current_use.jsonl")), path)
        a = self.adapter("after_failure", "enforce")
        inp = dict(hook_input("normal_no_current_use"), transcript_path=str(path))
        a.handle(inp)
        failed = data("transcripts/after_failure.jsonl").read_text(encoding="utf-8")
        path.write_text(failed, encoding="utf-8")                      # 그 사이 transcript 가 자랐다(실패가 들어옴)
        a.handle(inp)
        first, second = (r for k, r in self.records if k == "guard")
        self.assertNotEqual(first["dc_id"], second["dc_id"])

    def test_post_hooks_only_observe_and_never_collect(self):
        seen = []
        a = self.adapter("normal", "enforce", observe=seen.append)
        with mock.patch.object(hooks.TranscriptJudge, "collect", side_effect=AssertionError("거두면 안 된다")):
            post = dict(hook_input("normal"), hook_event_name="PostToolUse", tool_response={"stdout": "ok"})
            fail = dict(hook_input("normal"), hook_event_name="PostToolUseFailure", error="Exit code 1")
            self.assertEqual(a.handle(post), {})
            self.assertEqual(a.handle(fail), {})
        self.assertEqual([d["hook_event_name"] for d in seen], ["PostToolUse", "PostToolUseFailure"])
        self.assertEqual(self.records, [])                                  # 실행 뒤 훅은 판정하지 않는다

    def test_stop_and_session_end_collect_without_blocking(self):
        got = []
        a = self.adapter("ended", "enforce", collected=lambda ev, run, rs: got.append((ev, run, rs)))
        for ev in ("Stop", "SessionEnd"):
            self.assertEqual(a.handle(dict(hook_input("ended"), hook_event_name=ev)), {})
        self.assertEqual([g[0] for g in got], ["Stop", "SessionEnd"])
        ev, run, rs = got[0]
        self.assertEqual(run, "cc:00000000-0000-4000-8000-000000000001")
        self.assertEqual(rs.runs, [run])
        self.assertEqual(rs.read(f"agent:{run}", "execution_health")["value"], "NO_FAILURE_OBSERVED")
        self.assertEqual(rs.read(f"task:{run}", "completion_state")["value"], "RUNNING")   # Stop 은 차례의 끝이지 실행의 끝이 아니다

    def test_collect_error_never_blocks_the_end(self):
        a = self.adapter("ended", "enforce")
        self.assertEqual(a.handle(dict(hook_input("ended"), transcript_path="/nonexistent/t.jsonl")), {})
        self.assertEqual(self.records, [("collect_error", {"event": "Stop", "exception": "FileNotFoundError"})])


@NEEDS_SENSOR
class Closing(Base):
    def test_missing_transcript_closes_only_in_enforce(self):
        bad = dict(hook_input("normal"), transcript_path="/nonexistent/t.jsonl")
        out = self.adapter("normal", "enforce").handle(bad)
        self.assertEqual(out["hookSpecificOutput"]["permissionDecisionReason"], "guard error: FileNotFoundError")
        self.assertEqual(self.adapter("normal", "shadow").handle(bad), {})

    def test_judge_error_message_does_not_leak(self):
        def boom(d, mode):
            raise RuntimeError("secret detail")
        out = hooks.HookAdapter(boom, mode="enforce").handle(hook_input("normal"))
        self.assertEqual(out["hookSpecificOutput"]["permissionDecisionReason"], "guard error: RuntimeError")
        self.assertEqual(hooks.HookAdapter(boom, mode="shadow").handle(hook_input("normal")), {})

    def test_never_emits_allow(self):
        outs = [self.run_pre(n, m, **kw) for n in PRE_ALL for m in ("shadow", "enforce")
                for kw in ({}, {"purpose": "execution_control"})]
        self.assertIn({}, outs)
        self.assertFalse(any("allow" in json.dumps(o) for o in outs))

    def test_unknown_mode_is_refused(self):
        with self.assertRaises(ValueError):
            hooks.HookAdapter(lambda d, m: None, mode="audit")

    def test_sdk_callback(self):
        got = asyncio.run(self.adapter("parallel", "enforce").callback(hook_input("parallel"), "tu2", None))
        self.assertEqual(got["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertEqual(asyncio.run(self.adapter("normal", "enforce").callback(hook_input("normal"), "tu2", None)), {})


@NEEDS_SENSOR
class CommandHook(unittest.TestCase):
    """`python -m rlo.hooks` -- Claude Code 명령 훅 길(표준입력 → 표준출력, 종료 0)."""

    def cli(self, inp, *args, model=None):
        argv = [sys.executable, "-m", "rlo.hooks", "--model", model or str(data("cc_tools_model.json")), *args]
        env = dict(os.environ)
        return subprocess.run(argv, input=json.dumps(inp), capture_output=True, text=True, env=env)

    def test_enforce_denies_and_shadow_is_silent(self):
        now = str(now_after("parallel"))
        p = self.cli(hook_input("parallel"), "--mode", "enforce", "--grant", "Bash", "--now-ms", now)
        self.assertEqual(p.returncode, 0)
        self.assertEqual(json.loads(p.stdout)["hookSpecificOutput"]["permissionDecision"], "deny")
        p = self.cli(hook_input("parallel"), "--mode", "shadow", "--grant", "Bash", "--now-ms", now)
        self.assertEqual((p.returncode, p.stdout), (0, ""))
        p = self.cli(hook_input("normal"), "--mode", "enforce", "--grant", "Bash", "--now-ms", str(now_after("normal")))
        self.assertEqual((p.returncode, p.stdout), (0, ""))                  # 정상은 enforce 에서도 {} -- 아무것도 쓰지 않는다

    def test_bad_config_closes_pre_tool_use_in_enforce_only(self):
        p = self.cli(hook_input("normal"), "--mode", "enforce", model="/nonexistent/model.json")
        self.assertEqual(p.returncode, 0)
        self.assertEqual(json.loads(p.stdout)["hookSpecificOutput"]["permissionDecisionReason"],
                         "rlo hook config error: FileNotFoundError")
        p = self.cli(hook_input("normal"), "--mode", "shadow", model="/nonexistent/model.json")
        self.assertEqual((p.returncode, p.stdout), (0, ""))
        p = self.cli(dict(hook_input("ended")), "--mode", "enforce", model="/nonexistent/model.json")
        self.assertEqual((p.returncode, p.stdout), (0, ""))                  # Stop 은 막지 않는다

    def test_record_file(self):
        d = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d)
        self.cli(hook_input("parallel"), "--mode", "shadow", "--grant", "Bash", "--now-ms", str(now_after("parallel")),
                 "--record", str(d / "r.jsonl"))
        (row,) = [json.loads(x) for x in (d / "r.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual((row["kind"], row["result"]["verdict"], row["result"]["rule"]), ("guard", "DENY", "D"))
        self.assertNotIn("build", json.dumps(row))                        # 도구 입력 평문(rm -rf build)은 기록하지 않는다

    def test_example_main_is_green(self):
        from rlo import example_hooks
        with mock.patch("builtins.print"):
            self.assertEqual(example_hooks.main(), 0)



class WithoutSensor(unittest.TestCase):
    """Sensor 없이 깔린 곳(rlo-sdk 기본): 훅은 서지 않고, 명령 훅은 enforce 의 PreToolUse 를 막는다(닫는 쪽)."""

    def test_judge_refuses_to_stand(self):
        with mock.patch.dict(sys.modules, {"llmsensor": None}):
            with self.assertRaises(ImportError) as e:
                hooks.guard_hooks(MODEL, mode="enforce")
        self.assertIn("rlo-sdk[sensor]", str(e.exception))

    def test_command_hook_closes_in_enforce(self):
        out = io.StringIO()
        with mock.patch.dict(sys.modules, {"llmsensor": None}):
            hooks.main(["--model", str(data("cc_tools_model.json")), "--mode", "enforce"],
                       stdin=io.StringIO(json.dumps(hook_input("normal"))), stdout=out)
        self.assertEqual(json.loads(out.getvalue())["hookSpecificOutput"]["permissionDecisionReason"],
                         "rlo hook config error: ImportError")


if __name__ == "__main__":
    unittest.main()
