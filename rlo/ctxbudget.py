"""컨텍스트 예산(context-budget/1, CMD-K17, BD-296) -- 긴 Claude Code 세션이 컨텍스트를 끝없이 키우지 않게 한다.

원형: baseline ops/ctxbudget/ctxbudget.py(2e30f28), 설명: baseline CONTEXT_BUDGET.md. 긴 자율 세션은 모형을 부를 때마다 쌓인
대화 전체를 다시 읽는다(캐시 읽기) -- 토큰은 '컨텍스트 크기 × 호출 수' 로 는다. PreToolUse 에서 컨텍스트를 재고:

    단계        조건                 훅 출력(문서에 있는 칸만)
    ok          ctx < soft           없음(가드에 맡김)
    warn        soft <= ctx < hard   additionalContext: 지금 단계만 끝내고 상태 파일을 쓰고 commit · push 한 뒤 차례를 끝내라
    checkpoint  ctx >= hard          checkpoint 도구(상태 파일 Write/Edit, cd · git add/commit/push/status 만 있는 Bash)가 아니면
                                     deny + 까닭. checkpoint 도구면 막지 않는다(additionalContext 만)
    unknown     usage 를 못 읽음      없음 -- 모름은 0 도, 예산 넘음도 아니다(기록만)

ctx = transcript 의 마지막 주 사슬(사이드체인 · `<synthetic>` 아님) assistant `usage` 의 input + cache_read + cache_creation.
**기본 예산은 없다**(BD-289): soft · hard 는 부른 쪽 설정에서만 온다. 설정이 없으면 이 단계는 꺼져 있다. 처음은 shadow(기록만).

원형과 다른 점(더 닫는 쪽): transcript 는 **꼬리만** 읽는다(마지막 주 사슬 응답을 찾을 때까지 창을 두 배로). checkpoint Bash 는
`` ` `` · `$(` · `${` · `|` · `>` · `<` · 홑 `&` 가 있으면 checkpoint 가 아니다(원형은 `git status | sh` 를 받았다).
checkpoint 도구에 `permissionDecision: "allow"` 를 내지 않는다 -- allow 는 Claude Code 의 권한 확인을 건너뛰므로,
예산은 막기만 하고 허락은 가드 · 사용자 설정에 맡긴다(가드의 ALLOW 도 아무것도 내지 않는다).
"""
from __future__ import annotations

import dataclasses
import json
import os
import re
import sys

NAME = "context-budget/1"
MODES = ("shadow", "enforce")
TAIL_BYTES = 256 * 1024                          # 처음 읽는 꼬리. 주 사슬 응답이 없으면 두 배씩 늘린다
_PART_OK = re.compile(r"^(cd\s+\S+|git\s+(-C\s+\S+\s+)?(add|commit|push|status)\b.*)$")
_SHELL = re.compile(r"`|\$\(|\$\{|\||>|<|(?<!&)&(?!&)")


@dataclasses.dataclass(frozen=True)
class Budget:
    soft: int
    hard: int
    state_paths: tuple = ("STATE.md",)
    mode: str = "shadow"

    def __post_init__(self):
        for k in ("soft", "hard"):
            v = getattr(self, k)
            if not isinstance(v, int) or isinstance(v, bool):
                raise ValueError(f"context budget {k}: an integer")
        if not 0 < self.soft <= self.hard:
            raise ValueError("context budget: need 0 < soft <= hard")
        sp = tuple(self.state_paths) if isinstance(self.state_paths, (list, tuple)) else ()
        if not sp or not all(isinstance(p, str) and p.strip() for p in sp):
            raise ValueError("context budget: state_paths is a non-empty list of paths")
        object.__setattr__(self, "state_paths", sp)
        if self.mode not in MODES:
            raise ValueError(f"context budget mode: one of {MODES}")

    @classmethod
    def of(cls, v) -> "Budget | None":
        """None · Budget · {soft, hard, state_paths?, mode?} -> Budget | None(꺼짐). 기본 예산은 없다."""
        if v is None or isinstance(v, Budget):
            return v
        if not isinstance(v, dict) or set(v) - {"soft", "hard", "state_paths", "mode"} or not {"soft", "hard"} <= set(v):
            raise ValueError("context budget: {soft, hard, state_paths?, mode?}")
        return cls(v["soft"], v["hard"], tuple(v.get("state_paths", ("STATE.md",))), v.get("mode", "shadow"))


def _usage_of(line: str) -> "dict | None":
    """그 줄이 주 사슬 assistant 응답이고 usage 가 있으면 usage. 아니면 None."""
    try:
        d = json.loads(line)
    except ValueError:
        return None
    if not isinstance(d, dict) or d.get("type") != "assistant" or d.get("isSidechain"):
        return None
    m = d.get("message") or {}
    if not isinstance(m, dict) or m.get("model") == "<synthetic>":
        return None
    u = m.get("usage")
    return u if isinstance(u, dict) else None


def context_tokens(transcript_path: str, tail_bytes: int = TAIL_BYTES) -> "int | None":
    """transcript 꼬리에서 마지막 주 사슬 assistant usage -> 컨텍스트 토큰. 못 읽으면 None(0 으로 메우지 않는다)."""
    try:
        with open(transcript_path, "rb") as f:
            size = f.seek(0, os.SEEK_END)
            n = tail_bytes
            while True:
                start = max(0, size - n)
                f.seek(start)
                chunk = f.read(size - start)
                lines = chunk.split(b"\n")
                if start > 0:
                    lines = lines[1:]                  # 꼬리의 첫 줄은 잘렸을 수 있다
                for raw in reversed(lines):
                    if not raw.strip():
                        continue
                    u = _usage_of(raw.decode("utf-8", "replace"))
                    if u is not None:
                        parts = [u.get(k) for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")]
                        if any(not isinstance(p, int) or isinstance(p, bool) for p in parts):
                            return None
                        return sum(parts)
                if start == 0:
                    return None
                n *= 2
    except OSError:
        return None


def is_checkpoint(tool_name: str, tool_input, state_paths: tuple) -> bool:
    """상태를 남기는 일만: 상태 파일 Write/Edit, 그리고 cd · git add/commit/push/status 만 있는 Bash(셸 특수 기호 없음)."""
    tool_input = tool_input if isinstance(tool_input, dict) else {}
    if tool_name in ("Write", "Edit"):
        p = str(tool_input.get("file_path", ""))
        return any(p == s or p.endswith("/" + s.lstrip("/")) for s in state_paths)
    if tool_name == "Bash":
        cmd = str(tool_input.get("command", ""))
        if _SHELL.search(cmd):
            return False
        parts = [c.strip() for c in re.split(r"&&|;|\n", cmd) if c.strip()]
        return bool(parts) and all(_PART_OK.match(c) for c in parts)
    return False


def note(ctx: int, b: Budget) -> str:
    return (f"[{NAME}] context is {ctx} tokens (soft {b.soft}, hard {b.hard}). Every model call re-reads all of it. "
            f"Finish only the current step, write your state to {b.state_paths[0]}, commit and push it, then end your turn. "
            "Do not read more issues or logs in this turn.")


def decide(ctx: "int | None", tool_name: str, tool_input, budget: Budget) -> "tuple[str, dict]":
    """-> (단계, PreToolUse 출력). 단계: ok · warn · checkpoint · unknown. 출력은 enforce 일 때 낼 것(shadow 는 부른 쪽이 버린다)."""
    if ctx is None:
        return "unknown", {}
    if ctx < budget.soft:
        return "ok", {}
    if ctx < budget.hard or is_checkpoint(tool_name, tool_input, budget.state_paths):
        return ("warn" if ctx < budget.hard else "checkpoint"), {
            "hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": note(ctx, budget)}}
    return "checkpoint", {"hookSpecificOutput": {
        "hookEventName": "PreToolUse", "permissionDecision": "deny",
        "permissionDecisionReason": f"[{NAME}] over the hard budget ({ctx} >= {budget.hard}): only checkpoint tools are "
                                    f"allowed (write {budget.state_paths[0]}, git add/commit/push). Then end your turn."}}


# ── 대본 추정(S4) -- 예산을 걸기 전에 기록된 세션으로 고른다 ───────────────────────────────────────────────────

def simulate(contexts: list, *, soft: int, hard: int, reset_to: int, calls_after_hard: int = 2) -> dict:
    """기록된 호출별 컨텍스트로 '예산이 있었다면' 을 셈한다(추정, L1 -- 원형 그대로). hard 를 넘은 뒤 checkpoint 호출
    calls_after_hard 번을 더 하고, 그다음 호출부터 새 컨텍스트(reset_to)에서 다시 시작해 기록의 증가분만큼 자란다고 본다.
    기록에서 컨텍스트가 크게 줄면(실제 압축) 그 뒤는 기록 그대로 둔다."""
    Budget(soft, hard)
    actual = sum(contexts)
    sim, shift, pending, resets, warn, prev = 0, 0, None, 0, 0, None
    for c in contexts:
        if prev is not None and c < prev * 0.6:
            shift = 0
        prev = c
        cur = max(1, c - shift)
        if pending is not None:
            if pending == 0:
                shift = c - reset_to
                cur = reset_to
                pending = None
                resets += 1
            else:
                pending -= 1
        elif cur >= hard:
            pending = calls_after_hard
        elif cur >= soft:
            warn += 1
        sim += cur
    return {"calls": len(contexts), "actual_tokens_read": actual, "simulated_tokens_read": sim,
            "saved_pct": round(100 * (1 - sim / actual), 1) if actual else 0.0, "resets": resets, "warn_calls": warn}


def contexts_from_l0(events) -> list:
    """Telemetry L0 사건(또는 원장 경로)의 llm.response 마다 input + cache_read + cache_creation(seq 차례). 칸이 빠진 응답은 뺀다."""
    if isinstance(events, (str, os.PathLike)):
        from telemetry import ledger
        events = ledger.read(events)
    out = []
    for e in sorted((e for e in events if e.get("type") == "llm.response"), key=lambda e: (e.get("run_id", ""), e["seq"])):
        d = e.get("data") or {}
        parts = [d.get(k) for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")]
        if all(isinstance(p, int) for p in parts):
            out.append(sum(parts))
    return out


def main(argv=None) -> int:
    """python -m rlo.ctxbudget simulate (--l0 <L0 원장> | --cc <Claude Code transcript>) --soft N --hard N [--reset-to N]"""
    import argparse
    ap = argparse.ArgumentParser(prog="python -m rlo.ctxbudget")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("simulate", help="기록된 세션으로 예산의 효과를 추정한다(오프라인)")
    src = s.add_mutually_exclusive_group(required=True)
    src.add_argument("--l0", help="Telemetry L0 원장(JSONL)")
    src.add_argument("--cc", help="Claude Code transcript(JSONL) -- Telemetry from_cc_jsonl 로 L0 로 바꾼다")
    s.add_argument("--soft", type=int, required=True)
    s.add_argument("--hard", type=int, required=True)
    s.add_argument("--reset-to", type=int, default=None, help="다시 시작한 컨텍스트(없으면 기록에서 처음 호출의 크기)")
    s.add_argument("--calls-after-hard", type=int, default=2)
    a = ap.parse_args(argv)
    if a.l0:
        events = a.l0
    else:
        from telemetry.collect import from_cc_jsonl
        events = from_cc_jsonl(a.cc, "cc")
    ctx = contexts_from_l0(events)
    if not ctx:
        print(json.dumps({"error": "no llm.response with usage"}))
        return 1
    r = simulate(ctx, soft=a.soft, hard=a.hard, reset_to=a.reset_to if a.reset_to is not None else ctx[0],
                 calls_after_hard=a.calls_after_hard)
    print(json.dumps(dict(r, soft=a.soft, hard=a.hard), sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
