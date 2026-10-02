"""입구 한 바퀴(BD-120 (1)) -- 결정(DC 길) → Guard → 실행기 → VERIFY, shadow · enforce. 깔린 패키지만 쓴다(옆 저장소 경로 없음)."""
import inspect
import unittest
from unittest import mock

from rlo import Autonomy, example


class Loop(unittest.TestCase):
    def test_one_turn_both_modes(self):
        for mode in ("shadow", "enforce"):
            with self.subTest(mode):
                a, r, events = example.one_turn(mode)
                self.assertEqual(r.outcome, "executed")
                self.assertEqual([g["guard"]["verdict"] for g in r.guards], ["ALLOW"])
                self.assertEqual((a.runtime.guard_mode, r.guards[0]["guard"]["mode"]), (mode, mode))
                (x,) = r.executions
                self.assertEqual((x["command"]["action"], x["command"]["target"]), ("throttle", "srv07"))
                self.assertEqual(x["command"]["decision_ref"], r.decision_id)
                (v,) = r.verifications
                self.assertEqual((v["record"]["result"], v["record"]["reason"]), ("VERIFIED", "MET"))
                acts = [e["type"] for e in events if e["type"].startswith(("action.", "tool."))]
                self.assertEqual(acts, ["action.dispatch", "action.result"])          # tool.* 0 -- 한 사실 한 사건
                disp = next(e for e in events if e["type"] == "action.dispatch")
                self.assertEqual(disp["data"]["action_ref"], x["command"]["command_id"])
                self.assertIsNone(a.runtime.ledger_path)                               # 원장을 주지 않으면 쓰지 않는다

    def test_example_main_is_green(self):
        with mock.patch("builtins.print"):
            self.assertEqual(example.main(), 0)

    def test_handle_needs_a_session(self):
        spec, obs, _ = example.world()
        from ms.providers import make_provider
        a = Autonomy.from_spec(spec, obs, clock=lambda: spec["now"], llm=make_provider("sim-claude"))
        with self.assertRaises(ValueError):
            a.handle("x")


class NoSnapshotPath(unittest.TestCase):
    """SDK 는 snapshot 길을 내지 않는다(BD-120 (1)): 기본이 DC 길이고, 끄는 자리가 없고, 꺼지면 handle 이 거절한다."""

    def test_dc_path_is_the_default(self):
        _, r, _ = example.one_turn("shadow")
        self.assertEqual(r.raw["decision"]["state_source"]["kind"], "state_reader")
        self.assertTrue(r.raw["decision"]["state_source"]["id"].startswith("dc-"))

    def test_no_knob_turns_the_dc_path_off(self):
        params = set(inspect.signature(Autonomy.__init__).parameters)
        self.assertFalse({"state_reader", "snapshot", "state"} & params)

    def test_handle_refuses_without_a_decision_context(self):
        a, _, _ = example.one_turn("shadow")
        a.runtime.state_reader = None
        with self.assertRaises(RuntimeError):
            a.handle(example.TASK, queries=example.world()[0]["queries"])


class GuardAndHealthAreRequired(unittest.TestCase):
    """MS 런타임은 guard · 실행기 · VERIFY 가 없으면 조용히 빼고 돈다. SDK 는 그 갈래를 막는다(BD-120 (3)) -- shadow 에서도."""

    def build(self):
        spec, obs, tools = example.world()
        from ms.providers import make_provider
        return Autonomy.from_spec(spec, obs, clock=lambda: spec["now"], actions=tools, llm=make_provider("sim-claude"))

    def test_built_runtime_has_all_three(self):
        rt = self.build().runtime
        self.assertEqual(rt.guard_mode, "shadow")                                      # 기본은 shadow(BD-118)
        self.assertIsNotNone(rt.guard)
        self.assertIsNotNone(rt.dispatch)
        self.assertIsNotNone(rt.verifier)

    def test_refuses_to_stand_without_each(self):
        for target in ("ms.guard_shadow.available", "ms.dispatch.available", "ms.verify.available"):
            with self.subTest(target), mock.patch(target, return_value=False):
                with self.assertRaises(ImportError):
                    self.build()


if __name__ == "__main__":
    unittest.main()
