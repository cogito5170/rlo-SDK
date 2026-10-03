"""훅이 Sensor 에 넣는 사건 수에 한도를 둔다(CMD-K14) -- 긴 세션도 판정 한 번의 비용이 세션 길이와 함께 자라지 않는다."""
import json
import subprocess
import sys
import unittest

from rlo import hooks
from rlo import react as R
from rlo.example_hooks import hook_input, now_after

from tests.test_channels import CHANNELS, COMMENT, MODEL, MODEL_D, NEEDS_SENSOR, PIN, Fixture


def ev(i, typ, idx=None, call=None):
    d = {} if idx is None else {"tool_index": idx}
    return {"id": f"e{i}", "type": typ, "data": d if call is None else dict(d, call_index=call)}


class WindowEvents(unittest.TestCase):
    """window_events 의 꼴: 최근 k 개, 대기 중인 tool.start 와 창 안 tool.end 의 짝은 창 밖이라도 둔다."""

    def setUp(self):
        self.events = [ev(0, "turn.start"), ev(1, "llm.response", call=0), ev(2, "tool.start", 0, 0),
                       ev(3, "tool.end", 0),                                                  # 끝난 호출(창 밖)
                       ev(4, "llm.response", call=1), ev(5, "tool.start", 1, 1),              # 대기 중(창 밖) + 그 응답
                       ev(6, "llm.response", call=2), ev(7, "tool.start", 2, 2),              # 짝(tool.end)은 창 안
                       ev(8, "llm.response", call=3), ev(9, "tool.end", 2),
                       ev(10, "tool.start", 3, 3), ev(11, "tool.end", 3)]

    def ids(self, out):
        return [e["id"] for e in out]

    def test_keeps_the_last_k_plus_pending_and_pairs(self):
        out, dropped = hooks.window_events(self.events, 4)
        self.assertEqual(self.ids(out), ["e4", "e5", "e6", "e7", "e8", "e9", "e10", "e11"])
        self.assertEqual(dropped, 4)                     # e0 · e1 · e2 · e3: 끝난 호출과 그 응답

    def test_the_responses_of_kept_calls_come_along(self):
        out, _ = hooks.window_events(self.events, 2)
        self.assertEqual(self.ids(out), ["e4", "e5", "e8", "e10", "e11"])   # 대기 중인 호출과 그 응답, 창 안 호출(e10)의 응답

    def test_order_is_kept(self):
        out, _ = hooks.window_events(self.events, 4)
        self.assertEqual(self.ids(out), sorted(self.ids(out), key=lambda x: int(x[1:])))

    def test_short_or_unbounded_is_unchanged(self):
        self.assertEqual(hooks.window_events(self.events, None), (self.events, 0))
        self.assertEqual(hooks.window_events(self.events, len(self.events)), (self.events, 0))

    def test_bad_sizes_are_config_errors(self):
        for k in (0, -1, True, "400", 1.5):
            with self.assertRaises(ValueError):
                hooks.check_window(k)
        self.assertEqual(hooks.check_window(None), None)

    def test_default_is_bounded(self):
        self.assertIsInstance(hooks.WINDOW, int)
        self.assertTrue(0 < hooks.WINDOW <= 1000)


@NEEDS_SENSOR
class WindowDecisions(Fixture):
    """창(작게)과 전부를 넣은 판정이 같다 -- 다른 곳은 아래에 적은 것뿐."""

    def judge_adapter(self, window, health_ttl=False, now=None):
        return hooks.guard_hooks(MODEL, mode="enforce", grants=("Bash", COMMENT), channels=R.channels_of({"channels": CHANNELS}),
                                 clock=lambda: now or self.now, record=lambda k, d: self.rows.append((k, d)),
                                 health_ttl=health_ttl, window=window, deadline_s=None)

    def reads(self, n, t0, prefix="r"):
        out = []
        for i in range(n):
            out += [self.use(f"tu-{prefix}{i}", "Read", {"file_path": f"/a{i}"}, t0 + 10 * i),
                    self.result(f"tu-{prefix}{i}", "x", t0 + 10 * i + 5)]
        return out

    def test_replays_decide_the_same_with_a_small_window(self):
        t_fail = now_after("normal_no_current_use")
        old_failure = [*self.base, self.use("tu-f", "Bash", {"command": "cat missing.txt"}, t_fail - 500),
                       self.result("tu-f", "No such file", t_fail, True), *self.reads(6, self.now - 9000)]
        cases = [(hook_input(n), now_after(n)) for n in ("normal", "parallel", "after_failure", "earlier_pending",
                                                         "read_after_failure", "first_call")]
        cases += [(self.inp(t, a, lines=old_failure, tu=f"tu-c{i}"), None) for i, (t, a) in
                  enumerate([("Bash", {"command": "make"}), (COMMENT, dict(PIN, body="x")), ("Read", {"file_path": "/b"})])]
        for inp, now in cases:
            with self.subTest(tool=inp["tool_name"], path=inp["transcript_path"][-24:]):
                self.assertEqual(self.judge_adapter(3, now=now).handle(inp), self.judge_adapter(None, now=now).handle(inp))

    def text(self, i, ms):
        return {"type": "assistant", "sessionId": self.sid, "uuid": f"x{i}", "timestamp": self.iso(ms),
                "message": {"id": f"mx{i}", "model": "claude-x", "role": "assistant", "stop_reason": "end_turn",
                            "usage": {"input_tokens": 1, "output_tokens": 1}, "content": [{"type": "text", "text": "."}]}}

    def assert_unknown_d(self, out):
        self.assertTrue(self.reason(out).startswith("guard DENY(D)"))
        self.assertEqual(self.react(out)["cause"], "unavailable")

    def test_pending_call_outside_the_window_still_gives_unknown_d(self):
        """Sensor 는 본 도구 결과가 하나도 없고 결과를 기다리는 호출이 있으면 UNKNOWN 이다. 그 호출이 창 밖이어도 같다."""
        user = {"type": "user", "sessionId": self.sid, "uuid": "u0", "timestamp": self.iso(self.now - 30000),
                "message": {"role": "user", "content": "go"}}
        lines = [user, self.use("tu-p", "Bash", {"command": "sleep 99"}, self.now - 20000),          # 결과 없음
                 *(self.text(i, self.now - 10000 + i) for i in range(8))]
        from telemetry.collect import from_cc_jsonl
        events = from_cc_jsonl(self.inp("Bash", {}, lines=lines, tu="tu-z")["transcript_path"], "x")
        pending = next(i for i, e in enumerate(events) if e["type"] == "tool.start" and e["data"]["tool_use_id"] == "tu-p")
        self.assertLess(pending, len(events) - 3)                         # 대기 중인 호출은 정말 창(3) 밖에 있다
        for window in (None, 3, 1):
            with self.subTest(window=window):
                a = self.judge_adapter(window)
                self.assert_unknown_d(a.handle(self.inp("Bash", {"command": "make"}, lines=lines)))
                self.assertTrue(self.reason(a.handle(self.inp(COMMENT, dict(PIN, body="x"), lines=lines, tu="tu-y")))
                                .startswith("guard DENY(D)"))             # 모름이면 통로도 막힌다
        for name in ("parallel", "earlier_pending"):                      # 짧은 실제 꼴: 창 1 · 2 에서도
            for window in (1, 2):
                with self.subTest(name=name, window=window):
                    self.assert_unknown_d(self.judge_adapter(window, now=now_after(name)).handle(hook_input(name)))

    def test_pending_call_inside_the_window_too(self):
        user = {"type": "user", "sessionId": self.sid, "uuid": "u0", "timestamp": self.iso(self.now - 30000),
                "message": {"role": "user", "content": "go"}}
        lines = [user, *(self.text(i, self.now - 10000 + i) for i in range(8)),
                 self.use("tu-p", "Bash", {"command": "sleep 99"}, self.now - 500)]
        self.assert_unknown_d(self.judge_adapter(4).handle(self.inp("Bash", {"command": "make"}, lines=lines)))

    def test_a_pending_call_after_results_is_the_same_as_the_full_feed(self):
        """본 결과가 있으면 기다리는 호출만으로는 UNKNOWN 이 아니다(Sensor) -- 창도 같다."""
        lines = [*self.base, self.use("tu-p", "Bash", {"command": "sleep 99"}, self.now - 20000), *self.reads(8, self.now - 10000)]
        inp = self.inp("Bash", {"command": "make"}, lines=lines)
        self.assertEqual(self.judge_adapter(4).handle(inp), self.judge_adapter(None).handle(inp))

    def test_documented_difference_an_old_failure_outside_the_window(self):
        """다른 곳: 창 밖의 풀리지 않은 옛 실패는 보이지 않는다. 선택 B 에서는 값만 바뀌고(판정은 같다), 예전 TTL 동작에서는
        그 실패로 생긴 낡음 D 도 사라진다."""
        t_fail = now_after("normal_no_current_use")
        lines = [*self.base, self.use("tu-f", "Bash", {"command": "cat missing.txt"}, t_fail - 500),
                 self.result("tu-f", "No such file", t_fail, True), *self.reads(6, self.now - 9000)]
        inp = self.inp("Bash", {"command": "make"}, lines=lines)
        full, win = self.judge_adapter(None), self.judge_adapter(4)
        self.assertEqual(full.judge.view(inp)[1]["core"]["states"]["agent.execution_health"][0], "UNRESOLVED_FAILURES")
        self.assertNotEqual(win.judge.view(inp)[1]["core"]["states"]["agent.execution_health"][0], "UNRESOLVED_FAILURES")
        self.assertEqual(full.handle(inp), {})
        self.assertEqual(win.handle(inp), {})
        self.assertTrue(self.reason(self.judge_adapter(None, health_ttl=True).handle(inp)).startswith("guard DENY(D)"))
        self.assertEqual(self.judge_adapter(4, health_ttl=True).handle(inp), {})

    def test_the_window_is_recorded(self):
        lines = [*self.base, *self.reads(8, self.now - 10000)]
        self.judge_adapter(4).handle(self.inp("Bash", {"command": "make"}, lines=lines, tu="tu-w"))
        self.judge_adapter(None).handle(self.inp("Bash", {"command": "make"}, lines=lines, tu="tu-w"))
        (k1, r1), (k2, r2) = self.rows
        self.assertEqual((k1, k2), ("guard", "guard"))
        self.assertEqual(r1["window"]["size"], 4)
        self.assertTrue(4 <= r1["window"]["fed"] <= 6)              # 창 끝에서 잘린 호출 하나의 시작 · 응답이 따라올 수 있다
        self.assertEqual(r2["window"], {"size": None, "fed": r1["window"]["fed"] + r1["window"]["dropped"], "dropped": 0})

    def test_a_long_session_feeds_at_most_the_window(self):
        """한도: 사건이 창보다 훨씬 많아도 Sensor 에 가는 수는 창(+ 대기 중인 호출)을 넘지 않는다. 기본 창 그대로."""
        n = hooks.WINDOW // 2 + 40                                          # 사건 2n 개 > 창
        lines = [*self.base, *self.reads(n, self.now - 20 * n - 1000)]
        a = hooks.guard_hooks(MODEL, mode="enforce", grants=("Bash",), clock=lambda: self.now,
                              record=lambda k, d: self.rows.append((k, d)))
        self.assertEqual(a.handle(self.inp("Bash", {"command": "make"}, lines=lines, tu="tu-long")), {})
        w = self.rows[-1][1]["window"]
        self.assertEqual(w["size"], hooks.WINDOW)
        self.assertTrue(hooks.WINDOW <= w["fed"] <= hooks.WINDOW + 2)
        self.assertGreater(w["dropped"], 0)


@NEEDS_SENSOR
class CommandHookWindow(Fixture):
    def run_hook(self, lines, *extra):
        model = self.d / "model.json"
        model.write_text(json.dumps(MODEL_D), encoding="utf-8")
        rec = self.d / "rec.jsonl"
        if rec.exists():
            rec.unlink()
        inp = self.inp("Bash", {"command": "make"}, lines=lines, tu="tu-cli")
        p = subprocess.run([sys.executable, "-m", "rlo.hooks", "--model", str(model), "--mode", "enforce", "--grant", "Bash",
                            "--now-ms", str(self.now), "--record", str(rec), *extra],
                           input=json.dumps(inp), capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        return json.loads(rec.read_text(encoding="utf-8").splitlines()[-1])

    def test_command_hook_has_the_window_and_records_it(self):
        lines = [*self.base, *(x for i in range(6) for x in (self.use(f"tu-r{i}", "Read", {"file_path": "/a"}, self.now - 900 + i),
                                                         self.result(f"tu-r{i}", "x", self.now - 800 + i)))]
        self.assertEqual(self.run_hook(lines)["window"]["size"], hooks.WINDOW)
        w = self.run_hook(lines, "--window", "5")["window"]
        self.assertEqual(w["size"], 5)
        self.assertTrue(5 <= w["fed"] <= 7)
        self.assertEqual(self.run_hook(lines, "--window", "0")["window"]["size"], None)


if __name__ == "__main__":
    unittest.main()
