"""rlo 훅 깔기 · 떼기 -- `python -m rlo.hooks install-hook | uninstall-hook [--settings 경로] …` (CMD-K6).

    install-hook    PreToolUse(matcher "*") · Stop · SessionEnd 에 rlo 훅을 **사건마다 하나만** 둔다.
                    이미 있으면 그 자리에서 바꾼다(다시 깔아도 하나). 없으면 그 사건의 끝에 새 묶음으로 덧붙인다.
    uninstall-hook  rlo 훅만 뺀다. 비게 된 묶음 · 사건 · hooks 칸은 지운다.

- 남의 훅 · 다른 칸은 건드리지 않는다. rlo 훅 = 명령에 `-m rlo.hooks` 가 든 것.
- 바뀔 때만 쓴다. 쓰기 전 파일이 있었으면 그 내용을 `<설정>.bak-rlo` 로 남긴다(MBA 의 `.bak-mba` 와 겹치지 않는 이름).
- 닫는 쪽: 설정이 JSON 이 아니거나 꼴이 다르면(hooks 가 객체가 아님 · 사건이 목록이 아님) 아무것도 쓰지 않고 실패한다.
  깔 때는 모형 파일이 `action-model/1` 로 읽히는지 먼저 본다 -- 틀린 모형을 깔면 enforce 의 모든 PreToolUse 가 막힌다.
- 기본 설정 경로는 `~/.claude/settings.json`(Claude Code 사용자 설정).
"""
from __future__ import annotations

import json
import os
import re
import shlex
import sys
import tempfile
from pathlib import Path

EVENTS = (("PreToolUse", "*"), ("Stop", None), ("SessionEnd", None))
_OURS = re.compile(r"(?:^|\s)-m\s+rlo\.hooks(?:\s|$)")
BACKUP_SUFFIX = ".bak-rlo"


class SettingsError(ValueError):
    pass


def is_ours(command: str) -> bool:
    return bool(_OURS.search(command or ""))


def hook_command(*, model: str, mode: str = "shadow", grants=(), purpose: "str | None" = None,
                 stall_threshold: "int | None" = None, record: "str | None" = None, python: "str | None" = None) -> str:
    argv = [python or sys.executable, "-m", "rlo.hooks", "--model", str(Path(model).resolve()), "--mode", mode]
    for g in grants:
        argv += ["--grant", g]
    if purpose:
        argv += ["--purpose", purpose]
    if stall_threshold is not None:
        argv += ["--stall-threshold", str(stall_threshold)]
    if record:
        argv += ["--record", str(Path(record).resolve())]
    return " ".join(shlex.quote(x) for x in argv)


def _load(p: Path) -> "tuple[str | None, dict]":
    if not p.is_file():
        return None, {}
    text = p.read_text(encoding="utf-8")
    if not text.strip():
        return text, {}
    try:
        d = json.loads(text)
    except json.JSONDecodeError as e:
        raise SettingsError(f"{p}: JSON 이 아니다 ({e.msg}) -- 쓰지 않는다") from e
    if not isinstance(d, dict):
        raise SettingsError(f"{p}: 객체가 아니다 -- 쓰지 않는다")
    hooks = d.get("hooks", {})
    if not isinstance(hooks, dict) or not all(isinstance(v, list) and all(isinstance(g, dict) for g in v)
                                              for v in hooks.values()):
        raise SettingsError(f"{p}: hooks 의 꼴이 다르다(사건 -> 묶음 목록) -- 쓰지 않는다")
    return text, d


def _write(p: Path, old: "str | None", d: dict) -> "str | None":
    p.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    if old is not None:
        backup = str(p.with_name(p.name + BACKUP_SUFFIX))
        Path(backup).write_text(old, encoding="utf-8")
    fd, tmp = tempfile.mkstemp(prefix=p.name + ".", dir=str(p.parent))
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(json.dumps(d, ensure_ascii=False, indent=2) + "\n")
    os.replace(tmp, p)
    return backup


def install(settings=None, *, command: "str | None" = None, remove: bool = False) -> dict:
    p = Path(settings or Path.home() / ".claude" / "settings.json").expanduser()
    old, d = _load(p)
    before = json.dumps(d, sort_keys=True)
    hooks = d.setdefault("hooks", {})
    for ev, matcher in EVENTS:
        groups = hooks.setdefault(ev, [])
        entry = {"type": "command", "command": command}
        placed = False
        for g in groups:
            keep = []
            for h in g.get("hooks", []):
                if not is_ours(h.get("command", "")):
                    keep.append(h)
                elif not remove and not placed:
                    keep.append(dict(entry))          # 제자리에서 바꾼다
                    placed = True
            g["hooks"] = keep
        groups[:] = [g for g in groups if g.get("hooks")]
        if not remove and not placed:
            groups.append({**({"matcher": matcher} if matcher else {}), "hooks": [entry]})
        if not groups:
            del hooks[ev]
    if not hooks:
        del d["hooks"]
    changed = json.dumps(d, sort_keys=True) != before
    backup = _write(p, old, d) if changed else None
    return {"settings": str(p), "changed": changed, "installed": not remove, "backup": backup}


def main(cmd: str, argv) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog=f"python -m rlo.hooks {cmd}")
    ap.add_argument("--settings", default=None, help="Claude Code 설정 JSON(기본 ~/.claude/settings.json)")
    if cmd == "install-hook":
        ap.add_argument("--model", required=True, help="에이전트 도구의 ActionModel(action-model/1) JSON 경로")
        ap.add_argument("--mode", default="shadow", choices=("shadow", "enforce"))
        ap.add_argument("--grant", action="append", default=[])
        ap.add_argument("--purpose", default=None)
        ap.add_argument("--stall-threshold", type=int, default=None)
        ap.add_argument("--record", default=None)
        ap.add_argument("--python", default=None, help="훅을 돌릴 python(기본: 지금 이 python)")
    a = ap.parse_args(argv)
    try:
        command = None
        if cmd == "install-hook":
            from action.spec import ActionModel
            with open(a.model, encoding="utf-8") as f:
                ActionModel.from_dict(json.load(f))          # 틀린 모형은 깔지 않는다
            command = hook_command(model=a.model, mode=a.mode, grants=a.grant, purpose=a.purpose,
                                   stall_threshold=a.stall_threshold, record=a.record, python=a.python)
        out = install(a.settings, command=command, remove=cmd == "uninstall-hook")
    except Exception as e:                                    # 실패하면 아무것도 쓰지 않았다
        print(f"rlo {cmd}: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0
