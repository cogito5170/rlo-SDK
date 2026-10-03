"""턴 안 ReAct(CMD-K11) -- 거부마다 닫힌 대안표의 대안 하나 · 같은 거부 세 번째는 올림 · 대안은 허가를 넓히지 않는다."""
import itertools
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone

from action.spec import ActionModel

from rlo import hooks
from rlo import react as R
from rlo.example_hooks import data, hook_input, now_after

try:
    import llmsensor  # noqa: F401
    SENSOR = True
except ImportError:
    SENSOR = False
NEEDS_SENSOR = unittest.skipUnless(SENSOR, "훅 판정은 Sensor 가 필요하다 -- rlo-sdk[sensor] 로 깔면 돈다")

BASE = json.loads(data("cc_tools_model.json").read_text(encoding="utf-8"))
ISSUE_READ = "mcp__github__issue_read"                 # W1 의 통로 읽기(read)
SEND = "mcp__claude-code-remote__send_message"         # external -- 허가가 있어야 한다


def spec(name, risk, params=None):
    return {"schema": "action-spec/1", "name": name, "version": "1", "target_model": None, "params": params or {},
            "preconditions": [], "risk": risk, "postcondition": [], "window_ms": None, "description": name}


MODEL_D = dict(BASE, specs=[*BASE["specs"], spec(ISSUE_READ, "read", {"issue_number": {"type": "number"}}),
                            spec(SEND, "external", {"message": {"type": "string"}})])
MODEL = ActionModel.from_dict(MODEL_D)
SUBS = {"ReadNotifications": [ISSUE_READ]}
KEYS = {"kind", "rule", "cause", "tool", "attempt", "of", "escalate"}
IDLE_MS = 11 * 60_000


def check(tc, obj):
    """react 객체는 닫힌 꼴이다: 알려진 칸 · 알려진 종류 · 라벨 값 · 자유 글 없음."""
    tc.assertLessEqual(set(obj), KEYS)
    tc.assertIn(obj["kind"], R.KINDS)
    for k in ("kind", "rule", "cause") + (("tool",) if "tool" in obj else ()):
        tc.assertRegex(obj[k], R.LABEL)
    tc.assertEqual(obj["of"], R.MAX_ATTEMPTS)
    tc.assertIsInstance(obj["attempt"], int)
    tc.assertIs(obj["escalate"], obj["kind"] == "report")
    tc.assertEqual("tool" in obj, obj["kind"] == "use_tool")


def react_of(out):
    reason = out["hookSpecificOutput"]["permissionDecisionReason"]
    last = reason.splitlines()[-1]
    assert last.startswith(R.PREFIX), reason
    return json.loads(last[len(R.PREFIX):])


class Table(unittest.TestCase):
    """표 자체: 닫혀 있고, 같은 거부 세 번째는 report · escalate."""

    def test_table_is_closed(self):
        self.assertLessEqual(set(R.TABLE.values()), set(R.KINDS))
        for (rule, cause) in R.TABLE:
            self.assertRegex(rule, R.LABEL)
            self.assertRegex(cause, R.LABEL)
        self.assertEqual(R.react("A5", "other")["kind"], "none")             # 표에 없는 짝은 none

    def test_third_identical_deny_escalates(self):
        self.assertEqual([R.react("D", "stale", prior=n)["kind"] for n in range(4)],
                         ["refresh_read", "refresh_read", "report", "report"])
        third = R.react("A1", "has_substitute", ISSUE_READ, prior=2)
        self.assertEqual((third["kind"], third["attempt"], third["escalate"]), ("report", 3, True))
        self.assertNotIn("tool", third)
        for n in range(4):
            check(self, R.react("A1", "has_substitute", ISSUE_READ, prior=n))

    def test_line_is_one_machine_readable_line(self):
        ln = R.line(R.react("A7", "not_granted"))
        self.assertEqual(ln.count("\n"), 1)
        self.assertTrue(ln.startswith("\n" + R.PREFIX))
        self.assertEqual(R.react_of(ln), R.react("A7", "not_granted"))


class Substitutes(unittest.TestCase):
    """대체표는 운영자 몫이고 모형 파일 옆 칸이다. action-model/1 계약은 바꾸지 않는다."""

    def test_split_keeps_action_model_1_intact(self):
        rest, subs = R.split_model(dict(MODEL_D, substitutes=SUBS))
        self.assertEqual((rest, subs), (MODEL_D, SUBS))
        ActionModel.from_dict(rest)
        with self.assertRaises(Exception):                                  # action-model/1 은 모르는 칸을 받지 않는다
            ActionModel.from_dict(dict(MODEL_D, substitutes=SUBS))
        self.assertEqual(R.split_model(MODEL_D), (MODEL_D, {}))

    def test_bad_substitutes_are_refused(self):
        for bad in ([ISSUE_READ], {"ReadNotifications": ISSUE_READ}, {"ReadNotifications": ["has space"]}, {"": []}):
            with self.subTest(bad=bad), self.assertRaises(R.SubstitutesError):
                R.split_model(dict(MODEL_D, substitutes=bad))

    def test_never_names_a_tool_outside_the_model_or_ungranted(self):
        """어떤 대체표 · 허가 조합에서도 use_tool 은 모형 안 · (external 이면) 허가된 도구만."""
        from guard import GuardModel
        pool = [ISSUE_READ, SEND, "WebFetch", "Bash", "ReadNotifications"]
        for grants in ((), ("Bash",), (SEND,), ("Bash", SEND)):
            g = GuardModel.from_action_model(MODEL, grants)
            for k in range(1, 4):
                for subs in itertools.permutations(pool, k):
                    got = R.eligible("ReadNotifications", g, {"ReadNotifications": list(subs)})
                    for t in got:
                        self.assertIn(t, g.specs)
                        self.assertTrue(g.specs[t].risk not in ("external", "irreversible") or t in grants, (t, grants))
                        self.assertNotEqual(t, "ReadNotifications")
                    self.assertEqual(g.grants, frozenset(grants))                # 읽기만 한다

    def test_install_hook_accepts_a_model_with_substitutes(self):
        from rlo import install
        d = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d)
        (d / "m.json").write_text(json.dumps(dict(MODEL_D, substitutes=SUBS)), encoding="utf-8")
        self.assertEqual(install.main("install-hook", ["--settings", str(d / "s.json"), "--model", str(d / "m.json")]), 0)
        (d / "bad.json").write_text(json.dumps(dict(MODEL_D, substitutes=[1])), encoding="utf-8")
        self.assertEqual(install.main("install-hook", ["--settings", str(d / "t.json"), "--model", str(d / "bad.json")]), 1)


@NEEDS_SENSOR
class W1Cases(unittest.TestCase):
    """CMD-K11 D1 -- 오늘 W1 의 네 사례를 재생한다."""

    def setUp(self):
        self.dir = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir)
        self.rows = []

    def adapter(self, mode="enforce", grants=("Bash",), subs=SUBS, now=None):
        return hooks.guard_hooks(MODEL, mode=mode, grants=grants, substitutes=subs,
                                 clock=lambda: now, record=lambda k, d: self.rows.append((k, d)))

    def inp(self, lines, tool, args, tu):
        p = self.dir / f"{tu}.jsonl"
        p.write_text("".join((x if isinstance(x, str) else json.dumps(x)) + "\n" for x in lines), encoding="utf-8")
        return {"session_id": "00000000-0000-4000-8000-000000000001", "transcript_path": str(p), "cwd": "/work",
                "permission_mode": "default", "hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": args,
                "tool_use_id": tu}

    def base(self):
        return data("transcripts/normal_no_current_use.jsonl").read_text(encoding="utf-8").splitlines()

    def iso(self, ms):
        return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")

    def use(self, tu, tool, args, ms, mid):
        return {"type": "assistant", "sessionId": "00000000-0000-4000-8000-000000000001", "uuid": f"a-{tu}",
                "timestamp": self.iso(ms), "message": {"id": mid, "model": "claude-x", "role": "assistant",
                "stop_reason": "tool_use", "usage": {"input_tokens": 1, "output_tokens": 1},
                "content": [{"type": "tool_use", "id": tu, "name": tool, "input": args}]}}

    def result(self, tu, text, ms, error):
        return {"type": "user", "sessionId": "00000000-0000-4000-8000-000000000001", "uuid": f"r-{tu}",
                "timestamp": self.iso(ms), "toolUseResult": {},
                "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tu,
                                                         "content": text, "is_error": error}]}}

    def test_read_notifications_a1_uses_issue_read(self):
        now = now_after("normal_no_current_use")
        out = self.adapter(now=now).handle(self.inp(self.base(), "ReadNotifications", {}, "tu-n"))
        r = react_of(out)
        check(self, r)
        self.assertEqual((r["kind"], r["tool"], r["rule"], r["attempt"], r["escalate"]),
                         ("use_tool", ISSUE_READ, "A1", 1, False))
        self.assertEqual(self.rows[-1][1]["react"], r)                       # 기록 줄에 같은 객체
        follow = self.adapter(now=now).handle(self.inp(self.base(), ISSUE_READ, {"issue_number": 1}, "tu-i"))
        self.assertEqual(follow, {})                                         # 대안을 따르면 지난다

    def test_a1_without_substitute_reports(self):
        out = self.adapter(subs={}, now=now_after("normal")).handle(hook_input("normal") | {"tool_name": "WebFetch",
                                                                                           "tool_input": {}})
        r = react_of(out)
        check(self, r)
        self.assertEqual((r["kind"], r["cause"], r["escalate"]), ("report", "no_substitute", True))

    def test_stale_only_d_refresh_read_then_external_passes(self):
        t0 = now_after("normal_no_current_use") + IDLE_MS
        out = self.adapter(now=t0 - 500).handle(self.inp(self.base(), "Bash", {"command": "make"}, "tu-b0"))
        r = react_of(out)
        check(self, r)
        self.assertEqual((r["kind"], r["rule"], r["cause"]), ("refresh_read", "D", "stale"))
        self.assertIn(hooks.STALE_HINT, out["hookSpecificOutput"]["permissionDecisionReason"])    # K10 안내도 그대로
        read = self.use("tu-r", "Read", {"file_path": "/work/a"}, t0, "m9")
        self.assertEqual(self.adapter(now=t0 + 500).handle(self.inp([*self.base(), read], "Read",
                                                                    {"file_path": "/work/a"}, "tu-r")), {})
        done = self.result("tu-r", [{"type": "text", "text": "x"}], t0 + 1000, False)
        self.assertEqual(self.adapter(now=t0 + 2000).handle(self.inp([*self.base(), read, done], "Bash",
                                                                     {"command": "make"}, "tu-b1")), {})

    def test_a7_reports_and_escalates(self):
        out = self.adapter(grants=(), now=now_after("normal")).handle(hook_input("normal"))
        r = react_of(out)
        check(self, r)
        self.assertEqual((r["kind"], r["rule"], r["escalate"]), ("report", "A7", True))

    def test_a7_wins_over_stale_d(self):
        """낡은 D 와 A7 이 함께 걸리면(guard 의 rule 은 D) 읽기로는 풀리지 않는다 -- report."""
        out = self.adapter(grants=(), now=now_after("normal") + IDLE_MS).handle(hook_input("normal"))
        self.assertTrue(out["hookSpecificOutput"]["permissionDecisionReason"].startswith("guard DENY(D)"))
        r = react_of(out)
        self.assertEqual((r["kind"], r["rule"], r["escalate"]), ("report", "A7", True))

    def test_malformed_input_reports(self):
        out = self.adapter(now=0).handle({"hook_event_name": "PreToolUse"})
        r = react_of(out)
        check(self, r)
        self.assertEqual((r["kind"], r["rule"], r["cause"], r["escalate"]), ("report", "input", "malformed_input", True))
        p = subprocess.run([sys.executable, "-m", "rlo.hooks", "--model", str(data("cc_tools_model.json")),
                            "--mode", "enforce"], input="", capture_output=True, text=True)
        self.assertEqual((p.returncode, react_of(json.loads(p.stdout))["kind"]), (0, "report"))

    def test_other_rows_of_the_table(self):
        now = now_after("parallel")
        r = react_of(self.adapter(now=now).handle(hook_input("parallel")))
        self.assertEqual((r["kind"], r["cause"]), ("wait_previous", "unavailable"))   # 앞 호출 결과 없음 · 나란히
        edit = {"file_path": "/a", "old_string": "x", "new_string": "y", "dry_run": True}
        r = react_of(self.adapter(now=now_after("normal")).handle(hook_input("normal") | {"tool_name": "Edit",
                                                                                           "tool_input": edit}))
        self.assertEqual((r["kind"], r["cause"]), ("drop_unknown_args", "unknown_args"))
        r = react_of(self.adapter(now=now_after("normal")).handle(hook_input("normal") | {"tool_name": "Read",
                                                                                           "tool_input": {}}))
        self.assertEqual((r["kind"], r["cause"]), ("none", "bad_args"))      # 필수 인자 없음 -- 표에 대안 없음
        r = react_of(self.adapter(now=0).handle(dict(hook_input("normal"), transcript_path="/nonexistent/t.jsonl")))
        self.assertEqual((r["kind"], r["rule"]), ("report", "E"))

    def test_third_identical_deny_in_the_transcript_escalates(self):
        """같은 도구의 같은 거부 둘이 transcript 에 있으면(훅이 실제로 낸 까닭 그대로) 세 번째는 report · escalate.
        그 도구가 거부 아닌 결과를 받으면 다시 1 부터."""
        now = now_after("normal_no_current_use")
        lines = self.base()
        kinds = []
        for i in range(4):
            out = self.adapter(now=now).handle(self.inp(lines, "ReadNotifications", {}, f"tu-n{i}"))
            r = react_of(out)
            check(self, r)
            kinds.append((r["kind"], r["attempt"], r["escalate"]))
            lines = [*lines, self.use(f"tu-n{i}", "ReadNotifications", {}, now - 5000 + i, f"mn{i}"),
                     self.result(f"tu-n{i}", out["hookSpecificOutput"]["permissionDecisionReason"], now - 4000 + i, True)]
        self.assertEqual(kinds, [("use_tool", 1, False), ("use_tool", 2, False), ("report", 3, True), ("report", 4, True)])
        lines = [*lines, self.use("tu-ok", "ReadNotifications", {}, now - 3000, "mok"),
                 self.result("tu-ok", "fine", now - 2000, False)]
        r = react_of(self.adapter(now=now).handle(self.inp(lines, "ReadNotifications", {}, "tu-n9")))
        self.assertEqual((r["kind"], r["attempt"]), ("use_tool", 1))

    def test_shadow_records_and_prints_nothing(self):
        out = self.adapter(mode="shadow", grants=(), now=now_after("normal")).handle(hook_input("normal"))
        self.assertEqual(out, {})
        (kind, row), = self.rows
        self.assertEqual((kind, row["result"]["verdict"], row["react"]["kind"]), ("guard", "DENY", "report"))
        check(self, row["react"])

    def test_decisions_are_unchanged(self):
        """대안은 판정을 바꾸지 않는다: 같은 입력의 판정(규칙 · verdict)이 대체표 · 되풀이와 무관하다."""
        for name in ("normal", "parallel", "after_failure", "earlier_pending"):
            for grants in (("Bash",), ()):
                a = self.adapter(grants=grants, subs={}, now=now_after(name)).handle(hook_input(name))
                b = self.adapter(grants=grants, subs={"Bash": [ISSUE_READ]}, now=now_after(name)).handle(hook_input(name))
                self.assertEqual(bool(a), bool(b))
                if a:
                    head = lambda o: o["hookSpecificOutput"]["permissionDecisionReason"].split("\n-- react: ")[0]
                    self.assertEqual(head(a), head(b))

    def test_command_hook_reads_substitutes_from_the_model_file(self):
        (self.dir / "m.json").write_text(json.dumps(dict(MODEL_D, substitutes=SUBS)), encoding="utf-8")
        inp = self.inp(self.base(), "ReadNotifications", {}, "tu-c")
        argv = [sys.executable, "-m", "rlo.hooks", "--model", str(self.dir / "m.json"), "--mode", "enforce",
                "--now-ms", str(now_after("normal_no_current_use")), "--record", str(self.dir / "r.jsonl")]
        p = subprocess.run(argv, input=json.dumps(inp), capture_output=True, text=True)
        r = react_of(json.loads(p.stdout))
        self.assertEqual((p.returncode, r["kind"], r["tool"]), (0, "use_tool", ISSUE_READ))
        (row,) = [json.loads(x) for x in (self.dir / "r.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(row["react"], r)
        (self.dir / "bad.json").write_text(json.dumps(dict(MODEL_D, substitutes={"x": "y"})), encoding="utf-8")
        p = subprocess.run([*argv[:4], str(self.dir / "bad.json"), *argv[5:]], input=json.dumps(inp),
                           capture_output=True, text=True)
        out = json.loads(p.stdout)
        self.assertTrue(out["hookSpecificOutput"]["permissionDecisionReason"].startswith(
            "rlo hook config error: SubstitutesError"))
        self.assertEqual(react_of(out)["kind"], "report")


if __name__ == "__main__":
    unittest.main()
