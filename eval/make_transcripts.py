"""기록된 transcript 를 짓는다 -- `rlo/data/transcripts/*.jsonl` 과 짝 훅 입력 `*.hook.json`.

    python eval/make_transcripts.py        (다시 지으면 같은 바이트가 나온다)

**지어낸 것이다(실데이터 아님).** Claude Code transcript JSONL 의 꼴(Telemetry `tests/fixtures.py` 가 쓰는 칸: type ·
timestamp · message.id/model/usage/content · tool_use · tool_result · toolUseResult)을 따른다. 비밀값 · 실제 경로 · 글이 없다.
각 파일은 **훅이 불린 순간의 transcript** 다(T18: 차례로 부른 앞 도구의 결과는 그때 있다 · 나란히 부른 도구는 앞 결과가 없다).
PreToolUse 순간 지금 부르는 도구의 tool_use 줄은 있을 수도 없을 수도 있다(T19: 결정적이지 않다, BD-124) -- 두 꼴을 다 둔다.
"""
from __future__ import annotations

import json
import pathlib

OUT = pathlib.Path(__file__).resolve().parent.parent / "rlo" / "data" / "transcripts"
SESSION = "00000000-0000-4000-8000-000000000001"


class T:
    def __init__(self):
        self.lines, self.t, self.n = [], 0, 0

    def ts(self):
        self.t += 1
        return f"2026-10-02T00:00:{self.t:02d}.000Z"

    def _base(self, kind):
        self.n += 1
        return {"type": kind, "sessionId": SESSION, "uuid": f"u{self.n:04d}", "timestamp": self.ts()}

    def user(self, text):
        self.lines.append({**self._base("user"), "message": {"role": "user", "content": text}})
        return self

    def uses(self, mid, *calls):
        self.lines.append({**self._base("assistant"), "message": {
            "id": mid, "model": "claude-x", "role": "assistant", "stop_reason": "tool_use",
            "usage": {"input_tokens": 100, "output_tokens": 10},
            "content": [{"type": "tool_use", "id": i, "name": n, "input": inp} for i, n, inp in calls]}})
        return self

    def result(self, tid, ok, text):
        self.lines.append({**self._base("user"), "toolUseResult": {}, "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": tid, "content": [{"type": "text", "text": text}], "is_error": not ok}]}})
        return self

    def text(self, mid, s):
        self.lines.append({**self._base("assistant"), "message": {
            "id": mid, "model": "claude-x", "role": "assistant", "stop_reason": "end_turn",
            "usage": {"input_tokens": 100, "output_tokens": 20}, "content": [{"type": "text", "text": s}]}})
        return self

    def stop_summary(self):
        self.lines.append({**self._base("system"), "subtype": "stop_hook_summary", "hookCount": 1})
        return self


def hook(event, name, tool=None, tool_input=None, tid=None):
    d = {"session_id": SESSION, "transcript_path": f"<{name}.jsonl>", "cwd": "/work", "permission_mode": "default",
         "hook_event_name": event}
    if tool is not None:
        d.update(tool_name=tool, tool_input=tool_input, tool_use_id=tid)
    return d


LS, MAKE, RM = {"command": "ls"}, {"command": "make"}, {"command": "rm -rf build"}
SCENARIOS = {
    # 이름: (transcript, 훅 입력)
    "normal": (T().user("go").uses("m1", ("tu1", "Bash", LS)).result("tu1", True, "a b")
               .uses("m2", ("tu2", "Bash", MAKE)),
               hook("PreToolUse", "normal", "Bash", MAKE, "tu2")),
    "normal_no_current_use": (T().user("go").uses("m1", ("tu1", "Bash", LS)).result("tu1", True, "a b"),
                              hook("PreToolUse", "normal_no_current_use", "Bash", MAKE, "tu2")),
    "after_failure": (T().user("go").uses("m1", ("tu1", "Bash", MAKE)).result("tu1", False, "Exit code 2")
                      .uses("m2", ("tu2", "Bash", RM)),
                      hook("PreToolUse", "after_failure", "Bash", RM, "tu2")),
    "read_after_failure": (T().user("go").uses("m1", ("tu1", "Bash", MAKE)).result("tu1", False, "Exit code 2")
                           .uses("m2", ("tu2", "Read", {"file_path": "/work/Makefile"})),
                           hook("PreToolUse", "read_after_failure", "Read", {"file_path": "/work/Makefile"}, "tu2")),
    "parallel": (T().user("go").uses("m1", ("tu1", "Read", {"file_path": "/work/a"}), ("tu2", "Bash", RM)),
                 hook("PreToolUse", "parallel", "Bash", RM, "tu2")),
    "first_call": (T().user("go").uses("m1", ("tu1", "Bash", LS)),
                   hook("PreToolUse", "first_call", "Bash", LS, "tu1")),
    "first_call_no_current_use": (T().user("go"),
                                  hook("PreToolUse", "first_call_no_current_use", "Bash", LS, "tu1")),
    # 앞 호출(tu1)의 결과가 아직 없다 -- 지금 호출(tu2)을 빼도 execution_health 는 모름이다
    "earlier_pending": (T().user("go").uses("m1", ("tu1", "Bash", MAKE)).uses("m2", ("tu2", "Bash", RM)),
                        hook("PreToolUse", "earlier_pending", "Bash", RM, "tu2")),
    "ended": (T().user("go").uses("m1", ("tu1", "Bash", LS)).result("tu1", True, "a b").text("m2", "done")
              .stop_summary(),
              hook("Stop", "ended")),
}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for name, (t, h) in SCENARIOS.items():
        (OUT / f"{name}.jsonl").write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in t.lines),
                                           encoding="utf-8")
        (OUT / f"{name}.hook.json").write_text(json.dumps(h, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{len(SCENARIOS)} 개 -> {OUT}")


if __name__ == "__main__":
    main()
