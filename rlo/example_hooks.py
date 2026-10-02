"""훅 예 -- `python -m rlo.example_hooks`. 설치한 패키지만 쓴다(옆 저장소 경로 없이).

기록된 transcript(`rlo/data/transcripts`, 지어낸 것 -- `eval/make_transcripts.py`)를 Claude Code **명령 훅 길 그대로** 돌린다:
훅 입력 JSON 을 `python -m rlo.hooks --model … --mode …` 의 표준입력으로 주고 표준출력을 읽는다. 지금(now)은 transcript 의
마지막 시각 + 1 초로 고정한다(`--now-ms`). 모형은 `rlo/data/cc_tools_model.json`, Bash 에 허가(`--grant Bash`)를 준다.

    $ python -m rlo.example_hooks
    shadow   normal               {}
    enforce  normal               deny  guard DENY(D): ...
    ...
"""
from __future__ import annotations

import json
import subprocess
import sys
from importlib import resources

PRE_SCENARIOS = ("normal", "normal_no_current_use", "after_failure", "read_after_failure", "parallel", "first_call",
                 "first_call_no_current_use", "earlier_pending")


def data(name: str):
    return resources.files("rlo") / "data" / name


def hook_input(name: str) -> dict:
    """짝 훅 입력. transcript_path 를 패키지 안의 실제 경로로 바꾼다."""
    d = json.loads(data(f"transcripts/{name}.hook.json").read_text(encoding="utf-8"))
    d["transcript_path"] = str(data(f"transcripts/{name}.jsonl"))
    return d


def now_after(name: str) -> float:
    """transcript 의 마지막 시각 + 1 초(unix ms)."""
    from datetime import datetime
    last = [json.loads(x)["timestamp"] for x in data(f"transcripts/{name}.jsonl").read_text(encoding="utf-8").splitlines()
            if x.strip()][-1]
    return datetime.fromisoformat(last.replace("Z", "+00:00")).timestamp() * 1000 + 1000


def command_hook(name: str, mode: str, *extra: str) -> dict:
    """`python -m rlo.hooks` 를 명령 훅처럼 부른다. 표준출력이 비면 {}."""
    argv = [sys.executable, "-m", "rlo.hooks", "--model", str(data("cc_tools_model.json")), "--mode", mode,
            "--grant", "Bash", "--now-ms", str(now_after(name)), *extra]
    p = subprocess.run(argv, input=json.dumps(hook_input(name)), capture_output=True, text=True, check=True)
    return json.loads(p.stdout) if p.stdout.strip() else {}


def show(out: dict) -> str:
    if not out:
        return "{}"
    h = out["hookSpecificOutput"]
    return f'{h["permissionDecision"]}  {h["permissionDecisionReason"][:110]}'


def main() -> int:
    rows = []
    for mode in ("shadow", "enforce"):
        for name in PRE_SCENARIOS:
            out = command_hook(name, mode)
            rows.append((mode, name, out))
            print(f"{mode:8} {name:22} {show(out)}")
    ok = all(out == {} for mode, _, out in rows if mode == "shadow") and all(
        (out == {} or out["hookSpecificOutput"]["permissionDecision"] == "deny") for _, _, out in rows)
    ok = ok and not any("allow" in json.dumps(out) for _, _, out in rows)
    stop = command_hook("ended", "enforce")
    print(f"{'enforce':8} {'ended (Stop)':22} {show(stop)}")
    ok = ok and stop == {}
    print("OK" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
