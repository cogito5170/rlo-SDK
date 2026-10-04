"""rlo.optimize -- 가짜 모형으로만(CMD-K15 S3 · S4, D2). 네트워크를 쓰지 않는다: 시험 동안 socket 연결은 실패한다."""
import hashlib
import json
import math
import os
import pathlib
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from rlo.governor import Governor
from rlo.optimize import Metric, RunMismatch, cache_key, optimize

ROOT = pathlib.Path(__file__).resolve().parent.parent
SPEC = {"schema": "prompt-spec/1", "id": "demo.label", "version": "1", "goal": "Label the report a or b.",
        "inputs": [{"name": "report", "term": "Observation", "basis": "OBSERVED"}],
        "output": {"term": "Opinion", "format": "json", "fields": [{"name": "label", "type": "enum", "values": ["a", "b"]}]},
        "rules": ["one label"]}
TRAIN, HELD = ["t1", "t2", "t3", "t4"], ["h1", "h2", "h3", "h4"]
LABELS = {c: "ab"[i % 2] for i, c in enumerate(TRAIN + HELD)}
CASES = [{"id": c, "inputs": {"report": f"case:{c}"}, "label": LABELS[c]} for c in TRAIN + HELD]
SPLIT = {"train": TRAIN, "held_out": HELD}
# 변형마다 맞히는 사례(가짜 모형의 표). base: train 2/4 · held 2/4
RIGHT = {"base": {"t1", "t2", "h1", "h2"},
         "good": set(TRAIN + HELD),                          # 어디서나 낫다
         "trainonly": set(TRAIN) | {"h1", "h2"},              # train 에서만 낫다
         "slight": {"t1", "t2", "t3", "h1", "h2", "h3"}}      # held 에서 0.25 낫다
V = {n: {"id": n, "wording": {"intro": f"V={n}\n"}} for n in RIGHT if n != "base"}


def exact(chk, case):
    return 1.0 if chk.ok and chk.opinion.value["label"] == case["label"] else 0.0


EXACT = Metric("exact", exact)


class FakeModel:
    """프롬프트의 변형 표지(V=)와 사례 표지(case:)로 답한다. 부름을 센다."""

    def __init__(self, die_at=None, log=None):
        self.calls, self.die_at, self.log = [], die_at, log

    def __call__(self, text):
        v = text.split("\n", 1)[0][2:] if text.startswith("V=") else "base"
        cid = text.rsplit("case:", 1)[1].split("\n", 1)[0]
        if self.die_at is not None and len(self.calls) + 1 == self.die_at:
            os._exit(9)                                       # 실행이 도중에 죽는다(답을 내기 전)
        self.calls.append((hashlib.sha256(text.encode()).hexdigest(), cid))
        if self.log:
            with open(self.log, "a") as f:
                f.write(json.dumps(self.calls[-1]) + "\n")
        right = cid in RIGHT[v]
        label = LABELS[cid] if right else ("b" if LABELS[cid] == "a" else "a")
        return {"text": f'```json\n{{"label": "{label}"}}\n```', "usage": {"input_tokens": 10, "output_tokens": 3}}


def gov(calls=100, rpm=None):
    return Governor({"fake": {"rpm": rpm, "calls": calls}}, clock=lambda: 0.0)


class NoNetwork(unittest.TestCase):
    def setUp(self):
        self.d = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.d)
        p = mock.patch.object(socket.socket, "connect", side_effect=OSError("network is off in tests"))
        p.start()
        self.addCleanup(p.stop)

    def run_opt(self, candidates, model=None, margin=0.2, governor=None, d="run", **kw):
        model = model or FakeModel()
        r = optimize(SPEC, candidates, CASES, model=model, metrics=kw.pop("metrics", [EXACT]), split=kw.pop("split", SPLIT),
                     governor=governor or gov(), run_dir=self.d / d, margin=margin, sleep=kw.pop("sleep", lambda s: None),
                     **kw)
        return r, model


class Adoption(NoNetwork):
    def test_better_on_held_out_is_adopted_above_the_margin(self):
        r, m = self.run_opt([V["good"]], margin=0.2)
        self.assertEqual((r.status, r.adopted, r.chosen), ("done", True, "good"))
        self.assertEqual(r.train, {"base": 0.5, "good": 1.0})
        self.assertEqual(r.held_out, {"base": 0.5, "good": 1.0})
        self.assertEqual(len(m.calls), 8 + 8)                 # train 2 변형 × 4, held-out 2 × 4

    def test_a_gain_below_the_margin_keeps_the_base(self):
        r, _ = self.run_opt([V["slight"]], margin=0.3)       # held 이득 0.25 < 0.3
        self.assertEqual((r.adopted, r.chosen, r.candidate), (False, "base", "slight"))
        self.assertAlmostEqual(r.held_out["slight"] - r.held_out["base"], 0.25)
        r, _ = self.run_opt([V["slight"]], margin=0.25, d="run2")
        self.assertTrue(r.adopted)                           # 여유폭과 같으면 채택(>=)

    def test_better_only_on_train_is_not_adopted(self):
        r, _ = self.run_opt([V["trainonly"]], margin=0.1)
        self.assertEqual(r.train["trainonly"], 1.0)
        self.assertEqual(r.held_out, {"base": 0.5, "trainonly": 0.5})
        self.assertFalse(r.adopted)

    def test_scores_use_only_their_split(self):
        self.run_opt([V["good"]])
        rows = [json.loads(x) for x in (self.d / "run" / "ledger.jsonl").read_text().splitlines()]
        cases = [x for x in rows if x["kind"] == "case"]
        self.assertEqual({x["case"] for x in cases if x["split"] == "train"}, set(TRAIN))
        self.assertEqual({x["case"] for x in cases if x["split"] == "held_out"}, set(HELD))

    def test_split_must_not_overlap(self):
        for split in ({"train": TRAIN, "held_out": HELD + ["t1"]}, {"train": TRAIN, "held_out": []},
                      {"train": TRAIN, "held_out": ["zz"]}, {"train": TRAIN}):
            with self.subTest(split=split), self.assertRaises(ValueError):
                self.run_opt([V["good"]], split=split)

    def test_no_candidate_better_on_train_spends_no_held_out_calls(self):
        worse = {"id": "worse", "wording": {"intro": "V=base\n"}}
        r, m = self.run_opt([worse])
        self.assertEqual((r.adopted, r.candidate, r.held_out), (False, None, {}))
        self.assertEqual(len(m.calls), 8)


class Cap(NoNetwork):
    def test_the_governor_cap_stops_the_run_at_exactly_n_calls(self):
        for n in (1, 5, 12):
            with self.subTest(n=n):
                r, m = self.run_opt([V["good"]], governor=gov(calls=n), d=f"cap{n}")
                self.assertEqual(len(m.calls), n)
                self.assertEqual((r.status, r.adopted, r.chosen, r.calls), ("capped", False, "base", n))

    def test_the_cap_holds_across_a_resume(self):
        r, m = self.run_opt([V["good"]], governor=gov(calls=5))
        self.assertEqual((r.status, len(m.calls)), ("capped", 5))
        r, m = self.run_opt([V["good"]], governor=gov(calls=5))          # 같은 run_dir · 같은 상한: 쓴 수를 되살린다
        self.assertEqual((r.status, len(m.calls), r.calls, r.cache_hits), ("capped", 0, 5, 5))
        r, m = self.run_opt([V["good"]], governor=gov(calls=16))         # 다음 날 상한을 올려 이어 간다
        self.assertEqual((r.status, r.adopted, len(m.calls), r.calls), ("done", True, 11, 16))

    def test_a_run_without_a_cap_is_refused(self):
        with self.assertRaises(ValueError):
            self.run_opt([V["good"]], governor=Governor({"fake": {"rpm": 5}}, clock=lambda: 0.0))

    def test_rate_window_waits_then_pauses(self):
        slept = []
        r, m = self.run_opt([V["good"]], governor=Governor({"fake": {"rpm": 4, "calls": 100}}, clock=lambda: 0.0),
                            sleep=slept.append, max_wait_s=30)
        self.assertEqual((r.status, len(m.calls)), ("paused", 4))   # 시계가 멈춘 가짜: 60 초 창 > 30 초 -- 멈춘다
        self.assertEqual(slept, [])

    def test_governor_calls_budget(self):
        g = Governor({"m": {"calls": 2}}, clock=lambda: 0.0)
        self.assertTrue(g.try_acquire().ok and g.try_acquire().ok)
        self.assertFalse(g.try_acquire().ok)
        self.assertTrue(math.isinf(g.wait_s()))
        self.assertEqual(g.remaining_calls(), 0)
        h = Governor({"m": {"calls": 3}}, clock=lambda: 0.0)
        h.load(json.loads(json.dumps(g.to_dict())))
        self.assertEqual(h.remaining_calls(), 1)
        with self.assertRaises(ValueError):
            Governor({"m": {"calls": 0}})
        with self.assertRaises(ValueError):
            h.load(dict(g.to_dict(), used={"m": -1}))


class Cache(NoNetwork):
    def test_a_cache_hit_makes_no_call(self):
        r1, m1 = self.run_opt([V["good"]])
        r2, m2 = self.run_opt([V["good"]])                   # 같은 run_dir
        self.assertEqual(len(m2.calls), 0)
        self.assertEqual(r2.cache_hits, 16)
        self.assertEqual(r1.summary(), r2.summary())

    def test_the_key_includes_the_case(self):
        twins = [{"id": "x1", "inputs": {"report": "case:t1"}, "label": "a"},
                 {"id": "x2", "inputs": {"report": "case:t1"}, "label": "a"},     # 같은 프롬프트 글, 다른 사례
                 {"id": "y1", "inputs": {"report": "case:h1"}, "label": "a"}]
        m = FakeModel()
        optimize(SPEC, [V["good"]], twins, model=m, metrics=[EXACT], split={"train": ["x1", "x2"], "held_out": ["y1"]},
                 governor=gov(), run_dir=self.d / "tw", margin=0.0)
        self.assertEqual([c for _, c in m.calls].count("t1"), 4)   # 두 사례 × 두 변형 -- 사례가 다르면 따로 부른다
        self.assertNotEqual(cache_key("p", "x1"), cache_key("p", "x2"))


RESUME = """
import json, sys
sys.path.insert(0, {root!r})
from tests.test_optimize import FakeModel, SPEC, CASES, SPLIT, EXACT, V, gov
from rlo.optimize import optimize
die = int(sys.argv[2]) or None
r = optimize(SPEC, [V["slight"], V["good"], V["trainonly"]], CASES, model=FakeModel(die, sys.argv[3]), metrics=[EXACT],
             split=SPLIT, governor=gov(calls=40), run_dir=sys.argv[1], margin=0.2)
print(json.dumps(r.summary(), sort_keys=True))
"""


class Resume(NoNetwork):
    def go(self, run_dir, die, log):
        script = self.d / "resume.py"
        script.write_text(RESUME.format(root=str(ROOT)), encoding="utf-8")
        return subprocess.run([sys.executable, str(script), str(run_dir), str(die), str(log)], capture_output=True,
                              text=True, cwd=str(ROOT))

    def test_killed_midway_resumes_to_the_same_result_with_no_repeated_calls(self):
        full = self.go(self.d / "full", 0, self.d / "full.log")
        self.assertEqual(full.returncode, 0, full.stderr)
        calls_full = (self.d / "full.log").read_text().splitlines()
        for die in (1, 9, 17):
            with self.subTest(die=die):
                run_dir, log = self.d / f"k{die}", self.d / f"k{die}.log"
                p = self.go(run_dir, die, log)
                self.assertEqual(p.returncode, 9)                       # 죽었다
                q = self.go(run_dir, 0, log)
                self.assertEqual(q.returncode, 0, q.stderr)
                self.assertEqual(json.loads(q.stdout), json.loads(full.stdout))
                calls = log.read_text().splitlines()
                self.assertEqual(len(calls), len(calls_full))
                self.assertEqual(len(set(calls)), len(calls))           # 같은 (프롬프트, 사례) 를 두 번 부르지 않았다
                rec = json.loads((run_dir / "run.json").read_text())
                self.assertEqual((rec["segments"], rec["calls"], rec["status"]), (1, len(calls_full), "done"))
        self.assertTrue(json.loads(full.stdout)["adopted"])

    def test_a_changed_setup_is_refused(self):
        self.run_opt([V["good"]], margin=0.2)
        with self.assertRaises(RunMismatch):
            self.run_opt([V["good"]], margin=0.3)
        with self.assertRaises(RunMismatch):
            self.run_opt([V["slight"]], margin=0.2)


class Records(NoNetwork):
    def test_telemetry_per_call_and_per_run(self):
        from telemetry.event import check
        r, m = self.run_opt([V["good"]])
        self.run_opt([V["good"]])                                     # 둘째 토막: 캐시만
        evs = [json.loads(x) for x in (self.d / "run" / "l0.jsonl").read_text().splitlines()]
        kinds = [e["type"] for e in evs]
        self.assertEqual(kinds.count("llm.request"), 16)
        self.assertEqual(kinds.count("llm.response"), 16)
        self.assertEqual((kinds.count("run.start"), kinds.count("run.end")), (2, 2))
        self.assertEqual(len({e["run_id"] for e in evs}), 2)          # 토막마다 run_id 가 다르다(seq 가 겹치지 않게)
        self.assertEqual([check(e) for e in evs if check(e)], [])
        req = next(e for e in evs if e["type"] == "llm.request")
        self.assertEqual((req["data"]["provider"], req["data"]["model"]), ("injected", "fake"))
        rows = [json.loads(x) for x in (self.d / "run" / "ledger.jsonl").read_text().splitlines()]
        self.assertEqual(sum(x["kind"] == "case" for x in rows), 32)
        self.assertEqual([x["status"] for x in rows if x["kind"] == "run"], ["done", "done"])
        text = (self.d / "run" / "ledger.jsonl").read_text() + (self.d / "run" / "l0.jsonl").read_text()
        self.assertNotIn("case:t1", text)                              # 프롬프트 · 답 글은 기록에 없다

    def test_it_writes_nothing_but_its_run_dir(self):
        before = set(ROOT.rglob("*"))
        r, _ = self.run_opt([V["good"]])
        self.assertEqual(sorted(p.name for p in (self.d / "run").iterdir()), ["cache.jsonl", "l0.jsonl", "ledger.jsonl",
                                                                                "run.json"])
        self.assertEqual({p for p in set(ROOT.rglob("*")) - before if "__pycache__" not in str(p)}, set())


class Metrics(NoNetwork):
    def test_an_llm_only_metric_is_refused(self):
        judge = Metric("judge", lambda chk, case: 1.0, kind="llm_judge")
        with self.assertRaises(ValueError):
            self.run_opt([V["good"]], metrics=[judge])
        r, _ = self.run_opt([V["good"]], metrics=[EXACT, judge])     # 결정론 지표와 함께면 받는다
        self.assertEqual(r.train["base"], 0.75)
        for bad in ([], [Metric("x", exact, kind="vibes")], [Metric("bad name", exact)]):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.run_opt([V["good"]], metrics=bad, d="m2")

    def test_scores_must_be_in_range(self):
        with self.assertRaises(ValueError):
            self.run_opt([V["good"]], metrics=[Metric("big", lambda c, k: 2.0)])

    def test_variants_are_checked_before_any_call(self):
        m = FakeModel()
        with self.assertRaises(ValueError):
            self.run_opt([{"id": "bad", "rules": []}], model=m)
        with self.assertRaises(ValueError):
            self.run_opt([{"id": "base"}], model=m)                     # base 와 id 가 겹친다
        self.assertEqual(m.calls, [])


if __name__ == "__main__":
    unittest.main()
