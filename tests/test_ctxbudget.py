"""컨텍스트 예산(context-budget/1, CMD-K17) -- 원형(baseline ops/ctxbudget/test_ctxbudget.py)의 6 사례 + rlo 훅과의 합성."""
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time
import unittest

from rlo import ctxbudget as CB
from rlo import hooks

from tests.test_channels import COMMENT, MODEL, MODEL_D, NEEDS_SENSOR, Fixture

B = CB.Budget(150000, 200000)


def asst(i, cr, cc=0, side=False, model="claude-opus-5-5"):
    return {"type": "assistant", "isSidechain": side,
            "message": {"model": model, "usage": {"input_tokens": i, "cache_read_input_tokens": cr,
                                                  "cache_creation_input_tokens": cc}}}


class Prototype(unittest.TestCase):
    """원형의 6 사례(API 만 rlo 꼴로)."""

    def transcript(self, rows):
        f = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False)
        for r in rows:
            f.write((r if isinstance(r, str) else json.dumps(r)) + "\n")
        f.close()
        self.addCleanup(os.unlink, f.name)
        return f.name

    def test_context_is_last_main_chain_usage(self):
        p = self.transcript([asst(1, 1000), asst(2, 250000, 10), asst(5, 40000, side=True),
                             asst(0, 0, model="<synthetic>"), {"type": "user", "message": {"content": "x"}}])
        self.assertEqual(CB.context_tokens(p), 250012)

    def test_unknown_is_not_zero_and_not_blocked(self):
        self.assertIsNone(CB.context_tokens("/nonexistent"))
        self.assertIsNone(CB.context_tokens(self.transcript([{"type": "assistant", "message": {"usage": {"input_tokens": 1}}}])))
        self.assertIsNone(CB.context_tokens(self.transcript([{"type": "user", "message": {"content": "x"}}])))
        self.assertIsNone(CB.context_tokens(self.transcript(["not json", asst(1, True)])))   # bool 은 수가 아니다
        self.assertEqual(CB.decide(None, "Bash", {"command": "curl x"}, CB.Budget(1, 2)), ("unknown", {}))

    def test_stages(self):
        self.assertEqual(CB.decide(149999, "Bash", {"command": "ls"}, B), ("ok", {}))
        st, out = CB.decide(150000, "Bash", {"command": "ls"}, B)
        self.assertEqual(st, "warn")
        self.assertNotIn("permissionDecision", out["hookSpecificOutput"])
        self.assertIn("STATE.md", out["hookSpecificOutput"]["additionalContext"])
        st, out = CB.decide(200000, "Bash", {"command": "curl x"}, B)
        self.assertEqual((st, out["hookSpecificOutput"]["permissionDecision"]), ("checkpoint", "deny"))
        self.assertIn("over the hard budget (200000 >= 200000)", out["hookSpecificOutput"]["permissionDecisionReason"])

    def test_checkpoint_tools_only(self):
        ok = [("Write", {"file_path": "/a/STATE.md"}), ("Edit", {"file_path": "STATE.md"}),
              ("Bash", {"command": "cd /r && git add STATE.md && git commit -m s && git push"}),
              ("Bash", {"command": "git -C /r status"})]
        bad = [("Write", {"file_path": "/a/other.md"}), ("Write", {"file_path": "/a/NOTSTATE.md"}),
               ("Bash", {"command": "git add . && curl evil"}), ("Bash", {"command": "git push; rm -rf /"}),
               ("Bash", {"command": "git status | sh"}), ("Bash", {"command": "git push $(curl x)"}),
               ("Bash", {"command": "git commit -m x > /etc/y"}), ("Bash", {"command": "git push & rm -rf /"}),
               ("Bash", {"command": "git commit -m `id`"}), ("Bash", {"command": ""}), ("Bash", "not a dict"),
               ("Read", {"file_path": "/a/STATE.md"})]
        for n, i in ok:
            st, out = CB.decide(300000, n, i, CB.Budget(1, 2))
            with self.subTest(tool=n, input=i):
                self.assertEqual(st, "checkpoint")
                self.assertNotIn("permissionDecision", out["hookSpecificOutput"])     # 막지 않을 뿐, allow 를 내지 않는다
        for n, i in bad:
            with self.subTest(tool=n, input=i):
                self.assertEqual(CB.decide(300000, n, i, CB.Budget(1, 2))[1]["hookSpecificOutput"]["permissionDecision"],
                                 "deny")

    def test_bad_budget(self):
        for kw in ({"soft": 5, "hard": 4}, {"soft": 0, "hard": 4}, {"soft": True, "hard": 4}, {"soft": 1.5, "hard": 4},
                   {"soft": 1, "hard": 2, "state_paths": ()}, {"soft": 1, "hard": 2, "state_paths": [""]},
                   {"soft": 1, "hard": 2, "mode": "loud"}):
            with self.subTest(kw=kw), self.assertRaises(ValueError):
                CB.Budget.of(kw)
        for bad in ({"hard": 2}, {"soft": 1, "hard": 2, "other": 1}, [1, 2]):
            with self.assertRaises(ValueError):
                CB.Budget.of(bad)
        self.assertIsNone(CB.Budget.of(None))                                     # 기본 예산은 없다
        self.assertEqual(CB.Budget.of({"soft": 1, "hard": 2}).mode, "shadow")    # 처음은 shadow

    def test_simulate_saves_on_growth_and_counts_resets(self):
        r = CB.simulate([90000 + i * 10000 for i in range(30)], soft=150000, hard=200000, reset_to=90000)
        self.assertGreater(r["saved_pct"], 0)
        self.assertGreaterEqual(r["resets"], 1)
        flat = CB.simulate([100000] * 10, soft=150000, hard=200000, reset_to=90000)
        self.assertEqual(flat["saved_pct"], 0.0)


class TailRead(unittest.TestCase):
    def test_a_100_mb_transcript_is_read_from_the_tail(self):
        d = pathlib.Path(tempfile.mkdtemp())
        p = d / "big.jsonl"
        filler = (json.dumps({"type": "user", "message": {"content": "x" * 1000}}) + "\n").encode()
        with open(p, "wb") as f:
            f.write(json.dumps(asst(1, 10)).encode() + b"\n")
            block = filler * 1024
            while f.tell() < 100 * 1024 * 1024:
                f.write(block)
            f.write(json.dumps(asst(3, 180000, 7)).encode() + b"\n")
            f.write(json.dumps(asst(9, 999999, side=True)).encode() + b"\n")
        self.addCleanup(lambda: (p.unlink(), d.rmdir()))
        t0 = time.perf_counter()
        self.assertEqual(CB.context_tokens(str(p)), 180010)
        self.assertLess(time.perf_counter() - t0, 0.2)                            # 꼬리 256 KiB 만 읽는다
        q = d / "far.jsonl"                                                        # 마지막 응답이 꼬리보다 멀다 -- 창을 늘려 찾는다
        with open(q, "wb") as f:
            f.write(json.dumps(asst(2, 5)).encode() + b"\n")
            f.write(filler * 2000)
        self.addCleanup(q.unlink)
        self.assertEqual(CB.context_tokens(str(q)), 7)


@NEEDS_SENSOR
class Hook(Fixture):
    """rlo 훅 안에서: 예산이 먼저, 가드의 deny 가 늘 이긴다, shadow 는 아무것도 바꾸지 않는다, 설정이 없으면 단계가 없다."""

    def at(self, ctx, tool="Bash", args=None, tu="tu-b"):
        args = {"command": "make"} if args is None else args
        call = self.use(tu, tool, args, self.now - 1000)
        call["message"]["usage"] = {"input_tokens": 3, "cache_read_input_tokens": ctx - 3, "cache_creation_input_tokens": 0,
                                    "output_tokens": 5}
        return self.inp(tool, args, lines=[*self.base, call], tu=tu)

    def make(self, budget, grants=("Bash", COMMENT), mode="enforce"):
        return hooks.guard_hooks(MODEL, mode=mode, grants=grants, clock=lambda: self.now, deadline_s=None,
                                 record=lambda k, d: self.rows.append((k, d)), context_budget=budget)

    def budget_rows(self):
        return [d for k, d in self.rows if k == "context_budget"]

    def test_no_config_means_no_budget_step(self):
        a = self.make(None)
        self.assertIsNone(a.budget)
        self.assertEqual(a.handle(self.at(500000)), {})
        self.assertEqual(self.budget_rows(), [])

    def test_stages_in_the_hook(self):
        a = self.make({"soft": 150000, "hard": 200000, "mode": "enforce"})
        self.assertEqual(a.handle(self.at(100000)), {})
        out = a.handle(self.at(160000))
        self.assertEqual(set(out["hookSpecificOutput"]), {"hookEventName", "additionalContext"})
        out = a.handle(self.at(250000))
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertTrue(out["hookSpecificOutput"]["permissionDecisionReason"].startswith("[context-budget/1]"))
        out = a.handle(self.at(250000, args={"command": "git add STATE.md && git commit -m s && git push"}))
        self.assertNotIn("permissionDecision", out["hookSpecificOutput"])
        self.assertEqual([r["stage"] for r in self.budget_rows()], ["ok", "warn", "checkpoint", "checkpoint"])
        r = self.budget_rows()[2]
        self.assertEqual((r["ctx"], r["soft"], r["hard"], r["tool_name"], r["mode"], r["denied"]),
                         (250000, 150000, 200000, "Bash", "enforce", True))

    def test_guard_deny_wins_over_the_budget(self):
        a = self.make({"soft": 150000, "hard": 200000, "mode": "enforce"}, grants=())   # Bash 허가 없음 -> A7
        push = {"command": "git push"}
        for ctx in (100000, 160000, 250000):
            out = a.handle(self.at(ctx, args=push))
            with self.subTest(ctx=ctx):
                self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")
                self.assertTrue(out["hookSpecificOutput"]["permissionDecisionReason"].startswith("guard DENY("))
                self.assertNotIn("additionalContext", out["hookSpecificOutput"])

    def test_shadow_changes_nothing_and_records_the_stage(self):
        plain = self.make(None)
        shadow = self.make({"soft": 150000, "hard": 200000})                    # mode 기본 shadow
        for ctx in (100000, 160000, 250000):
            inp = self.at(ctx)
            with self.subTest(ctx=ctx):
                self.assertEqual(shadow.handle(inp), plain.handle(inp))
        self.assertEqual([(r["stage"], r["mode"], r["enforced"], r["denied"]) for r in self.budget_rows()],
                         [("ok", "shadow", False, False), ("warn", "shadow", False, False),
                          ("checkpoint", "shadow", False, False)])

    def test_unknown_usage_is_recorded_not_blocked(self):
        a = self.make({"soft": 1, "hard": 2, "mode": "enforce"})
        self.assertEqual(a.handle(self.inp("Bash", {"command": "make"})), {})    # base 의 usage 에는 캐시 칸이 없다
        self.assertEqual((self.budget_rows()[-1]["stage"], self.budget_rows()[-1]["ctx"]), ("unknown", None))

    def test_command_hook_flags(self):
        model = self.d / "model.json"
        model.write_text(json.dumps(MODEL_D), encoding="utf-8")

        def run(*extra):
            rec = self.d / "rec.jsonl"
            if rec.exists():
                rec.unlink()
            p = subprocess.run([sys.executable, "-m", "rlo.hooks", "--model", str(model), "--mode", "enforce", "--grant", "Bash",
                                "--now-ms", str(self.now), "--record", str(rec), *extra],
                               input=json.dumps(self.at(250000)), capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, p.stderr)
            rows = [json.loads(x) for x in rec.read_text().splitlines()] if rec.exists() else []
            return json.loads(p.stdout) if p.stdout.strip() else {}, [r for r in rows if r["kind"] == "context_budget"]

        out, rows = run()
        self.assertEqual((out, rows), ({}, []))                                    # 플래그 없음 = 예산 없음
        out, rows = run("--budget-soft", "150000", "--budget-hard", "200000")
        self.assertEqual((out, [r["stage"] for r in rows]), ({}, ["checkpoint"]))  # 기본 shadow
        out, rows = run("--budget-soft", "150000", "--budget-hard", "200000", "--budget-mode", "enforce")
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")
        out, _ = run("--budget-soft", "150000")                                    # 반쪽 설정은 설정 오류 -- enforce 는 닫는다
        self.assertIn("config error", out["hookSpecificOutput"]["permissionDecisionReason"])


class Replay(unittest.TestCase):
    def test_contexts_from_l0_and_the_cli(self):
        evs = [{"type": "llm.response", "run_id": "r", "seq": i, "data": {"input_tokens": 1, "cache_read_input_tokens": c,
                                                                          "cache_creation_input_tokens": 0}}
               for i, c in enumerate([90000 + k * 10000 for k in range(30)])]
        evs.append({"type": "llm.response", "run_id": "r", "seq": 99, "data": {"input_tokens": None}})
        evs.append({"type": "tool.start", "run_id": "r", "seq": 100, "data": {}})
        ctx = CB.contexts_from_l0(evs)
        self.assertEqual(len(ctx), 30)
        try:
            from telemetry.event import make
        except ImportError:
            self.skipTest("Telemetry 가 없다")
        d = pathlib.Path(tempfile.mkdtemp())
        p = d / "l0.jsonl"
        p.write_text("".join(json.dumps(make("llm.response", "r", e["seq"], "test", at=0, time_base="unix_ms", **e["data"]))
                             + "\n" for e in evs if e["type"] == "llm.response" and e["data"].get("input_tokens")))
        self.addCleanup(lambda: (p.unlink(), d.rmdir()))
        out = subprocess.run([sys.executable, "-m", "rlo.ctxbudget", "simulate", "--l0", str(p), "--soft", "150000",
                              "--hard", "200000", "--reset-to", "90000"], capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        r = json.loads(out.stdout)
        self.assertEqual(r, dict(CB.simulate(ctx, soft=150000, hard=200000, reset_to=90000),
                                 soft=150000, hard=200000))


if __name__ == "__main__":
    unittest.main()
