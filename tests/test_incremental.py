"""훅이 Sensor 의 이어 받기(`RunState.extend` · `evaluate="once"`)로 판정한다(CMD-K14 S2) -- 값은 전부 넣은 것과 같다."""
import json
import os
import subprocess
import sys
import unittest

from rlo import hooks
from rlo import react as R

from tests.test_channels import CHANNELS, COMMENT, MODEL, MODEL_D, NEEDS_SENSOR, PIN, Fixture

try:
    from llmsensor.run_state import RunState
    EXTEND = hasattr(RunState, "extend")
except ImportError:
    EXTEND = False
NEEDS_EXTEND = unittest.skipUnless(EXTEND, "Sensor 에 extend 가 없다(f1e45b5 이전)")


@NEEDS_SENSOR
@NEEDS_EXTEND
class Incremental(Fixture):
    def setUp(self):
        super().setUp()
        self.path = self.d / "live.jsonl"
        self.path.write_text("".join(x + "\n" for x in self.base), encoding="utf-8")

    def adapter(self, incremental=True, window=hooks.WINDOW, **kw):
        return hooks.guard_hooks(MODEL, mode="enforce", grants=("Bash", COMMENT), channels=R.channels_of({"channels": CHANNELS}),
                                 clock=lambda: self.now, record=lambda k, d: self.rows.append((k, d)), deadline_s=None,
                                 incremental=incremental, window=window, **kw)

    def full(self):
        """비교 기준: 이어 받지 않고 창 없이 전부(0.8.0 의 길)."""
        return self.adapter(incremental=False, window=None)

    def append(self, *lines):
        with open(self.path, "a", encoding="utf-8") as f:
            for x in lines:
                f.write(json.dumps(x) + "\n")

    def call(self, tu, tool, args):
        return {"session_id": self.sid, "transcript_path": str(self.path), "cwd": "/w", "permission_mode": "default",
                "hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": args, "tool_use_id": tu}

    def states(self, a, inp):
        return a.judge.view(inp)[1]["core"]["states"]

    def mode(self):
        return self.rows[-1][1]["window"]["mode"]

    def test_default_extends(self):
        j = hooks.TranscriptJudge(MODEL)
        self.assertTrue(j.can_extend())
        self.assertFalse(hooks.TranscriptJudge(MODEL, incremental=False).can_extend())

    def test_a_growing_session_matches_the_full_feed_at_every_call(self):
        """첫 부름은 once, 그 뒤는 extend. 부를 때마다 판정과 DC 상태가 전부 넣은 것과 같다."""
        a = self.adapter()
        steps = [("Read", {"file_path": "/a"}, "x", False), ("Bash", {"command": "cat missing.txt"}, "No such file", True),
                 ("Bash", {"command": "make"}, "ok", False), (COMMENT, dict(PIN, body="hi"), "ok", False),
                 ("Bash", {"command": "cat missing.txt"}, "ok", False), ("Read", {"file_path": "/b"}, "x", False)]
        modes = []
        for k, (tool, args, out, err) in enumerate(steps):
            tu, t = f"tu-s{k}", self.now - 60_000 + 1000 * k
            self.append(self.use(tu, tool, args, t))
            inp = self.call(tu, tool, args)
            with self.subTest(step=k, tool=tool):
                got = a.handle(inp)
                modes.append(self.mode())
                self.assertEqual(got, self.full().handle(inp))
                self.assertEqual(self.states(a, inp), self.states(self.full(), inp))
            self.append(self.result(tu, out, t + 500, err))
        self.assertEqual(modes[0], "once:first")
        self.assertEqual(set(modes[1:]), {"incremental"})

    def test_a_different_transcript_at_the_same_path_is_rebuilt(self):
        """캐시는 같은 파일이 자랄 때만 쓴다: 새 파일(inode) · 같은 inode 에 다른 내용 · 줄어든 파일이면 새로 짓는다."""
        a = self.adapter()
        self.append(self.use("tu-f", "Bash", {"command": "cat missing.txt"}, self.now - 5000),
                    self.result("tu-f", "No such file", self.now - 4000, True))
        self.append(self.use("tu-1", "Bash", {"command": "make"}, self.now - 3000))
        a.handle(self.call("tu-1", "Bash", {"command": "make"}))
        other = self.d / "other.jsonl"                                     # 다른 세션: 실패 없음, 머리가 다르다
        lines = [json.loads(x) for x in self.base]
        lines[0] = dict(lines[0], uuid="other-head")
        other.write_text("".join(json.dumps(x) + "\n" for x in [*lines, *[self.use(f"tu-o{i}", "Read", {"file_path": "/x"},
                                                                                   self.now - 9000 + i) for i in range(3)]]),
                         encoding="utf-8")
        os.replace(other, self.path)                                       # 새 inode
        inp = self.call("tu-2", "Bash", {"command": "make"})
        a.handle(inp)
        self.assertEqual(self.mode(), "once:other_file")
        self.assertEqual(self.states(a, inp), self.states(self.full(), inp))
        with open(self.path, "r+", encoding="utf-8") as f:                 # 같은 inode, 머리를 바꿈
            body = f.read()
            f.seek(0)
            f.write(body.replace("other-head", "third-head"))
        a.handle(self.call("tu-3", "Bash", {"command": "make"}))
        self.assertEqual(self.mode(), "once:other_file")
        with open(self.path, "r+", encoding="utf-8") as f:                 # 줄어듦
            f.truncate(len(f.read()) // 2)
        a.handle(self.call("tu-4", "Bash", {"command": "make"}))
        self.assertEqual(self.mode(), "once:other_file")

    def test_a_current_call_already_cached_is_rebuilt(self):
        """나란히 부른 두 호출: 앞 호출 때 뒤 호출의 tool.start 가 캐시에 들어간다. 뒤 호출의 판정은 그것을 빼야 하므로 새로 짓는다."""
        msg = {"type": "assistant", "sessionId": self.sid, "uuid": "a-par", "timestamp": self.iso(self.now - 2000),
               "message": {"id": "m-par", "model": "claude-x", "role": "assistant", "stop_reason": "tool_use",
                           "usage": {"input_tokens": 1, "output_tokens": 1},
                           "content": [{"type": "tool_use", "id": "tu-a", "name": "Read", "input": {"file_path": "/a"}},
                                       {"type": "tool_use", "id": "tu-b", "name": "Bash", "input": {"command": "make"}}]}}
        a = self.adapter()
        self.append(self.use("tu-0", "Read", {"file_path": "/z"}, self.now - 4000))
        a.handle(self.call("tu-0", "Read", {"file_path": "/z"}))           # 캐시를 먼저 세운다(once)
        self.append(self.result("tu-0", "x", self.now - 3000), msg)
        a.handle(self.call("tu-a", "Read", {"file_path": "/a"}))           # extend -- tu-b 의 tool.start 가 들어간다
        self.assertEqual(self.mode(), "incremental")
        inp = self.call("tu-b", "Bash", {"command": "make"})
        got = a.handle(inp)
        self.assertEqual(self.mode(), "once:current_seen")
        self.assertEqual(got, self.full().handle(inp))
        self.assertEqual(self.states(a, inp), self.states(self.full(), inp))

    def test_a_history_config_falls_back_to_the_window(self):
        from llmsensor.state import DEFAULT_CONFIG
        cfg = DEFAULT_CONFIG.with_(min_consecutive={"execution_health": 2})
        self.assertFalse(hooks.TranscriptJudge(MODEL, sensor_config=cfg).can_extend())
        self.append(self.use("tu-1", "Bash", {"command": "make"}, self.now - 3000))
        self.adapter(window=5, sensor_config=cfg).handle(self.call("tu-1", "Bash", {"command": "make"}))
        w = self.rows[-1][1]["window"]
        self.assertEqual((w["mode"], w["size"]), ("window", 5))

    def test_the_cache_is_bounded(self):
        a = self.adapter()
        for i in range(hooks.CACHE_MAX + 3):
            p = self.d / f"t{i}.jsonl"
            p.write_text(self.path.read_text(encoding="utf-8"), encoding="utf-8")
            a.handle(dict(self.call(f"tu-{i}", "Read", {"file_path": "/a"}), transcript_path=str(p)))
        self.assertEqual(len(a.judge._cache), hooks.CACHE_MAX)

    def test_same_transcript_rule(self):
        old = (1, 2, 10, b"abc")
        self.assertTrue(hooks.same_transcript(old, (1, 2, 20, b"abcdef")))
        for new in [(1, 3, 20, b"abcdef"), (9, 2, 20, b"abcdef"), (1, 2, 5, b"abc"), (1, 2, 20, b"abX")]:
            self.assertFalse(hooks.same_transcript(old, new), new)


@NEEDS_SENSOR
@NEEDS_EXTEND
class CommandHookFeed(Fixture):
    def run_hook(self, *extra):
        model = self.d / "model.json"
        model.write_text(json.dumps(MODEL_D), encoding="utf-8")
        rec = self.d / "rec.jsonl"
        inp = self.inp("Bash", {"command": "make"}, tu="tu-cli")
        p = subprocess.run([sys.executable, "-m", "rlo.hooks", "--model", str(model), "--mode", "enforce", "--grant", "Bash",
                            "--now-ms", str(self.now), "--record", str(rec), *extra],
                           input=json.dumps(inp), capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        return json.loads(rec.read_text(encoding="utf-8").splitlines()[-1])["window"]

    def test_command_hook_builds_once_by_default(self):
        """명령 훅은 부를 때마다 새 프로세스다 -- 캐시가 없으니 늘 once(선형)로 짓는다."""
        self.assertEqual(self.run_hook()["mode"], "once:first")
        self.assertEqual(self.run_hook("--no-incremental")["mode"], "window")


if __name__ == "__main__":
    unittest.main()
