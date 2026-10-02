"""CMD-K4 탐침 -- MBA-frontend(`cogito5170/MBA`) 와 rlo 훅을 한 Claude Code 설정에 함께 깔고 떼 본다(BD-125).

    python eval/with_mba.py --mba-frontend <mba-frontend 실행 파일> [--python <rlo 가 깔린 python>]

**임시 디렉터리 안에서만** 돈다: HOME · MBA_HOME · 설정 파일 모두 임시다. 실제 ~/.claude · ~/.mba 는 건드리지 않는다.
`claude -p` 는 부르지 않는다: MBA 의 컴파일 실행 파일을 `MBA_CLAUDE_BIN` 으로 가짜(OP=NONE 을 내는 스크립트)로 바꾼다.
MBA 는 읽기만 한다(이 탐침은 MBA 를 import 하지 않고 그 CLI 만 부른다).

rlo 는 설치 명령이 없다 -- `examples/claude_code_settings.json` 의 항목을 사용자가 손으로 넣는 것을 흉내 낸다:
    깔기  사건마다 rlo 묶음({"hooks": [rlo 명령]}, PreToolUse 는 matcher "*")을 **끝에 덧붙인다**
    떼기  명령에 `rlo.hooks` 가 든 훅을 빼고, 빈 묶음 · 빈 사건을 지운다
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import threading

RLO_TAG = "rlo.hooks"
USER = {   # 원래 사용자 설정(지어낸 것): 남의 훅 · 다른 칸이 그대로 남는지 본다
    "permissions": {"allow": ["Bash(ls:*)"]},
    "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "echo user-pre"}]}],
              "Stop": [{"hooks": [{"type": "command", "command": "echo user-stop"}]}]},
}


def rlo_entries(python: str, model: str, record: str) -> dict:
    cmd = f'"{python}" -m {RLO_TAG} --model "{model}" --mode shadow --record "{record}"'
    return {"PreToolUse": [{"matcher": "*", "hooks": [{"type": "command", "command": cmd + " --grant Bash"}]}],
            "Stop": [{"hooks": [{"type": "command", "command": cmd}]}],
            "SessionEnd": [{"hooks": [{"type": "command", "command": cmd}]}]}


def rlo_install(p: pathlib.Path, entries: dict):
    d = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
    hooks = d.setdefault("hooks", {})
    for ev, groups in entries.items():
        hooks.setdefault(ev, []).extend(copy.deepcopy(groups))
    p.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def rlo_uninstall(p: pathlib.Path):
    d = json.loads(p.read_text(encoding="utf-8"))
    hooks = d.get("hooks", {})
    for ev in list(hooks):
        for g in hooks[ev]:
            g["hooks"] = [h for h in g.get("hooks", []) if RLO_TAG not in h.get("command", "")]
        hooks[ev] = [g for g in hooks[ev] if g.get("hooks")]
        if not hooks[ev]:
            del hooks[ev]
    if not hooks:
        d.pop("hooks", None)
    p.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load(p: pathlib.Path):
    return json.loads(p.read_text(encoding="utf-8"))


def commands(d: dict, ev: str) -> list:
    return [h["command"] for g in d.get("hooks", {}).get(ev, []) for h in g.get("hooks", [])]


class Probe:
    def __init__(self, mba: str, python: str):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="rlo-mba-"))
        self.home = self.tmp / "home"
        (self.home / ".claude").mkdir(parents=True)
        self.mba, self.python = mba, python
        fake = self.tmp / "fake-claude"
        fake.write_text('#!/bin/sh\necho \'{"result": "OP=NONE", "usage": {}, "is_error": false}\'\n', encoding="utf-8")
        fake.chmod(0o755)
        self.env = {**os.environ, "HOME": str(self.home), "MBA_HOME": str(self.home / ".mba"),
                    "MBA_CLAUDE_BIN": str(fake)}
        self.env.pop("PYTHONPATH", None)
        from rlo.example_hooks import data            # 기록된 transcript · 모형(rlo 패키지 안)
        self.model = str(data("cc_tools_model.json"))
        self.transcript = str(data("transcripts/ended.jsonl"))
        self.record = str(self.tmp / "rlo-record.jsonl")
        self.checks: list = []

    def check(self, name: str, ok: bool, detail=None):
        self.checks.append({"check": name, "ok": bool(ok), **({"detail": detail} if detail is not None else {})})

    def mba_cli(self, settings: pathlib.Path, cmd: str):
        r = subprocess.run([self.mba, cmd, "--settings", str(settings)], env=self.env, capture_output=True, text=True)
        assert r.returncode == 0, r.stderr

    # ── 깔고 떼기 ──────────────────────────────────────────────────────────
    def order(self, first: str) -> dict:
        p = self.tmp / f"settings-{first}.json"
        p.write_text(json.dumps(USER, indent=2) + "\n", encoding="utf-8")
        bak = p.with_name(p.name + ".bak-mba")
        entries = rlo_entries(self.python, self.model, self.record)
        steps = {"original": load(p)}
        trace = []

        def note(step):                                  # 그 단계 뒤 .bak-mba 에 무엇이 있나(이름 붙은 단계와 같은가)
            if not bak.exists():
                trace.append((step, "없음"))
                return
            b = json.loads(bak.read_text(encoding="utf-8"))
            same = [k for k, v in steps.items() if v == b]
            trace.append((step, f"= {same[0]}" if same else "다른 것", "rlo 항목 있음" if RLO_TAG in json.dumps(b) else "rlo 항목 없음"))
        put = {"mba": lambda: self.mba_cli(p, "install-hook"), "rlo": lambda: rlo_install(p, entries)}
        take = {"mba": lambda: self.mba_cli(p, "uninstall-hook"), "rlo": lambda: rlo_uninstall(p)}
        second = "rlo" if first == "mba" else "mba"
        put[first]()
        steps[f"after_{first}"] = load(p)
        note(f"{first} 깖")
        put[second]()
        both = steps["both"] = load(p)
        note(f"{second} 깖")
        bak_after_both = bak.read_text(encoding="utf-8") if bak.exists() else None
        tag = f"[{first} → {second}]"
        self.check(f"{tag} 둘 다 남는다: Stop 에 MBA · rlo · 사용자", any("mba" in c for c in commands(both, "Stop"))
                   and any(RLO_TAG in c for c in commands(both, "Stop")) and "echo user-stop" in commands(both, "Stop"))
        self.check(f"{tag} UserPromptSubmit 은 MBA 하나", len(commands(both, "UserPromptSubmit")) == 1)
        self.check(f"{tag} PreToolUse 는 사용자 + rlo, SessionEnd 는 rlo",
                   commands(both, "PreToolUse")[0] == "echo user-pre" and len(commands(both, "PreToolUse")) == 2
                   and len(commands(both, "SessionEnd")) == 1)
        self.check(f"{tag} 다른 칸(permissions)이 그대로", both.get("permissions") == USER["permissions"])
        put["mba"]()                                     # MBA 를 다시 깔아도 하나만(MBA 의 '제자리에 하나만')
        self.check(f"{tag} MBA 다시 깔아도 겹치지 않는다", load(p) == both)
        take[second]()
        note(f"{second} 뗌")
        self.check(f"{tag} {second} 를 떼면 {first} 을 깐 뒤와 같다", load(p) == steps[f"after_{first}"])
        take[first]()
        note(f"{first} 뗌")
        self.check(f"{tag} 마저 떼면 원래 설정과 같다", load(p) == steps["original"])
        print(f"{tag} .bak-mba: " + " · ".join(" ".join(t) for t in trace))
        bak_final = bak.read_text(encoding="utf-8") if bak.exists() else None
        self.check(f"{tag} rlo 는 .bak-mba 를 만들지 · 고치지 않는다",
                   not any(RLO_TAG in json.dumps(steps[k]) for k in ("original",)) and
                   (bak_final is None or json.loads(bak_final) in (steps["original"], steps["both"], steps[f"after_{first}"],
                                                                    load(p))),
                   {"bak_after_both": None if bak_after_both is None else "rlo 항목 있음" if RLO_TAG in bak_after_both
                    else "rlo 항목 없음", "bak_final_has_rlo": None if bak_final is None else RLO_TAG in bak_final})
        extra = sorted(x.name for x in p.parent.iterdir() if x.name.startswith(p.name) and x.name not in (p.name, bak.name))
        self.check(f"{tag} 설정 옆에 다른 파일이 생기지 않는다", extra == [], extra)
        return steps

    # ── Stop 을 함께 ───────────────────────────────────────────────────────
    def run_hook(self, cmd: str, payload: dict):
        r = subprocess.run(cmd, shell=True, input=json.dumps(payload), env=self.env, capture_output=True, text=True,
                           cwd=str(self.tmp))
        return {"rc": r.returncode, "stdout": r.stdout, "stderr": r.stderr}

    def stop_together(self, both: dict) -> dict:
        common = {"session_id": "00000000-0000-4000-8000-000000000001", "transcript_path": self.transcript,
                  "cwd": str(self.tmp), "permission_mode": "default"}
        mba_prompt = next(c for c in commands(both, "UserPromptSubmit") if "mba" in c)
        mba_stop = next(c for c in commands(both, "Stop") if "mba" in c)
        rlo_stop = next(c for c in commands(both, "Stop") if RLO_TAG in c)
        stop = {**common, "hook_event_name": "Stop", "stop_hook_active": False}

        def turn():                                      # MBA 는 Stop 에 앞서 그 턴의 프롬프트를 기록해 둬야 Stop 이 일한다
            self.run_hook(mba_prompt, {**common, "hook_event_name": "UserPromptSubmit", "prompt": "지금 HEAD 는?"})

        def ledger_stops():
            p = self.home / ".mba" / "ledger.jsonl"
            rows = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []
            return [{k: r[k] for k in ("e", "tokens", "read_only", "kinds", "final_len")} for r in rows if r["e"] == "stop"]

        turn()
        alone_mba = self.run_hook(mba_stop, stop)
        alone_rlo = self.run_hook(rlo_stop, stop)
        n_alone = len(ledger_stops())
        turn()
        out = {}

        def go(name, cmd):
            out[name] = self.run_hook(cmd, stop)
        ts = [threading.Thread(target=go, args=("mba", mba_stop)), threading.Thread(target=go, args=("rlo", rlo_stop))]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        stops = ledger_stops()
        self.check("Stop: MBA 의 출력 · 종료 코드가 혼자일 때와 함께일 때 같다", out["mba"] == alone_mba,
                   {"alone": alone_mba, "together": out["mba"]})
        self.check("Stop: rlo 의 출력 · 종료 코드가 혼자일 때와 함께일 때 같다", out["rlo"] == alone_rlo,
                   {"alone": alone_rlo, "together": out["rlo"]})
        self.check("Stop: 둘 다 막지 않는다(출력 없음 · 종료 0)",
                   all(r == {"rc": 0, "stdout": "", "stderr": ""} for r in (alone_mba, alone_rlo, out["mba"], out["rlo"])))
        self.check("Stop: MBA 원장의 stop 기록이 함께 돌아도 같다", n_alone == 1 and len(stops) == 2 and stops[0] == stops[1],
                   stops)
        rec = pathlib.Path(self.record)
        rows = [json.loads(x) for x in rec.read_text(encoding="utf-8").splitlines()] if rec.exists() else []
        self.check("Stop: rlo 는 거두다 오류가 나지 않았다(collect_error 0)",
                   not any(r.get("kind") == "collect_error" for r in rows), rows)
        return {"alone": {"mba": alone_mba, "rlo": alone_rlo}, "together": out, "mba_ledger_stops": stops}

    def run(self) -> int:
        s1 = self.order("mba")
        self.order("rlo")
        self.stop_together(s1["both"])
        for c in self.checks:
            print(("OK   " if c["ok"] else "FAIL ") + c["check"] + ("" if c["ok"] else f"  {json.dumps(c.get('detail'), ensure_ascii=False)[:400]}"))
        bad = sum(not c["ok"] for c in self.checks)
        print(f"{len(self.checks) - bad}/{len(self.checks)} OK")
        shutil.rmtree(self.tmp)
        return 0 if bad == 0 else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mba-frontend", required=True)
    ap.add_argument("--python", default=sys.executable)
    a = ap.parse_args(argv)
    return Probe(a.mba_frontend, a.python).run()


if __name__ == "__main__":
    sys.exit(main())
