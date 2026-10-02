"""설정 예 둘(examples/) -- Claude Code 명령 훅 설정 · Agent SDK 콜백. 저장소 소스에서만 돈다(examples/ 는 설치되지 않는다).

claude-agent-sdk 는 이 저장소의 의존이 아니다(OQ-19). 그래서 Agent SDK 예는 문서의 이름(ClaudeAgentOptions · HookMatcher)만 가진
대역 모듈로 불러, 거는 사건과 콜백이 맞는지만 본다. 실제 Agent SDK · Claude Code 실행으로는 재보지 않았다.
"""
import dataclasses
import importlib.util
import json
import os
import pathlib
import shlex
import sys
import types
import unittest

from rlo.example_hooks import data

ROOT = pathlib.Path(os.environ.get("RLO_REPO") or pathlib.Path(__file__).resolve().parent.parent)
EX = ROOT / "examples"


@unittest.skipUnless(EX.is_dir(), "examples/ 가 옆에 없다(소스가 아닌 곳에서 돎) -- RLO_REPO")
class ClaudeCodeSettings(unittest.TestCase):
    def setUp(self):
        self.s = json.loads((EX / "claude_code_settings.json").read_text(encoding="utf-8"))["hooks"]

    def test_events_and_matchers(self):
        self.assertEqual(set(self.s), {"PreToolUse", "Stop", "SessionEnd"})          # PostToolUse 는 거두지 않으니 걸 일이 없다
        self.assertEqual(self.s["PreToolUse"][0]["matcher"], "*")
        self.assertNotIn("matcher", self.s["Stop"][0])                              # Stop 은 matcher 를 받지 않는다(문서)

    def test_commands_parse_with_the_real_cli(self):
        from rlo.hooks import _parser
        for event, entries in self.s.items():
            for h in entries[0]["hooks"]:
                self.assertEqual(h["type"], "command")
                argv = shlex.split(h["command"])
                self.assertEqual(argv[:3], ["python", "-m", "rlo.hooks"])
                a = _parser().parse_args(argv[3:])
                self.assertEqual(a.mode, "shadow")                                  # 예의 기본은 shadow(BD-118)
                self.assertIsNone(a.now_ms)                                         # 실제 훅은 지금을 고정하지 않는다


try:
    import llmsensor  # noqa: F401
    SENSOR = True
except ImportError:
    SENSOR = False


@unittest.skipUnless(EX.is_dir(), "examples/ 가 옆에 없다 -- RLO_REPO")
@unittest.skipUnless(SENSOR, "훅은 Sensor 가 필요하다 -- rlo-sdk[sensor]")
class AgentSdkExample(unittest.TestCase):
    def setUp(self):
        stub = types.ModuleType("claude_agent_sdk")

        @dataclasses.dataclass
        class HookMatcher:                      # agent-sdk/python 의 정의: matcher · hooks · timeout
            matcher: "str | None" = None
            hooks: list = dataclasses.field(default_factory=list)
            timeout: "float | None" = None

        class ClaudeAgentOptions:
            def __init__(self, hooks=None, **kw):
                self.hooks = hooks

        stub.HookMatcher, stub.ClaudeAgentOptions = HookMatcher, ClaudeAgentOptions
        self.prev = sys.modules.get("claude_agent_sdk")
        sys.modules["claude_agent_sdk"] = stub
        spec = importlib.util.spec_from_file_location("rlo_example_agent_sdk", EX / "agent_sdk.py")
        self.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.mod)

    def tearDown(self):
        if self.prev is None:
            sys.modules.pop("claude_agent_sdk", None)
        else:
            sys.modules["claude_agent_sdk"] = self.prev

    def test_hooks_wired_to_one_adapter(self):
        options, adapter = self.mod.build_options(str(data("cc_tools_model.json")))
        self.assertEqual(set(options.hooks), {"PreToolUse", "PostToolUse", "PostToolUseFailure", "Stop"})
        self.assertNotIn("SessionEnd", options.hooks)                               # Python SDK 의 HookEvent 에 없다(문서)
        for matchers in options.hooks.values():
            (m,) = matchers
            self.assertIsNone(m.matcher)
            self.assertEqual(m.hooks, [adapter.callback])
        self.assertEqual(adapter.mode, "shadow")


if __name__ == "__main__":
    unittest.main()
