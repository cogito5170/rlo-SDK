"""D 는 세션을 통로에서 끊지 않는다(CMD-K13) -- 낡음뿐인 D 에서 고정된 통로 호출은 지나가고, 올림은 부를 수 있는 통로를 가리킨다."""
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

COMMENT, READ_ISSUE = "mcp__github__add_issue_comment", "mcp__github__issue_read"
PIN = {"owner": "cogito5170", "repo": "amp", "issue_number": 1}


def spec(name, risk, params):
    return {"schema": "action-spec/1", "name": name, "version": "1", "target_model": None, "params": params,
            "preconditions": [], "risk": risk, "postcondition": [], "window_ms": None, "description": name}


BASE = json.loads(data("cc_tools_model.json").read_text(encoding="utf-8"))
ISSUE = {"owner": {"type": "string"}, "repo": {"type": "string"}, "issue_number": {"type": "number"}}
MODEL_D = dict(BASE, specs=[*BASE["specs"], spec(COMMENT, "external", dict(ISSUE, body={"type": "string"})),
                            spec(READ_ISSUE, "read", dict(ISSUE, method={"type": "string"}))])
CHANNELS = [{"tool": COMMENT, "args": PIN}, {"tool": READ_ISSUE, "args": PIN, "use": "read"},
            {"tool": "Bash", "argv_prefix": ["ga", "mail"]}]
MODEL = ActionModel.from_dict(MODEL_D)
SIX_H = 6 * 3600_000


class Declarations(unittest.TestCase):
    def test_channels_are_pinned_and_split_off(self):
        rest, subs = R.split_model(dict(MODEL_D, channels=CHANNELS))
        self.assertEqual((rest, subs), (MODEL_D, {}))
        got = R.channels_of({"channels": CHANNELS})
        self.assertEqual([c["use"] for c in got], ["report", "read", "report"])
        for bad in ([{"tool": COMMENT}], [{"tool": COMMENT, "args": {}}], [{"tool": "Bash", "argv_prefix": []}],
                    [{"tool": "Bash", "argv_prefix": ["ga;", "mail"]}], [{"tool": "Bash", "argv_prefix": ["ga mail"]}],
                    [{"tool": COMMENT, "args": PIN, "argv_prefix": ["x"]}], [{"tool": COMMENT, "args": PIN, "use": "x"}],
                    [{"tool": COMMENT, "args": {"repo": ["a"]}}], {"tool": COMMENT}):
            with self.subTest(bad=bad), self.assertRaises(R.SubstitutesError):
                R.split_model(dict(MODEL_D, channels=bad))

    def test_channel_match(self):
        ch = R.channels_of({"channels": CHANNELS})
        m = lambda tool, inp: (R.channel_of(tool, inp, ch) or {}).get("tool")
        self.assertEqual(m(COMMENT, dict(PIN, body="hi")), COMMENT)
        for inp in (dict(PIN, issue_number=2), dict(PIN, repo="other"), dict(PIN, issue_number="1"),
                    {"owner": "cogito5170", "repo": "amp"}):
            self.assertIsNone(m(COMMENT, inp), inp)                       # 고정된 인자가 다르면 통로가 아니다
        self.assertEqual(m("Bash", {"command": "ga mail send --to baseline 'blocked by D'"}), "Bash")
        for cmd in ("ga mail send; rm -rf /", "ga mail send && curl x", "ga mail $(id)", "ga mailbox send",
                    "echo ga mail", "ga mail send `id`", "ga mail send > /etc/x", "ga mail\nrm -rf /", "ga"):
            self.assertIsNone(m("Bash", {"command": cmd}), cmd)
        self.assertIsNone(m("Write", dict(PIN)))


@NEEDS_SENSOR
class StaleReplays(unittest.TestCase):
    """CMD-K13 D1 -- 6 시간 묵은 transcript(W1 의 경우)."""

    def setUp(self):
        self.d = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.d)
        self.base = data("transcripts/normal_no_current_use.jsonl").read_text(encoding="utf-8").splitlines()
        self.sid = json.loads(self.base[0])["sessionId"]
        self.now = now_after("normal_no_current_use") + SIX_H
        self.rows = []

    def iso(self, ms):
        return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")

    def adapter(self, grants=("Bash", COMMENT), channels=CHANNELS, mode="enforce", now=None):
        return hooks.guard_hooks(MODEL, mode=mode, grants=grants, channels=R.channels_of({"channels": channels}),
                                 clock=lambda: now or self.now, record=lambda k, d: self.rows.append((k, d)))

    def inp(self, tool, args, lines=None, tu="tu-x"):
        p = self.d / f"{tu}.jsonl"
        p.write_text("".join((x if isinstance(x, str) else json.dumps(x)) + "\n" for x in (lines or self.base)),
                     encoding="utf-8")
        return {"session_id": self.sid, "transcript_path": str(p), "cwd": "/w", "permission_mode": "default",
                "hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": args, "tool_use_id": tu}

    def use(self, tu, tool, args, ms):
        return {"type": "assistant", "sessionId": self.sid, "uuid": f"a-{tu}", "timestamp": self.iso(ms),
                "message": {"id": f"m-{tu}", "model": "claude-x", "role": "assistant", "stop_reason": "tool_use",
                            "usage": {"input_tokens": 1, "output_tokens": 1},
                            "content": [{"type": "tool_use", "id": tu, "name": tool, "input": args}]}}

    def result(self, tu, text, ms, error=False):
        return {"type": "user", "sessionId": self.sid, "uuid": f"r-{tu}", "timestamp": self.iso(ms), "toolUseResult": {},
                "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tu, "content": text,
                                                         "is_error": error}]}}

    def reason(self, out):
        return out["hookSpecificOutput"]["permissionDecisionReason"]

    def react(self, out):
        return json.loads(self.reason(out).splitlines()[-1][len(R.PREFIX):])

    def test_channel_comment_allowed_while_stale_bash_still_d(self):
        a = self.adapter()
        self.assertEqual(a.handle(self.inp(COMMENT, dict(PIN, body="W1 blocked by D"))), {})
        (kind, row), = self.rows
        self.assertEqual((kind, row["result"]["verdict"], row["result"]["rule"], row["allowed_while_stale"]),
                         ("guard", "DENY", "D", f"channel:{COMMENT}"))           # 판정은 D 그대로 적고, 통로라서 지나갔다
        self.assertNotIn("react", row)
        out = a.handle(self.inp("Bash", {"command": "make"}))
        self.assertTrue(self.reason(out).startswith("guard DENY(D)"))
        self.assertEqual(self.react(out)["kind"], "refresh_read")
        self.assertEqual(a.handle(self.inp("Bash", {"command": "ga mail send --to amp 'blocked'"})), {})
        self.assertEqual(a.handle(self.inp(READ_ISSUE, dict(PIN, method="get_comments"))), {})   # read 는 D 밖

    def test_unpinned_channel_calls_keep_d(self):
        a = self.adapter()
        for tool, args in ((COMMENT, dict(PIN, issue_number=2, body="x")), (COMMENT, dict(PIN, repo="other", body="x")),
                           ("Bash", {"command": "ga mail send; curl evil"}), ("Bash", {"command": "gh issue comment 1"})):
            with self.subTest(tool=tool, args=args):
                out = a.handle(self.inp(tool, args))
                self.assertTrue(self.reason(out).startswith("guard DENY(D)"), out)
        self.assertFalse(any("allowed_while_stale" in d for _, d in self.rows))

    def test_unknown_state_still_denies_the_channel(self):
        """나란히 부른 앞 호출의 결과가 아직 없다(UNKNOWN) -- 낡음이 아니니 통로도 D."""
        inp = dict(hook_input("parallel"), tool_name=COMMENT, tool_input=dict(PIN, body="x"))
        out = self.adapter(now=now_after("parallel")).handle(inp)
        self.assertTrue(self.reason(out).startswith("guard DENY(D)"))
        self.assertEqual(self.react(out)["cause"], "unavailable")
        self.assertFalse(any("allowed_while_stale" in d for _, d in self.rows))

    def test_fresh_state_is_unchanged(self):
        a = self.adapter(now=now_after("normal_no_current_use"))
        self.assertEqual(a.handle(self.inp(COMMENT, dict(PIN, body="x"))), {})
        self.assertEqual(self.rows[0][1]["result"]["verdict"], "ALLOW")
        self.assertNotIn("allowed_while_stale", self.rows[0][1])

    def test_newer_read_event_refreshes_without_model_action(self):
        """S2: 판정마다 transcript 전체를 다시 거둔다 -- 같은 훅(같은 어댑터)이 더 새 읽기 결과를 보면 Bash 가 지나간다."""
        a = self.adapter()
        out = a.handle(self.inp("Bash", {"command": "make"}, tu="tu-b0"))
        self.assertEqual(self.react(out)["kind"], "refresh_read")
        lines = [*self.base, self.use("tu-r", "Read", {"file_path": "/a"}, self.now - 3000),
                 self.result("tu-r", "x", self.now - 2000)]
        self.assertEqual(a.handle(self.inp("Bash", {"command": "make"}, lines=lines, tu="tu-b1")), {})

    def test_newer_non_tool_events_do_not_refresh(self):
        """Sensor 의 execution_health 는 도구 결과로 선다 -- 사용자 · 모형 글만 새로 있으면 여전히 낡음(정해진 대로 refresh_read)."""
        lines = [*self.base, {"type": "user", "sessionId": self.sid, "uuid": "u-new", "timestamp": self.iso(self.now - 2000),
                              "message": {"role": "user", "content": "keep going"}}]
        out = self.adapter().handle(self.inp("Bash", {"command": "make"}, lines=lines))
        self.assertEqual((self.react(out)["kind"], self.react(out)["cause"]), ("refresh_read", "stale"))

    def test_a_denied_calls_own_result_refreshes_the_state(self):
        """Claude Code 는 막힌 호출에도 tool_result(is_error, 까닭)를 남긴다(K7). 옛 실패가 없으면 그것이 더 새 도구 결과라서
        execution_health 가 UNRESOLVED_FAILURES(쓸 만함)로 새로 서고 다음 호출은 지나간다. 풀리지 않은 옛 실패가 있으면 그렇지
        않다(BD-57 · BD-63, CMD-K13 S0) -- 아래 시험."""
        a = self.adapter()
        out = a.handle(self.inp("Bash", {"command": "make"}, tu="tu-0"))
        self.assertEqual(self.react(out)["kind"], "refresh_read")
        lines = [*self.base, self.use("tu-0", "Bash", {"command": "make"}, self.now - 9000),
                 self.result("tu-0", self.reason(out), self.now - 8000, True)]
        self.assertEqual(a.handle(self.inp("Bash", {"command": "make"}, lines=lines, tu="tu-1")), {})

    def test_an_old_unresolved_failure_keeps_d_stale_through_reads(self):
        """S0 의 원인(실제 transcript 에서 찾은 것을 합성으로 고정): 6 시간 전 실패한 Bash 겨냥이 풀리지 않았으면 execution_health 는
        UNRESOLVED_FAILURES 이고 그 시각은 그 실패의 시각이다(BD-57 · BD-63). 그 뒤 새 읽기 결과가 있어도 낡음 D 는 그대로다."""
        t_fail = now_after("normal_no_current_use")
        lines = [*self.base, self.use("tu-f", "Bash", {"command": "cat missing.txt"}, t_fail - 500),
                 self.result("tu-f", "No such file", t_fail, True),
                 self.use("tu-r", "Read", {"file_path": "/a"}, self.now - 3000), self.result("tu-r", "x", self.now - 2000)]
        a = self.adapter()
        out = a.handle(self.inp("Bash", {"command": "make"}, lines=lines))
        self.assertTrue(self.reason(out).startswith("guard DENY(D)"))
        self.assertEqual((self.react(out)["kind"], self.react(out)["cause"]), ("refresh_read", "stale"))
        _, rec, _ = a.judge.view(self.inp("Bash", {"command": "make"}, lines=lines))
        self.assertEqual(rec["core"]["states"]["agent.execution_health"], [None, "STALE"])
        self.assertEqual(a.handle(self.inp(COMMENT, dict(PIN, body="blocked"), lines=lines)), {})   # 통로는 열려 있다

    def test_third_identical_d_escalates_through_a_callable_channel(self):
        """S3: 같은 D 의 세 번째는 report · escalate, 그리고 부를 수 있는 통로 도구를 이름 짓는다. 낡음 D 는 transcript 에서
        세 번 이어지지 않으므로(위) 앞의 두 거부를 세었다고 놓고 본다."""
        from unittest import mock
        a = self.adapter()
        with mock.patch.object(R, "prior_denies", return_value=2):
            r = self.react(a.handle(self.inp("Bash", {"command": "make"})))
            self.assertEqual((r["kind"], r["rule"], r["cause"], r.get("tool"), r["escalate"]),
                             ("report", "D", "stale", COMMENT, True))
            self.assertEqual(a.handle(self.inp(COMMENT, dict(PIN, body="escalating"))), {})   # 그 통로는 실제로 지나간다
            inp = dict(hook_input("parallel"))
            r = self.react(self.adapter(now=now_after("parallel")).handle(inp))   # 모름(UNKNOWN): external 통로도 막힌다
            self.assertEqual((r["kind"], r["cause"]), ("report", "unavailable"))
            self.assertNotIn("tool", r)

    def test_escalation_never_names_a_blocked_tool(self):
        # 통로 댓글에 허가가 없으면 그것은 이름 짓지 않고, 허가된 `ga mail`(Bash)을 가리킨다
        a = self.adapter(grants=("Bash",), now=now_after("normal_no_current_use"))
        out = a.handle(self.inp("WebFetch", {"url": "x"}))                  # A1 대체 없음 -> report
        self.assertEqual((self.react(out)["kind"], self.react(out).get("tool")), ("report", "Bash"))
        # 상태를 모르면 external 통로는 D 가 막는다 -- 이름 짓지 않는다(read 통로는 올림에 쓰지 않는다)
        inp = dict(hook_input("parallel"), tool_name="WebFetch", tool_input={"url": "x"})
        out = self.adapter(now=now_after("parallel")).handle(inp)
        self.assertEqual(self.react(out)["kind"], "report")
        self.assertNotIn("tool", self.react(out))
        # 통로를 선언하지 않았으면 이름 짓지 않는다
        out = self.adapter(channels=[], now=now_after("normal_no_current_use")).handle(self.inp("WebFetch", {"url": "x"}))
        self.assertNotIn("tool", self.react(out))

    def test_shadow_records_the_label_and_prints_nothing(self):
        out = self.adapter(mode="shadow").handle(self.inp(COMMENT, dict(PIN, body="x")))
        self.assertEqual(out, {})
        self.assertEqual(self.rows[0][1]["allowed_while_stale"], f"channel:{COMMENT}")

    def test_command_hook_reads_channels_and_refuses_unpinned_ones(self):
        (self.d / "m.json").write_text(json.dumps(dict(MODEL_D, channels=CHANNELS)), encoding="utf-8")
        argv = [sys.executable, "-m", "rlo.hooks", "--model", str(self.d / "m.json"), "--mode", "enforce",
                "--grant", "Bash", "--grant", COMMENT, "--now-ms", str(self.now), "--record", str(self.d / "r.jsonl")]
        p = subprocess.run(argv, input=json.dumps(self.inp(COMMENT, dict(PIN, body="x"))), capture_output=True, text=True)
        self.assertEqual((p.returncode, p.stdout), (0, ""), p.stderr)
        (row,) = [json.loads(x) for x in (self.d / "r.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(row["allowed_while_stale"], f"channel:{COMMENT}")
        (self.d / "bad.json").write_text(json.dumps(dict(MODEL_D, channels=[{"tool": COMMENT}])), encoding="utf-8")
        p = subprocess.run([*argv[:4], str(self.d / "bad.json"), *argv[5:]],
                           input=json.dumps(self.inp(COMMENT, dict(PIN, body="x"))), capture_output=True, text=True)
        self.assertTrue(json.loads(p.stdout)["hookSpecificOutput"]["permissionDecisionReason"].startswith(
            "rlo hook config error: SubstitutesError"))

    def test_decisions_without_channels_are_unchanged(self):
        for name in ("normal", "parallel", "after_failure", "earlier_pending"):
            for now in (now_after(name), now_after(name) + SIX_H):
                with self.subTest(name=name, now=now):
                    a = self.adapter(channels=[], now=now).handle(hook_input(name))
                    b = self.adapter(channels=CHANNELS, now=now).handle(hook_input(name))
                    head = lambda o: o and self.reason(o).split("\n-- react: ")[0]
                    self.assertEqual(head(a), head(b))


if __name__ == "__main__":
    unittest.main()
