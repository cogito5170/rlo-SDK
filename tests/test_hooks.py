"""훅 어댑터(BD-120 (5) · BD-122 · CMD-K2) -- 기록된 transcript(`rlo/data/transcripts`)로 판정 길 전체를 돈다:
transcript -> Telemetry cc_jsonl -> Sensor from_l0 -> DC 문맥 -> guard -> 훅 응답. 공식 문서의 응답 꼴 그대로."""
import asyncio
import dataclasses
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
PRE_ALL = ("normal", "normal_no_current_use", "after_failure", "read_after_failure", "parallel", "first_call")


def satisfiable_purpose():
    """시험용 목적: execution_control 에서 transcript 로 알 수 없는 둘(rate_limit_state · progress_state)만 선택으로.
    판정 길이 맞게 이어졌는지 보려는 것이다 -- 이 목적을 SDK 기본으로 삼자는 것이 아니다(그것은 baseline 판단)."""
    from dc.purpose import PURPOSES
    P = PURPOSES["execution_control"]
    refs = tuple(dataclasses.replace(r, required=False) if r.name in ("rate_limit_state", "progress_state") else r
                 for r in P.refs)
    return dataclasses.replace(P, version="rlo-test-hook-1", refs=refs)


class Base(unittest.TestCase):
    def adapter(self, name, mode, grants=("Bash",), **kw):
        self.records = []
        return hooks.guard_hooks(MODEL, mode=mode, grants=grants, clock=lambda: now_after(name),
                                 record=lambda k, d: self.records.append((k, d)), **kw)

    def run_pre(self, name, mode, **kw):
        return self.adapter(name, mode, **kw).handle(hook_input(name))

    def rule(self, out):
        return out["hookSpecificOutput"]["permissionDecisionReason"].split("(", 1)[1].split(")", 1)[0]


@NEEDS_SENSOR
class DefaultPurposeMeasured(Base):
    """기본 목적 execution_control 을 그대로 이었을 때 **잰 그대로**: Claude Code transcript 에는 요금 한도 사용률과 정체 문턱이
    없어 필수 상태 둘이 늘 UNKNOWN 이다 -> 문맥이 늘 불완전 -> enforce 에서 위험 도구(Bash, external)는 늘 D 로 막힌다."""

    def test_enforce_denies_every_risky_call_with_d(self):
        for name in ("normal", "normal_no_current_use", "after_failure", "parallel", "first_call"):
            with self.subTest(name):
                out = self.run_pre(name, "enforce")
                self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")
                self.assertEqual(self.rule(out), "D")
                (kind, rec), = self.records
                self.assertFalse(rec["complete"])
                self.assertIn("runtime.rate_limit_state", rec["missing_required"])

    def test_non_risky_tool_passes_even_when_incomplete(self):
        self.assertEqual(self.run_pre("read_after_failure", "enforce"), {})                 # Read 는 read 등급 -- D 밖
        self.assertEqual(self.records[0][1]["result"]["verdict"], "ALLOW")

    def test_shadow_never_blocks(self):
        for name in PRE_ALL:
            with self.subTest(name):
                self.assertEqual(self.run_pre(name, "shadow"), {})
                self.assertEqual(self.records[0][0], "guard")


@NEEDS_SENSOR
class SatisfiablePurpose(Base):
    """필수 상태를 transcript 로 알 수 있는 목적을 꽂으면(`purpose=`) 판정 길이 상태대로 가른다."""

    def pre(self, name, mode="enforce", **kw):
        return self.run_pre(name, mode, purpose=satisfiable_purpose(), **kw)

    def test_normal_is_empty(self):
        self.assertEqual(self.pre("normal"), {})
        self.assertEqual(self.pre("normal_no_current_use"), {})
        self.assertTrue(self.records[0][1]["complete"])

    def test_parallel_call_without_earlier_result_closes_with_d(self):
        out = self.pre("parallel")
        self.assertEqual(self.rule(out), "D")
        self.assertIn("agent.execution_health", self.records[0][1]["missing_required"])

    def test_first_call_of_a_session_closes_with_d(self):
        out = self.pre("first_call")
        self.assertEqual(self.rule(out), "D")

    def test_after_failure_is_not_blocked_by_d(self):
        """잰 것: 실패 뒤 execution_health 는 UNRESOLVED_FAILURES 로 **쓸 수 있는 값**이다. D 는 완전성 · 낡음만 보고 값은 보지
        않으므로 막지 않는다 -- K2 의 끝난 기준("실패 차례 뒤 위험 도구는 D")과 다르다. 보고의 Request 에 올렸다."""
        self.assertEqual(self.pre("after_failure"), {})
        self.assertTrue(self.records[0][1]["complete"])

    def test_no_grant_is_a7(self):
        self.assertEqual(self.rule(self.pre("normal", grants=())), "A7")

    def test_unknown_argument_is_a4(self):
        a = self.adapter("normal", "enforce", purpose=satisfiable_purpose())
        out = a.handle(dict(hook_input("normal"), tool_input={"command": "make", "dangerously": True}))
        self.assertEqual(self.rule(out), "A4")

    def test_tool_outside_the_model_is_a1(self):
        a = self.adapter("normal", "enforce", purpose=satisfiable_purpose())
        out = a.handle(dict(hook_input("normal"), tool_name="WebFetch", tool_input={"url": "x"}))
        self.assertEqual(self.rule(out), "A1")

    def test_intent_carries_the_context_it_was_judged_on(self):
        self.pre("normal")
        rec = self.records[0][1]
        self.assertTrue(rec["dc_id"].startswith("dc-"))
        self.assertEqual(rec["tool_use_id"], "tu2")


@NEEDS_SENSOR
class Collecting(Base):
    def test_reads_the_transcript_again_on_every_pre_tool_use(self):
        d = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d)
        path = d / "t.jsonl"
        shutil.copy(str(data("transcripts/normal_no_current_use.jsonl")), path)
        a = self.adapter("after_failure", "enforce", purpose=satisfiable_purpose())
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
                for kw in ({}, {"purpose": satisfiable_purpose()})]
        self.assertIn({}, outs)
        self.assertFalse(any("allow" in json.dumps(o) for o in outs))

    def test_unknown_mode_is_refused(self):
        with self.assertRaises(ValueError):
            hooks.HookAdapter(lambda d, m: None, mode="audit")

    def test_sdk_callback(self):
        a = self.adapter("normal", "enforce")
        got = asyncio.run(a.callback(hook_input("normal"), "tu2", None))
        self.assertEqual(got["hookSpecificOutput"]["permissionDecision"], "deny")


@NEEDS_SENSOR
class CommandHook(unittest.TestCase):
    """`python -m rlo.hooks` -- Claude Code 명령 훅 길(표준입력 → 표준출력, 종료 0)."""

    def cli(self, inp, *args, model=None):
        argv = [sys.executable, "-m", "rlo.hooks", "--model", model or str(data("cc_tools_model.json")), *args]
        env = dict(os.environ)
        return subprocess.run(argv, input=json.dumps(inp), capture_output=True, text=True, env=env)

    def test_enforce_denies_and_shadow_is_silent(self):
        now = str(now_after("normal"))
        p = self.cli(hook_input("normal"), "--mode", "enforce", "--grant", "Bash", "--now-ms", now)
        self.assertEqual(p.returncode, 0)
        self.assertEqual(json.loads(p.stdout)["hookSpecificOutput"]["permissionDecision"], "deny")
        p = self.cli(hook_input("normal"), "--mode", "shadow", "--grant", "Bash", "--now-ms", now)
        self.assertEqual((p.returncode, p.stdout), (0, ""))

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
        self.cli(hook_input("normal"), "--mode", "shadow", "--grant", "Bash", "--now-ms", str(now_after("normal")),
                 "--record", str(d / "r.jsonl"))
        (row,) = [json.loads(x) for x in (d / "r.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual((row["kind"], row["result"]["verdict"]), ("guard", "DENY"))
        self.assertNotIn("make", json.dumps(row))                         # 도구 입력 평문은 기록하지 않는다

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
