"""CMD-K7 탐침 -- 실제 Claude Code(`claude -p`)에서 rlo 훅을 확인한다(BD-129). **코드는 고치지 않는다 -- 재기만 한다.**

    python eval/k7_real_cc.py --python <stage-4 를 깐 venv 의 python> --out eval/results/k7_real_cc.json

격리
- `claude` 는 깨끗한 환경으로 부른다: PATH · LANG · 프록시 · ANTHROPIC_BASE_URL 만 넘기고, HOME 과 CLAUDE_CONFIG_DIR 은 임시다.
  이 세션의 CLAUDE_CODE_* 변수는 넘기지 않는다(자식 실행이 부모 세션에 섞이지 않게). 실제 ~/.claude 는 쓰지 않는다.
- 설정은 임시 파일(`--settings`)에만 `python -m rlo.hooks install-hook` 으로 건다. 작업 디렉터리도 임시다.
- 훅 명령을 이 파일의 `wrap` 으로 감싸 사건 · 도구 이름 · 걸린 ms · 응답(판정)만 적는다. 그 순간의 transcript 를 임시 디렉터리에
  복사해 두고(나중에 같은 판정이 나오는지 다시 돌려 보려고), 끝나면 지운다. **transcript 원문 · 프롬프트 답 글은 결과에 남기지 않는다.**
- 실행은 6 번: 시나리오 셋 × (shadow, enforce), 모델 haiku.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import shlex
import shutil
import subprocess
import sys
import tempfile
import time

HERE = pathlib.Path(__file__).resolve()
SCENARIOS = {
    "bash_fail_then_bash": "Use the Bash tool to run these commands one at a time, in order: `ls`, then `cat missing.txt`, "
                           "then `echo done`. Then reply with the single word: finished.",
    "read_grep_edit": "Use the Read tool to read notes.txt. Then use the Grep tool to search for the word foo in this "
                      "directory. Then use the Edit tool to replace foo with bar in notes.txt. Then reply with the single "
                      "word: finished.",
    "parallel_bash": "In ONE single response, call the Bash tool twice in parallel: `echo a` and `echo b`. Then reply "
                     "with the single word: finished.",
}
ALLOWED = "Bash Read Grep Edit Glob Write"


# ── 감싸기: 훅 명령 앞에 붙는다 ────────────────────────────────────────────────
def wrap(log: str, snaps: str, real: str) -> int:
    """훅을 감싼다. 그 순간의 transcript 사본과 '지금'(at_ms)을 훅을 돌리기 **전에** 뜬다(CMD-K8) -- 훅이 도는 동안 런타임이
    transcript 에 더 쓴 줄이 사본에 들어가지 않게. K7 은 훅 **뒤에** 떠서 재현 1/16 이 어긋났다."""
    raw = sys.stdin.read()
    try:
        d = json.loads(raw)
    except json.JSONDecodeError:
        d = {}
    n = sum(1 for _ in open(log, encoding="utf-8")) if os.path.exists(log) else 0
    at_ms = time.time() * 1000
    tp = d.get("transcript_path")
    snap = None
    if tp and os.path.exists(tp):
        snap = os.path.join(snaps, f"{n:03d}.jsonl")
        shutil.copy(tp, snap)
    t0 = time.perf_counter()
    p = subprocess.run(real, shell=True, input=raw, capture_output=True, text=True)
    ms = (time.perf_counter() - t0) * 1000
    sys.stdout.write(p.stdout)
    sys.stderr.write(p.stderr)
    out = json.loads(p.stdout) if p.stdout.strip() else {}
    hso = out.get("hookSpecificOutput", {})
    row = {"n": n, "event": d.get("hook_event_name"), "tool_name": d.get("tool_name"),
           "tool_use_id": d.get("tool_use_id"), "tool_input_keys": sorted((d.get("tool_input") or {}).keys()),
           "ms": round(ms, 1), "rc": p.returncode, "decision": hso.get("permissionDecision"),
           "reason_head": (hso.get("permissionDecisionReason") or "")[:60], "snapshot": snap, "at_ms": at_ms,
           "input": {k: v for k, v in d.items() if k not in ("tool_response", "prompt")}}     # 임시 로그에만 -- 결과에는 안 남긴다
    with open(log, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return p.returncode


# ── 실행 ───────────────────────────────────────────────────────────────────
def clean_env(home: pathlib.Path, cfg: pathlib.Path) -> dict:
    keep = ("PATH", "LANG", "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "https_proxy", "http_proxy", "no_proxy",
            "ANTHROPIC_BASE_URL", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "NODE_EXTRA_CA_CERTS")
    env = {k: os.environ[k] for k in keep if k in os.environ}
    env.update(HOME=str(home), CLAUDE_CONFIG_DIR=str(cfg))
    return env


def transcript_facts(cfg: pathlib.Path) -> dict:
    """그 실행의 transcript 에서 수만: 도구 이름 분포 · 결과 없는 tool_use · deny 까닭이 Claude 에 갔나."""
    uses, results = {}, {}
    for f in cfg.glob("projects/**/*.jsonl"):
        for line in f.read_text(encoding="utf-8").splitlines():
            try:
                x = json.loads(line)
            except json.JSONDecodeError:
                continue
            if x.get("isSidechain"):
                continue
            c = (x.get("message") or {}).get("content")
            for b in c if isinstance(c, list) else []:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    uses[b["id"]] = b.get("name")
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    text = json.dumps(b.get("content"), ensure_ascii=False)
                    results[b.get("tool_use_id")] = {"is_error": bool(b.get("is_error")), "mentions_guard": "guard" in text}
    names = {}
    for n in uses.values():
        names[n] = names.get(n, 0) + 1
    return {"tool_uses": len(uses), "names": names,
            "results": {tid: {"name": uses.get(tid), **r} for tid, r in results.items()},
            "no_result": [uses[t] for t in uses if t not in results]}


REPLAY = """
import json, sys
from action.spec import ActionModel
from rlo.hooks import TranscriptJudge
model = ActionModel.from_dict(json.load(open(sys.argv[1])))
row = json.load(open(sys.argv[2]))
inp = dict(row["input"], transcript_path=row["snapshot"])
j = TranscriptJudge(model, grants=("Bash",), clock=lambda: row["at_ms"])
_, v, res, dcv = j(inp, sys.argv[3])
_, rec, _ = j.view(inp)
h = rec["core"]["states"].get("agent.execution_health")
print(json.dumps({"verdict": res.verdict, "rule": res.rule, "complete": dcv.complete, "execution_health": h}))
"""


def replay(python: str, model: str, row: dict, mode: str, scratch: pathlib.Path) -> "dict | None":
    """그 순간의 transcript 사본 · 그 시각으로 같은 판정을 다시 낸다(결정적인가)."""
    if not row.get("snapshot") or row["event"] != "PreToolUse":
        return None
    f = scratch / f"row{row['n']}.json"
    f.write_text(json.dumps(row), encoding="utf-8")
    p = subprocess.run([python, "-c", REPLAY, model, str(f), mode], capture_output=True, text=True)
    return json.loads(p.stdout) if p.returncode == 0 else {"error": p.stderr.strip().splitlines()[-1:]}


def run(a) -> dict:
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="rlo-k7-"))
    model_json = subprocess.run([a.python, "-c", "from rlo.example_hooks import data; print(data('cc_tools_model.json'))"],
                                capture_output=True, text=True, check=True).stdout.strip()
    out = {"runs": [], "claude_version": subprocess.run(["claude", "--version"], capture_output=True, text=True).stdout.strip()}
    for mode in ("shadow", "enforce"):
        for name, prompt in SCENARIOS.items():
            d = tmp / f"{mode}-{name}"
            work, home, cfg, snaps = d / "work", d / "home", d / "cfg", d / "snaps"
            for x in (work, home, cfg, snaps):
                x.mkdir(parents=True)
            (work / "notes.txt").write_text("hello foo world\n", encoding="utf-8")
            settings, record, log = d / "settings.json", d / "record.jsonl", d / "wrap.jsonl"
            r = subprocess.run([a.python, "-m", "rlo.hooks", "install-hook", "--settings", str(settings), "--model", model_json,
                                "--mode", mode, "--grant", "Bash", "--record", str(record), "--python", a.python],
                               capture_output=True, text=True)
            assert r.returncode == 0, r.stderr
            s = json.loads(settings.read_text(encoding="utf-8"))
            for groups in s["hooks"].values():
                for g in groups:
                    for h in g["hooks"]:
                        h["command"] = " ".join(shlex.quote(x) for x in
                                                [sys.executable, str(HERE), "wrap", str(log), str(snaps), h["command"]])
            settings.write_text(json.dumps(s, indent=2), encoding="utf-8")
            argv = ["claude", "-p", prompt, "--model", "haiku", "--output-format", "json", "--settings", str(settings),
                    "--allowedTools", ALLOWED]
            t0 = time.perf_counter()
            p = subprocess.run(argv, cwd=str(work), env=clean_env(home, cfg), capture_output=True, text=True, timeout=300)
            wall = (time.perf_counter() - t0) * 1000
            try:
                res = json.loads(p.stdout)
            except json.JSONDecodeError:
                res = {"parse_error": True, "stderr_head": p.stderr[:200]}
            hooks = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []
            for h in hooks:
                h["replay"] = replay(a.python, model_json, h, mode, d)
            recs = [json.loads(x) for x in record.read_text(encoding="utf-8").splitlines()] if record.exists() else []
            out["runs"].append({
                "mode": mode, "scenario": name, "rc": p.returncode, "wall_ms": round(wall),
                "claude": {k: res.get(k) for k in ("is_error", "num_turns", "duration_ms", "duration_api_ms", "total_cost_usd",
                                                    "subtype", "parse_error", "stderr_head")},
                "result_chars": len(res.get("result") or ""),
                "hooks": [{k: v for k, v in h.items() if k not in ("input", "snapshot", "at_ms")} for h in hooks],
                "record": [{"kind": x.get("kind"), "tool_name": x.get("tool_name"), "verdict": (x.get("result") or {}).get("verdict"),
                            "rule": (x.get("result") or {}).get("rule"), "complete": x.get("complete"),
                            "missing_required": x.get("missing_required"), "exception": x.get("exception")} for x in recs],
                "transcript": transcript_facts(cfg),
            })
            print(f"{mode:8} {name:22} rc={p.returncode} hooks={len(hooks)} record={len(recs)} "
                  f"cost={res.get('total_cost_usd')} turns={res.get('num_turns')}", flush=True)
    shutil.rmtree(tmp)                                   # transcript 사본 · 설정 · 작업 디렉터리를 남기지 않는다
    return out


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "wrap":
        return wrap(argv[1], argv[2], argv[3])
    ap = argparse.ArgumentParser()
    ap.add_argument("--python", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    out = run(a)
    pathlib.Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    pathlib.Path(a.out).write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
