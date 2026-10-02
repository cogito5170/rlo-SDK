"""CMD-K8 확인 -- 고친 K7 탐침(훅 **전에** transcript 사본)의 재현이 훅의 판정과 같은가, `claude -p` 없이 기록된 transcript 로.

    python eval/k8_probe_check.py --python <rlo-sdk[sensor] 를 깐 python>

- 기록된 transcript(PreToolUse 꼴 여덟)를 임시로 베끼고 시각을 '지금' 쪽으로 옮긴다(마지막 줄 = 지금 − 1 초).
- 훅 명령 = 실제 rlo 훅 뒤에 **런타임이 transcript 에 줄을 더 쓰는 것**을 흉내 낸 한 줄(다음 tool_use, 시각은 훅 시작 뒤).
  K7 실제 실행에서 사본을 훅 뒤에 떠서 그런 줄이 섞였을 것이라는 가정(K7 보고)을 여기서 재 본다.
- 두 꼴로 재현한다: 새 탐침(훅 전 사본) · K7 탐침(훅 뒤 사본). 기대: 새 것은 모두 같고, K7 꼴은 어긋날 수 있다.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import shlex
import subprocess
import sys
import tempfile
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import k7_real_cc as K  # noqa: E402

PRE = ("normal", "normal_no_current_use", "after_failure", "read_after_failure", "parallel", "first_call",
       "first_call_no_current_use", "earlier_pending")


def iso(ms: float) -> str:
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def shifted(src: str, dst: pathlib.Path, last_ms: float):
    lines = [json.loads(x) for x in pathlib.Path(src).read_text(encoding="utf-8").splitlines() if x.strip()]
    t = [dt.datetime.fromisoformat(x["timestamp"].replace("Z", "+00:00")).timestamp() * 1000 for x in lines]
    off = last_ms - max(t)
    for x, ti in zip(lines, t):
        x["timestamp"] = iso(ti + off)
    dst.write_text("".join(json.dumps(x) + "\n" for x in lines), encoding="utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--python", required=True)
    a = ap.parse_args(argv)
    q = lambda *xs: [subprocess.run([a.python, "-c", x], capture_output=True, text=True, check=True).stdout.strip() for x in xs]
    model, tdir = q("from rlo.example_hooks import data; print(data('cc_tools_model.json'))",
                    "from rlo.example_hooks import data; print(data('transcripts'))")
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="rlo-k8-"))
    rows, new_same, old_same = [], 0, 0
    for name in PRE:
        d = tmp / name
        (d / "snaps").mkdir(parents=True)
        tp = d / "t.jsonl"
        shifted(f"{tdir}/{name}.jsonl", tp, time.time() * 1000 - 1000)
        inp = json.loads(pathlib.Path(f"{tdir}/{name}.hook.json").read_text(encoding="utf-8"))
        inp["transcript_path"] = str(tp)
        later = json.dumps({"type": "assistant", "timestamp": "__LATER__", "message": {
            "id": "mlater", "model": "claude-x", "stop_reason": "tool_use", "usage": {"input_tokens": 1, "output_tokens": 1},
            "content": [{"type": "tool_use", "id": "tu-later", "name": "Bash", "input": {"command": "ls"}}]}})
        appender = (f"{shlex.quote(sys.executable)} -c " + shlex.quote(
            "import sys,time,datetime as dt;"
            "ts=dt.datetime.now(dt.timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z');"
            f"open(sys.argv[1],'a').write({later!r}.replace('__LATER__', ts)+chr(10))") + f" {shlex.quote(str(tp))}")
        hook = " ".join(shlex.quote(x) for x in [a.python, "-m", "rlo.hooks", "--model", model, "--mode", "enforce",
                                                 "--grant", "Bash"])
        real = f"{hook}; rc=$?; sleep 0.05; {appender}; exit $rc"
        log = d / "wrap.jsonl"
        p = subprocess.run([sys.executable, str(HERE / "k7_real_cc.py"), "wrap", str(log), str(d / "snaps"), real],
                           input=json.dumps(inp), capture_output=True, text=True)
        row = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
        hook_out = (json.loads(p.stdout) if p.stdout.strip() else {}).get("hookSpecificOutput", {})
        hook_dec = "deny" if hook_out.get("permissionDecision") == "deny" else "allow"
        new = K.replay(a.python, model, row, "enforce", d)
        old = K.replay(a.python, model, dict(row, snapshot=str(tp)), "enforce", d)       # K7 꼴: 훅 뒤의 transcript
        to = lambda r: "deny" if r and r.get("verdict") != "ALLOW" else "allow"
        new_same += to(new) == hook_dec
        old_same += to(old) == hook_dec
        rows.append((name, hook_dec, new, old))
    for name, h, new, old in rows:
        print(f"{name:26} hook={h:5}  new={new['verdict']}/{new['rule']} {new['execution_health']}  "
              f"k7={old['verdict']}/{old['rule']} {old['execution_health']}")
    print(f"새 탐침(훅 전 사본) 재현 = 훅: {new_same}/{len(PRE)}   K7 꼴(훅 뒤 사본) 재현 = 훅: {old_same}/{len(PRE)}")
    return 0 if new_same == len(PRE) else 1


if __name__ == "__main__":
    sys.exit(main())
