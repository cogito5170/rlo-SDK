"""분당 한도 지킴이 · 걸음 차례(CMD-K12) -- 가짜 시계로. 429 는 실패가 아니라 미룸이고, 도구 걸음은 그 동안에도 돈다."""
import json
import math
import pathlib
import shutil
import tempfile
import unittest

import rlo
from rlo.governor import Governor, RateDeferred, gemini_429, quota_period, usage_tokens
from rlo.scheduler import ParkVerify, Scheduler, Step, StepKindError, load_step_kinds

KINDS = {"read": "tool", "check": "tool", "render": "tool", "plan": "model", "judge": "model"}


class Clock:
    def __init__(self, t=0.0):
        self.t, self.sleeps = t, []

    def __call__(self):
        return self.t

    def sleep(self, d):
        assert d > 0, d                                   # 바쁘게 기다리지 않는다: 0 이하로 자지 않는다
        self.sleeps.append(d)
        self.t += d


class Http429(Exception):
    """MS ProviderError 꼴(status · body · headers)."""

    def __init__(self, retry="7s", per="Minute"):
        super().__init__("429")
        self.status, self.body, self.headers = 429, gemini_429(retry, per), {}


class Provider:
    """가짜 모형: n 번째 부름(1 부터)마다 429. 부른 시각을 남긴다."""

    def __init__(self, clock, fail_on=(), retry="7s", per="Minute", tokens=100, other_on=()):
        self.clock, self.fail_on, self.retry, self.per, self.tokens, self.other_on = clock, set(fail_on), retry, per, tokens, set(other_on)
        self.calls, self.n = [], 0

    def __call__(self, payload):
        self.n += 1
        self.calls.append((self.clock(), payload))
        if self.n in self.fail_on:
            raise Http429(self.retry, self.per)
        if self.n in self.other_on:
            raise ValueError("boom")
        return {"text": f"ok {payload}", "usageMetadata": {"promptTokenCount": self.tokens - 10,
                                                           "candidatesTokenCount": 10, "totalTokenCount": self.tokens}}


def rows(report, kind):
    return [r for r in report.ledger if r["kind"] == kind]


class GovernorBudget(unittest.TestCase):
    def test_rpm_sliding_window(self):
        c = Clock(100.0)
        g = Governor({"m": {"rpm": 3}}, clock=c)
        for dt in (0, 10, 20):
            c.t = 100 + dt
            self.assertTrue(g.try_acquire().ok)
        c.t = 130
        no = g.try_acquire()
        self.assertEqual((no.ok, no.wait_s), (False, 30.0))       # 첫 부름(100)이 160 에 창을 나간다
        c.t = 160
        self.assertTrue(g.try_acquire().ok)
        self.assertEqual(g.wait_s(), 10.0)                        # 다음은 110 의 것이 나가는 170

    def test_tpm_uses_observed_usage(self):
        c = Clock()
        g = Governor({"m": {"tpm": 1000}}, clock=c)
        t = g.try_acquire(est_tokens=900)
        self.assertTrue(t.ok)
        self.assertFalse(g.try_acquire(est_tokens=200).ok)          # 추정 900 + 200 > 1000
        g.observe(t.ticket, {"usageMetadata": None, "promptTokenCount": 100, "candidatesTokenCount": 50})
        self.assertTrue(g.try_acquire(est_tokens=200).ok)           # 실제 150 + 200
        c.t = 10
        self.assertEqual(g.wait_s(est_tokens=900), 50.0)            # 150 + 200 + 900 > 1000 -- 0 의 것이 60 에 나간다

    def test_estimate_bigger_than_tpm_waits_for_an_empty_window_not_forever(self):
        c = Clock()
        g = Governor({"m": {"tpm": 100}}, clock=c)
        self.assertTrue(g.try_acquire(est_tokens=50).ok)
        self.assertEqual(g.wait_s(est_tokens=500), 60.0)
        c.t = 60
        self.assertTrue(g.try_acquire(est_tokens=500).ok)

    def test_429_retry_delay_wins(self):
        c = Clock(0)
        g = Governor({"m": {"rpm": 100}}, clock=c)
        self.assertEqual(g.on_rate_limit(Http429("41s")), 41.0)
        self.assertEqual(g.wait_s(), 41.0)
        c.t = 41
        self.assertEqual(g.wait_s(), 0.0)

    def test_429_without_retry_delay_reads_the_quota_hint(self):
        g = Governor({"m": {"rpm": 100}}, clock=Clock())
        self.assertEqual(g.on_rate_limit(Http429(None, "Minute")), 60.0)
        self.assertEqual(quota_period(gemini_429(None, "Day")), "day")
        self.assertTrue(math.isinf(Governor({"m": {}}, clock=Clock()).on_rate_limit(Http429(None, "Day"))))
        self.assertEqual(Governor({"m": {}}, clock=Clock()).on_rate_limit(status=429, body={}, headers={}), 60.0)

    def test_other_providers_retry_after_header(self):
        g = Governor({"m": {}}, clock=Clock(), provider="anthropic")
        self.assertEqual(g.on_rate_limit(status=429, body={"error": {"type": "rate_limit_error"}},
                                         headers={"retry-after": "12"}), 12.0)

    def test_is_rate_limit(self):
        from ms.providers import ProviderError
        self.assertTrue(Governor.is_rate_limit(ProviderError("x", status=429, body=gemini_429())))
        self.assertTrue(Governor.is_rate_limit(type("E", (Exception,), {"body": gemini_429()})()))
        self.assertFalse(Governor.is_rate_limit(ProviderError("x", status=500, body={})))
        self.assertFalse(Governor.is_rate_limit(ValueError("429")))           # 글은 읽지 않는다

    def test_usage_shapes(self):
        from ms.canonical import Usage
        self.assertEqual(usage_tokens({"input_tokens": 3, "output_tokens": 4}), 7)
        self.assertEqual(usage_tokens({"promptTokenCount": 3, "candidatesTokenCount": 4, "totalTokenCount": 9}), 9)
        self.assertEqual(usage_tokens(Usage(input_tokens=5, output_tokens=6)), 11)
        self.assertIsNone(usage_tokens({}))

    def test_budget_shape_is_checked(self):
        for bad in ({}, {"m": {"rpm": 0}}, {"m": {"rpd": 5}}, {"m": {"rpm": True}}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                Governor(bad)


class StepKinds(unittest.TestCase):
    def test_closed_table(self):
        d = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d)
        (d / "k.json").write_text(json.dumps({"schema": "rlo-step-kinds/1", "steps": KINDS}), encoding="utf-8")
        self.assertEqual(load_step_kinds(str(d / "k.json")), KINDS)
        for bad in ({"steps": KINDS}, {"schema": "rlo-step-kinds/1", "steps": {"x": "maybe"}},
                    {"schema": "rlo-step-kinds/1", "steps": {"has space": "tool"}},
                    {"schema": "rlo-step-kinds/1", "steps": KINDS, "extra": 1}):
            with self.subTest(bad=bad), self.assertRaises(StepKindError):
                load_step_kinds(bad)

    def test_unclassified_step_is_a_load_error(self):
        c = Clock()
        g = Governor({"m": {"rpm": 5}}, clock=c)
        with self.assertRaises(StepKindError):
            Scheduler([Step("a", "read", fn=lambda r: 1), Step("b", "summarize", payload="p")], g, Provider(c),
                      kinds=KINDS, clock=c, sleep=c.sleep)
        s = Scheduler([], g, Provider(c), kinds=KINDS, clock=c, sleep=c.sleep)
        with self.assertRaises(StepKindError):
            s.add(Step("x", "unknown", fn=lambda r: 1))
        with self.assertRaises(StepKindError):
            s.add(Step("y", "plan", payload="p", after=("nope",)))


class SchedulerRun(unittest.TestCase):
    """CMD-K12 D1 · D2."""

    def make(self, steps, budgets, provider_kw=(), **kw):
        c = Clock(kw.pop("t0", 0.0))
        g = Governor(budgets, clock=c)
        p = Provider(c, **dict(provider_kw))
        return c, p, Scheduler(steps, g, p, kinds=KINDS, clock=c, sleep=c.sleep, **kw)

    def test_429_on_nth_call_completes_with_zero_failed_steps(self):
        """n 번째 부름이 429(retryDelay 7s): 실패 0 · 기다리는 동안 도구 걸음이 돈다 · 모형 걸음은 창 뒤 차례대로."""
        steps = [Step("m1", "plan", payload="p1"), Step("m2", "plan", payload="p2"), Step("m3", "judge", payload="p3"),
                 Step("t1", "read", fn=lambda r: "a", not_before=3.0), Step("t2", "check", fn=lambda r: "b", not_before=5.0),
                 Step("m4", "plan", payload="p4", after=("t2",))]
        c, p, s = self.make(steps, {"m": {"rpm": 100}}, provider_kw={"fail_on": (2,)})
        r = s.run()
        self.assertTrue(r.ok, (r.failed, r.parked, r.skipped))
        self.assertEqual(r.failed, {})
        self.assertEqual(set(r.done), {"m1", "m2", "m3", "m4", "t1", "t2"})
        self.assertEqual([x[1] for x in p.calls], ["p1", "p2", "p2", "p3", "p4"])        # 차례 그대로, p2 를 다시
        (rl,) = rows(r, "rate_limit")
        self.assertEqual((rl["step"], rl["wait_s"]), ("m2", 7.0))
        self.assertEqual(p.calls[2][0], 7.0)                                            # retryDelay 가 이긴다
        tools = {x["step"]: x["at_ms"] for x in rows(r, "done") if x["step_kind"] == "tool"}
        self.assertEqual(tools, {"t1": 3000.0, "t2": 5000.0})                           # 기다리는 0–7 초 사이에 돌았다
        self.assertEqual([x["attempt"] for x in rows(r, "dispatch") if x["step"] == "m2"], [0, 1])

    def test_tool_step_is_not_blocked_behind_a_parked_model_step(self):
        steps = [Step("m1", "plan", payload="p1"), Step("m2", "plan", payload="p2"), Step("t1", "render", fn=lambda r: 1)]
        c, p, s = self.make(steps, {"m": {"rpm": 1}})
        r = s.run()
        self.assertTrue(r.ok)
        done_at = {x["step"]: x["at_ms"] for x in rows(r, "done")}
        self.assertEqual(done_at["t1"], 0.0)                     # m2 가 세워진 채로 t1 은 바로
        self.assertEqual(done_at["m2"], 60000.0)
        self.assertEqual(rows(r, "park")[0]["step"], "m2")

    def test_model_step_never_runs_while_the_budget_is_empty(self):
        steps = [Step(f"m{i}", "plan", payload=i, est_tokens=10) for i in range(7)]
        c, p, s = self.make(steps, {"m": {"rpm": 2}})
        r = s.run()
        self.assertTrue(r.ok)
        times = [t for t, _ in p.calls]
        for i, t in enumerate(times):                            # 어느 60 초 창에도 2 번 넘게 부르지 않았다
            self.assertLessEqual(sum(1 for u in times if t - 60 < u <= t), 2, times)
        self.assertEqual(times, [0, 0, 60, 60, 120, 120, 180])

    def test_model_steps_keep_queue_order(self):
        """앞 모형 걸음이 앞 도구 걸음을 기다리면 뒤 모형 걸음도 앞지르지 않는다."""
        steps = [Step("t1", "read", fn=lambda r: 1, not_before=30.0), Step("m1", "plan", payload="first", after=("t1",)),
                 Step("m2", "plan", payload="second")]
        c, p, s = self.make(steps, {"m": {"rpm": 10}})
        r = s.run()
        self.assertEqual([x[1] for x in p.calls], ["first", "second"])
        self.assertEqual(p.calls[0][0], 30.0)

    def test_no_busy_waiting_and_bounded_work(self):
        """한 번 잘 때마다 반드시 무언가가 바뀐다: 잠 수 ≤ 모형 걸음 + 429 + 도구 걸음, 부름 수 = 모형 걸음 + 429."""
        steps = [Step(f"m{i}", "plan", payload=i) for i in range(5)] + \
                [Step(f"t{i}", "read", fn=lambda r: 0, not_before=7.0 * i) for i in range(5)]
        c, p, s = self.make(steps, {"m": {"rpm": 2}}, provider_kw={"fail_on": (2, 4)})
        r = s.run()
        self.assertTrue(r.ok)
        self.assertEqual(r.provider_calls, 5 + 2)
        self.assertLessEqual(r.sleeps, 5 + 2 + 5)
        self.assertEqual(len(c.sleeps), r.sleeps)
        self.assertTrue(all(d > 0 for d in c.sleeps))

    def test_ten_minute_simulated_run_respects_budgets(self):
        """10 분: 모형 걸음 40 · 도구 걸음 60(시각 흩어짐) · rpm 5 · tpm 1,500 · 가끔 429. 어느 창에서도 예산 안."""
        steps = []
        for i in range(40):
            steps.append(Step(f"m{i}", "plan" if i % 2 else "judge", payload=i, est_tokens=250))
        for i in range(60):
            steps.append(Step(f"t{i}", "read", fn=lambda r: 0, not_before=10.0 * i))
        c, p, s = self.make(steps, {"m": {"rpm": 5, "tpm": 1500}}, provider_kw={"fail_on": (7, 19, 33), "tokens": 250})
        r = s.run()
        self.assertTrue(r.ok, (r.failed, r.parked))
        times = [t for t, _ in p.calls]
        for t in times:
            win = [u for u in times if t - 60 < u <= t]
            self.assertLessEqual(len(win), 5)
            self.assertLessEqual(len(win) * 250, 1500)
        self.assertGreaterEqual(c.t, 9 * 60)
        self.assertLessEqual(c.t, 11 * 60)
        self.assertEqual(r.provider_calls, 43)
        self.assertLessEqual(r.sleeps, 40 + 3 + 60)
        self.assertEqual(len(rows(r, "rate_limit")), 3)

    def test_daily_quota_parks_instead_of_retrying_forever(self):
        steps = [Step("m1", "plan", payload=1), Step("m2", "plan", payload=2), Step("t1", "read", fn=lambda r: 1)]
        c, p, s = self.make(steps, {"m": {"rpm": 10}}, provider_kw={"fail_on": (1,), "retry": None, "per": "Day"})
        r = s.run()
        self.assertEqual((r.failed, r.parked, set(r.done)), ({}, ["m1", "m2"], {"t1"}))
        self.assertEqual((r.provider_calls, r.sleeps), (1, 0))

    def test_other_errors_fail_and_skip_dependents(self):
        steps = [Step("m1", "plan", payload=1), Step("t1", "render", fn=lambda r: 1, after=("m1",)),
                 Step("m2", "plan", payload=2)]
        c, p, s = self.make(steps, {"m": {"rpm": 10}}, provider_kw={"other_on": (1,)})
        r = s.run()
        self.assertEqual((r.failed, r.skipped, set(r.done)), ({"m1": "ValueError"}, {"t1": "m1"}, {"m2"}))

    def test_tool_results_flow_to_later_steps(self):
        seen = []
        steps = [Step("t1", "read", fn=lambda r: "data"),
                 Step("m1", "plan", payload=lambda r: f"prompt with {r['t1']}", after=("t1",)),
                 Step("t2", "render", fn=lambda r: seen.append(r["m1"]["text"]), after=("m1",))]
        c, p, s = self.make(steps, {"m": {"rpm": 10}})
        self.assertTrue(s.run().ok)
        self.assertEqual(seen, ["ok prompt with data"])

    def test_events_are_closed_l0_and_the_ledger_is_written(self):
        from telemetry.event import check
        d = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d)
        steps = [Step("m1", "plan", payload=1), Step("m2", "plan", payload=2), Step("t1", "read", fn=lambda r: 1)]
        c, p, s = self.make(steps, {"m": {"rpm": 1}}, provider_kw={"fail_on": (2,)}, ledger=str(d / "l.jsonl"))
        r = s.run()
        self.assertTrue(r.ok)
        self.assertEqual([e for e in r.events if check(e)], [])
        types = {e["type"] for e in r.events}
        self.assertEqual(types, {"llm.request", "llm.response", "llm.error", "runtime.status", "tool.start", "tool.end"})
        err = [e for e in r.events if e["type"] == "llm.error"]
        self.assertEqual((err[0]["data"]["http_status"], err[0]["data"]["error_code"]), (429, "RATE_LIMITED"))
        self.assertEqual(err[0]["data"]["retry_after_ms"], 7000.0)
        on_disk = [json.loads(x) for x in (d / "l.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(on_disk, json.loads(json.dumps(r.ledger)))
        kinds = [x["kind"] for x in r.ledger]
        for k in ("park", "dispatch", "rate_limit", "verification", "done"):
            self.assertIn(k, kinds)

    def test_verify_each_parked_step_was_dispatched_in_its_window(self):
        steps = [Step(f"m{i}", "plan", payload=i) for i in range(3)]
        c, p, s = self.make(steps, {"m": {"rpm": 1}}, provider_kw={"fail_on": (2,)})
        r = s.run()
        ver = rows(r, "verification")
        self.assertTrue(ver)
        self.assertEqual({(v["result"], v["reason"]) for v in ver}, {("VERIFIED", "MET")})
        self.assertEqual(len(ver), len(rows(r, "park")))

    def test_park_verify_not_dispatched_in_window(self):
        v = ParkVerify("r", grace_s=5)
        v.park("s1", 0.0, 10.0)
        self.assertEqual(v.close(14.0), [])
        (sid, rec), = v.close(15.0)
        self.assertEqual((sid, rec.result, rec.reason), ("s1", "NOT_VERIFIED", "UNMET_AT_CLOSE"))
        self.assertIsNone(v.dispatched("s1", 16.0))
        v.park("s2", 0.0, math.inf)                                   # 하루 할당 -- 지킬 수 없는 창은 적지 않는다
        self.assertEqual(v.pending, {})

    def test_thin_interface_is_public(self):
        """S5: Autonomy 없이 제어기가 바로 쓴다."""
        self.assertIs(rlo.Scheduler, Scheduler)
        self.assertIs(rlo.Governor, Governor)
        c = Clock()
        r = rlo.Scheduler([rlo.Step("a", "plan", payload="x")], rlo.Governor({"m": {"rpm": 1}}, clock=c),
                          lambda payload: {"text": payload}, kinds=KINDS, clock=c, sleep=c.sleep).run()
        self.assertEqual(r.done["a"], {"text": "x"})


RESTART = r"""
import json, sys
from rlo.governor import Governor
from rlo.scheduler import Scheduler, Step
phase, state, t0 = sys.argv[1], sys.argv[2], float(sys.argv[3])
class Clock:
    t = t0
    def __call__(self): return self.t
    def sleep(self, d):
        assert d > 0, d
        self.t += d
c = Clock()
calls = []
def provider(p):
    calls.append([c(), p])
    return {"text": "ok " + p}
steps = [Step("t1", "read", fn=lambda r: "data"),
         Step("m1", "plan", payload=lambda r: "a:" + r["t1"], after=("t1",)),
         Step("m2", "plan", payload="b"),
         Step("t2", "render", fn=lambda r: r["m1"]["text"] + "|" + r["m2"]["text"], after=("m1", "m2")),
         Step("m3", "judge", payload=lambda r: "c:" + r["t2"], after=("t2",))]
s = Scheduler(steps, Governor({"m": {"rpm": 1}}, clock=c), provider,
              kinds={"read": "tool", "render": "tool", "plan": "model", "judge": "model"},
              clock=c, sleep=c.sleep, state=state)
r = s.run(wait=(phase == "second"))
print(json.dumps({"status": r.status, "failed": r.failed, "calls": calls, "done": sorted(r.done), "t": c.t,
                  "results": {k: v for k, v in r.done.items() if isinstance(v, str)}}))
"""


class SavedState(unittest.TestCase):
    """CMD-K12 S6 · D5: 세운 채 저장 -> 새 프로세스에서 읽어 실패 0 으로 끝낸다. status() 는 어느 때나 큐와 같다."""

    def setUp(self):
        self.d = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.d)

    def phase(self, name, t0):
        import os
        import subprocess
        import sys
        script = self.d / "restart.py"
        script.write_text(RESTART, encoding="utf-8")
        env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(pathlib.Path(rlo.__file__).resolve().parent.parent),
                                                           os.environ.get("PYTHONPATH", "")]))
        p = subprocess.run([sys.executable, str(script), name, str(self.d / "state.json"), str(t0)],
                           capture_output=True, text=True, cwd=str(self.d), env=env)
        self.assertEqual(p.returncode, 0, p.stderr)
        return json.loads(p.stdout)

    def test_save_while_parked_then_finish_in_a_new_process(self):
        first = self.phase("first", 0.0)
        self.assertEqual(first["failed"], {})
        self.assertEqual(first["done"], ["m1", "t1"])
        self.assertEqual(first["status"]["parked"], ["m2"])
        self.assertEqual(first["status"]["resumes_in_s"], 60.0)
        self.assertEqual([x["id"] for x in first["status"]["next"]], ["m2", "t2", "m3"])
        self.assertEqual([p for _, p in first["calls"]], ["a:data"])
        saved = json.loads((self.d / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["schema"], "rlo-scheduler-state/1")
        self.assertEqual(saved["steps"]["m2"]["state"], "parked")
        self.assertEqual(saved["governor"]["windows"]["m"], [[0.0, 0]])
        second = self.phase("second", 30.0)                      # 창이 열리기 전에 다시 띄웠다
        self.assertEqual(second["failed"], {})
        self.assertEqual(second["done"], ["m1", "m2", "m3", "t1", "t2"])
        self.assertEqual(second["status"], {"resumes_in_s": None, "done": ["t1", "m1", "m2", "t2", "m3"],
                                            "running": None, "running_all": [], "parked": [], "next": []})
        # 지킴이 창이 되살아났다: 30 초에 다시 띄웠어도 60 초 전에는 부르지 않았다. m1 은 다시 부르지 않았다
        self.assertEqual(second["calls"], [[60.0, "b"], [120.0, "c:ok a:data|ok b"]])

    def test_status_matches_the_queue_at_every_point(self):
        seen = []
        c = Clock()
        g = Governor({"m": {"rpm": 1}}, clock=c)
        holder = {}

        def look(where):
            seen.append((where, holder["s"].status()))

        def provider(p):
            look(("provider", p))
            if p == "p2" and not any(w == ("provider", "p2") for w, _ in seen[:-1]):
                raise Http429("7s")
            return {"text": p}

        steps = [Step("t1", "read", fn=lambda r: look(("tool", "t1"))),
                 Step("m1", "plan", payload="p1"), Step("m2", "plan", payload="p2"),
                 Step("t2", "render", fn=lambda r: look(("tool", "t2")), after=("m2",)),
                 Step("t3", "check", fn=lambda r: look(("tool", "t3")), not_before=30.0)]
        s = Scheduler(steps, g, provider, kinds=KINDS, clock=c, sleep=c.sleep, on_event=lambda row: look(row),
                      max_parallel=1)                      # 하나씩: 도는 걸음이 늘 하나라 어느 때나 정해진다(함께 돌 때는 아래)
        holder["s"] = s
        r = s.run()
        self.assertTrue(r.ok)
        done, parked, kinds = [], [], {"t1": "tool", "m1": "model", "m2": "model", "t2": "tool", "t3": "tool"}
        order = ["t1", "m1", "m2", "t2", "t3"]
        for where, st in seen:
            if isinstance(where, dict):                          # 원장 줄 -- 그 줄까지 다시 세운 큐와 같아야 한다
                row = where
                if row["kind"] == "done":
                    done.append(row["step"])
                    parked = [x for x in parked if x != row["step"]]
                elif row["kind"] == "park":
                    parked = [x for x in parked if x != row["step"]] + [row["step"]]
                self.assertIsNone(st["running"])
                if row["kind"] == "park":
                    self.assertEqual(st["resumes_in_s"], row["wait_s"])
                running = None
            else:
                running = where[1] if where[0] == "tool" else {"p1": "m1", "p2": "m2"}[where[1]]
                self.assertEqual(st["running"], running)
            self.assertEqual(st["done"], [x for x in order if x in done])
            self.assertEqual(st["parked"], [x for x in order if x in parked])
            self.assertEqual(st["next"], [{"id": x, "kind": kinds[x]} for x in order if x not in done and x != running])
            if not st["parked"]:
                self.assertIsNone(st["resumes_in_s"])
        self.assertGreater(len(seen), 15)

    def test_unsaved_results_rerun_and_mismatched_steps_are_refused(self):
        c = Clock()
        state = str(self.d / "s.json")
        mk = lambda steps: Scheduler(steps, Governor({"m": {"rpm": 1}}, clock=c), lambda p: {"text": p},
                                     kinds=KINDS, clock=c, sleep=c.sleep, state=state)
        ran = []
        steps = lambda: [Step("t1", "read", fn=lambda r: ran.append(1) or object()),
                         Step("m1", "plan", payload="x"), Step("m2", "plan", payload="y")]
        r = mk(steps()).run(wait=False)
        self.assertEqual((r.parked, len(ran)), (["m2"], 1))
        s2 = mk(steps())
        self.assertIn("rerun_after_restart", [x["kind"] for x in s2.rows])      # object() 는 JSON 이 아니다
        c.t = 60
        self.assertTrue(s2.run().ok)
        self.assertEqual(len(ran), 2)
        with self.assertRaises(StepKindError):
            mk([Step("t1", "check", fn=lambda r: 1), Step("m1", "plan", payload="x"), Step("m2", "plan", payload="y")])

    def test_governor_round_trip(self):
        c = Clock(5.0)
        g = Governor({"a": {"rpm": 2}, "b": {"tpm": 10}}, clock=c)
        g.try_acquire(model="a")
        g.on_rate_limit(Http429(None, "Day"), model="b")
        d = json.loads(json.dumps(g.to_dict()))
        h = Governor({"a": {"rpm": 2}, "b": {"tpm": 10}}, clock=c)
        h.load(d)
        self.assertEqual((h.wait_s(model="a"), h.wait_s(model="a", calls=2)), (0.0, 60.0))
        self.assertTrue(math.isinf(h.wait_s(model="b")))
        with self.assertRaises(ValueError):
            Governor({"a": {"rpm": 2}}, clock=c).load(d)


class ConcurrentTools(unittest.TestCase):
    """CMD-K12 S7 · D6: 준비된 도구 걸음은 크기가 정해진 풀에서 함께 돈다(실제 스레드 · 실제 시간)."""

    DT = 0.3                                                   # 걸음 하나가 자는 실제 시간

    def run_tools(self, n=4, max_parallel=4, extra=(), budgets=None, provider=None, **kw):
        import time as _t
        c = Clock()
        g = Governor(budgets or {"m": {"rpm": 1}}, clock=c)
        p = provider or Provider(c)
        tools = [Step(f"t{i}", "read", fn=lambda r, i=i: (_t.sleep(self.DT), i)[1]) for i in range(n)]
        s = Scheduler([*extra, *tools], g, p, kinds=KINDS, clock=c, sleep=c.sleep, max_parallel=max_parallel, **kw)
        t0 = _t.monotonic()
        r = s.run()
        return r, _t.monotonic() - t0, c, p

    def test_four_tools_take_about_one_step_not_four(self):
        r, wall, _, _ = self.run_tools()
        self.assertTrue(r.ok)
        self.assertEqual({k: r.done[k] for k in ("t0", "t1", "t2", "t3")}, {"t0": 0, "t1": 1, "t2": 2, "t3": 3})
        self.assertLess(wall, 2.5 * self.DT)                   # 넷이 함께: 한 걸음 시간 남짓(하나씩이면 4 배)
        r1, wall1, _, _ = self.run_tools(max_parallel=1)
        self.assertGreaterEqual(wall1, 4 * self.DT * 0.95)    # max_parallel=1 은 예전처럼 하나씩

    def test_pool_is_bounded(self):
        import threading
        live, peak, lock = [0], [0], threading.Lock()

        def tool(r):
            import time as _t
            with lock:
                live[0] += 1
                peak[0] = max(peak[0], live[0])
            _t.sleep(0.1)
            with lock:
                live[0] -= 1
        c = Clock()
        s = Scheduler([Step(f"t{i}", "read", fn=tool) for i in range(9)], Governor({"m": {"rpm": 1}}, clock=c),
                      Provider(c), kinds=KINDS, clock=c, sleep=c.sleep, max_parallel=3)
        self.assertTrue(s.run().ok)
        self.assertEqual(peak[0], 3)

    def test_parked_model_step_does_not_hold_them(self):
        """m1 이 예산을 다 쓰고 m2 가 세워져도 도구 넷은 바로 함께 돈다(가짜 시계는 0 초에 머문다)."""
        extra = [Step("m1", "plan", payload="a"), Step("m2", "plan", payload="b")]
        r, wall, c, p = self.run_tools(extra=extra)
        self.assertTrue(r.ok)
        done_at = {x["step"]: x["at_ms"] for x in rows(r, "done")}
        self.assertEqual([done_at[f"t{i}"] for i in range(4)], [0.0] * 4)
        self.assertEqual(done_at["m2"], 60000.0)
        park = rows(r, "park")[0]
        starts = [x for x in r.ledger if x["kind"] == "start"]
        self.assertEqual(len(starts), 4)
        self.assertLess(wall, 2.5 * self.DT)

    def test_model_step_does_not_wait_for_unrelated_tools(self):
        """도구 넷이 도는 동안 모형 걸음을 보낸다: 모형 응답이 도구보다 먼저 적힌다."""
        extra = [Step("m1", "plan", payload="a")]
        r, _, _, _ = self.run_tools(extra=extra, budgets={"m": {"rpm": 10}})
        kinds = [(x["kind"], x["step"]) for x in r.ledger if x["kind"] in ("done", "start")]
        self.assertLess(kinds.index(("done", "m1")), min(kinds.index(("done", f"t{i}")) for i in range(4)))

    def test_one_failure_does_not_cancel_the_others(self):
        import time as _t

        def boom(r):
            _t.sleep(0.05)
            raise RuntimeError("x")
        c = Clock()
        steps = [Step("bad", "check", fn=boom)] + [Step(f"t{i}", "read", fn=lambda r, i=i: (_t.sleep(0.2), i)[1])
                                                     for i in range(3)]
        r = Scheduler(steps, Governor({"m": {"rpm": 1}}, clock=c), Provider(c), kinds=KINDS, clock=c,
                      sleep=c.sleep).run()
        self.assertEqual(r.failed, {"bad": "RuntimeError"})
        self.assertEqual(sorted(r.done), ["t0", "t1", "t2"])
        order = [x["step"] for x in r.ledger if x["kind"] in ("done", "failed")]
        self.assertEqual(order[0], "bad")                       # 끝난 차례로 적는다

    def test_per_step_timeout(self):
        import time as _t
        c = Clock()
        steps = [Step("slow", "read", fn=lambda r: _t.sleep(1.0), timeout_s=0.1),
                 Step("fast", "read", fn=lambda r: (_t.sleep(0.2), "ok")[1])]
        t0 = _t.monotonic()
        r = Scheduler(steps, Governor({"m": {"rpm": 1}}, clock=c), Provider(c), kinds=KINDS, clock=c,
                      sleep=c.sleep).run()
        self.assertEqual((r.failed, r.done), ({"slow": "timeout"}, {"fast": "ok"}))
        self.assertLess(_t.monotonic() - t0, 0.8)               # 1 초 걸음을 기다리지 않았다
        ends = [e for e in r.events if e["type"] == "tool.end"]
        self.assertTrue(any(e["data"].get("timed_out") for e in ends))

    def test_status_while_running_and_events_per_step(self):
        import threading
        gate, seen = threading.Event(), []
        c = Clock()
        holder = {}

        def tool(r):
            gate.wait(2)
            return 1

        def on_event(row):
            seen.append((row["kind"], row["step"], holder["s"].status()))
            if row["kind"] == "start" and row["step"] == "t2":
                gate.set()
        s = Scheduler([Step(f"t{i}", "read", fn=tool) for i in range(3)], Governor({"m": {"rpm": 1}}, clock=c),
                      Provider(c), kinds=KINDS, clock=c, sleep=c.sleep, on_event=on_event)
        holder["s"] = s
        r = s.run()
        self.assertTrue(r.ok)
        (_, _, st), = [x for x in seen if x[:2] == ("start", "t2")]
        self.assertEqual((st["running"], st["running_all"], st["next"]), ("t0", ["t0", "t1", "t2"], []))
        ev = [e["type"] for e in r.events]
        self.assertEqual((ev.count("tool.start"), ev.count("tool.end")), (3, 3))

    def test_restart_reruns_only_the_running_steps(self):
        d = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d)
        state = str(d / "s.json")
        c = Clock()
        g = Governor({"m": {"rpm": 1}}, clock=c)
        s = Scheduler([Step("t0", "read", fn=lambda r: 0), Step("t1", "read", fn=lambda r: 1)], g, Provider(c),
                      kinds=KINDS, clock=c, sleep=c.sleep, state=state)
        s.state["t0"], s.results["t0"] = "done", 0                # 저장 순간: t0 끝남, t1 도는 중
        s.state["t1"] = "running"
        s.save()
        ran = []
        s2 = Scheduler([Step("t0", "read", fn=lambda r: ran.append("t0")), Step("t1", "read", fn=lambda r: ran.append("t1"))],
                       Governor({"m": {"rpm": 1}}, clock=c), Provider(c), kinds=KINDS, clock=c, sleep=c.sleep, state=state)
        self.assertTrue(s2.run().ok)
        self.assertEqual(ran, ["t1"])

    def test_max_parallel_is_checked(self):
        c = Clock()
        for bad in (0, -1, 2.5):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                Scheduler([], Governor({"m": {"rpm": 1}}, clock=c), Provider(c), kinds=KINDS, max_parallel=bad)


class AutonomyGoverned(unittest.TestCase):
    """CMD-K12 S4: Autonomy(governor=) -- 예산이 비었거나 429 면 미룬 결과, tick · close_windows 가 다시 보낸다."""

    def build(self, fail_on=(), rpm=1, retry="7s"):
        from ms.providers import ProviderError, make_provider
        from rlo.example import world
        spec, obs, tools = world()
        inner = make_provider("sim-claude")
        orig, self.calls = inner.generate, []
        c = self.c = Clock(1000.0)

        def gen(req):
            self.calls.append(c())
            if len(self.calls) in fail_on:
                raise ProviderError("429", status=429, body=gemini_429(retry), headers={})
            return orig(req)
        inner.generate = gen
        now = spec["now"]
        self.d = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.d)
        a = rlo.Autonomy.from_spec(spec, obs, clock=lambda: now, actions=tools, llm=inner,
                                   governor=Governor({"m": {"rpm": rpm}}, clock=c), ledger=str(self.d / "led.jsonl"),
                                   governor_sleep=c.sleep)
        a.open_session("s", {"token_budget": 100000})
        return a, spec

    def test_empty_budget_defers_and_tick_redispatches(self):
        from rlo.example import TASK
        a, spec = self.build()
        self.assertEqual(a.handle(TASK, queries=spec["queries"]).outcome, "executed")
        r = a.handle(TASK, queries=spec["queries"])
        self.assertEqual((r.outcome, r.deferred, r.wait_s, r.step_id), ("deferred", True, 60.0, "step-1"))
        self.assertEqual(len(self.calls), 1)                     # 예산이 없으면 부르지 않았다
        self.assertEqual(a.tick(), [])                           # 창이 아직이다
        self.c.t += 60
        out = a.tick()
        self.assertEqual([o["when"] for o in out], ["deferred_dispatch", "redispatch"])
        self.assertEqual(out[0]["record"]["result"], "VERIFIED")
        self.assertEqual((out[1]["step_id"], out[1]["result"].outcome), ("step-1", "executed"))
        self.assertEqual(a.parked, [])
        led = [json.loads(x)["kind"] for x in (self.d / "led.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertIn("step_park", led)
        self.assertIn("step_dispatch", led)

    def test_429_defers_instead_of_failing(self):
        from rlo.example import TASK
        a, spec = self.build(fail_on=(1,), rpm=5)
        r = a.handle(TASK, queries=spec["queries"])
        self.assertEqual((r.outcome, r.wait_s), ("deferred", 7.0))
        self.c.t += 7
        out = a.close_windows()                                  # close_windows 도 다시 보낸다
        self.assertEqual([o["result"].outcome for o in out if o["when"] == "redispatch"], ["executed"])
        self.assertEqual([o["record"]["result"] for o in out if o["when"] == "deferred_dispatch"], ["VERIFIED"])

    def test_new_requests_queue_behind_parked_ones(self):
        from rlo.example import TASK
        a, spec = self.build()
        a.handle(TASK, queries=spec["queries"])
        first = a.handle(TASK, queries=spec["queries"])
        second = a.handle(TASK, queries=spec["queries"])
        self.assertEqual([first.step_id, second.step_id], ["step-1", "step-2"])
        self.c.t += 60
        out = a.tick()
        self.assertEqual([(o["step_id"], o["result"].outcome) for o in out if o["when"] == "redispatch"],
                         [("step-1", "executed")])                # 예산이 다시 비면 거기서 멈춘다(부르지 않는다)
        self.assertEqual([p["step_id"] for p in a.parked], ["step-2"])
        self.assertEqual(len(self.calls), 2)
        self.c.t += 60
        out = a.tick()
        self.assertEqual([(o["step_id"], o["result"].outcome) for o in out if o["when"] == "redispatch"],
                         [("step-2", "executed")])
        # step-2 의 실행은 LLM 을 두 번 부른다(rpm 1 보다 많다). 실행 안의 둘째 부름은 걸음을 다시 보내지 않고
        # (앞 판을 되풀이하게 된다) 창이 열릴 때까지 한 번 잔다 -- 그래서 rpm 1 에서도 끝난다
        self.assertEqual(a._gp.inline_waits, [60.0])
        self.assertEqual(self.c.sleeps, [60.0])
        self.assertEqual(a.parked, [])

    def test_new_request_never_overtakes_a_reparked_step(self):
        """다시 보낸 걸음이 대기 0 초를 선언한 429 로 다시 세워져도(창에는 자리가 있다) 새 요청은 그 뒤에 선다."""
        from rlo.example import TASK
        a, spec = self.build(fail_on=(2, 3), rpm=10, retry="0s")
        self.assertEqual(a.handle(TASK, queries=spec["queries"]).outcome, "executed")
        first = a.handle(TASK, queries=spec["queries"])          # 둘째 부름이 429 -- step-1
        self.assertEqual((first.outcome, first.step_id, first.wait_s), ("deferred", "step-1", 0.0))
        third = a.handle(TASK, queries=spec["queries"])          # tick 이 step-1 을 다시 보냄 -> 셋째 부름도 429 -> 다시 세움
        self.assertEqual((third.outcome, third.step_id), ("deferred", "step-2"))
        self.assertEqual([p["step_id"] for p in a.parked], ["step-1", "step-2"])
        self.assertEqual(len(self.calls), 3)                     # 새 요청은 부르지 않았다

    def test_without_governor_nothing_changes(self):
        from rlo.example import one_turn
        a, r, _ = one_turn("shadow")
        self.assertIsNone(a.governor)
        self.assertFalse(r.deferred)
        self.assertEqual(a.tick(), [])

    def test_governed_provider_raises_rate_deferred_when_empty(self):
        from ms.providers import make_provider
        from rlo.autonomy import governed_provider
        c = Clock()
        g = Governor({"m": {"rpm": 1}}, clock=c)
        gp = governed_provider(make_provider("sim-claude"), g)
        g.try_acquire()
        with self.assertRaises(RateDeferred):
            gp.generate(type("Req", (), {"prompt": type("P", (), {"text": lambda self: "x"})()})())
        self.assertEqual(gp.deferred, 60.0)


if __name__ == "__main__":
    unittest.main()
