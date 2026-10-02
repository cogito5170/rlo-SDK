"""예제 한 바퀴 -- `python -m rlo.example`. 설치한 패키지만 쓴다(옆 저장소 경로 없이).

MS 가 함께 싣는 예시 세계(`ms/examples/datacenter.json` + 관측)에서 "srv07 을 throttle" 한 요청을 shadow · enforce 로 돌린다:
결정(DC 길) → Guard → 실행기 → VERIFY. 처리기를 주지 않았으므로 throttle 은 MS 의 effect 틀(모의)로 돈다 -- 부작용 없음.
L0 는 메모리 sink 에만 쓰고, 원장은 쓰지 않는다.

    $ python -m rlo.example
    shadow   executed  guard=ALLOW  exec=throttle srv07  verify=VERIFIED/MET  L0=action.dispatch,action.result
    enforce  executed  …
"""
from __future__ import annotations

import copy
import json
import sys
from importlib import resources

from .autonomy import Autonomy

TASK = "srv07 을 throttle"


def world():
    """MS 패키지에 실린 예시 세계. throttle 에 사후조건을 붙여 VERIFY 가 돌게 한다(예시 명세에는 사후조건이 없다)."""
    base = resources.files("ms") / "examples"
    spec = json.loads((base / "datacenter.json").read_text(encoding="utf-8"))
    obs = [json.loads(line) for line in (base / "datacenter_telemetry.jsonl").read_text(encoding="utf-8").splitlines() if line]
    tools = copy.deepcopy(spec["tools"])
    for t in tools:
        if t["name"] == "throttle":
            t.update(postcondition=[{"entity": "$target", "pred": ["throttled", "==", True]}], window_ms=60000)
    return spec, obs, tools


def one_turn(mode: str):
    """한 바퀴. (Autonomy, Result, L0 사건 목록) 을 돌려준다."""
    from ms.providers import make_provider
    from telemetry.ledger import MemorySink
    spec, obs, tools = world()
    sink = MemorySink()
    now = spec["now"]
    a = Autonomy.from_spec(spec, obs, clock=lambda: now, actions=tools, llm=make_provider("sim-claude"),
                           guard_mode=mode, l0=sink)
    a.open_session("example", {"token_budget": 1000})
    return a, a.handle(TASK, queries=spec["queries"]), sink.events


def summary(mode: str) -> "tuple[bool, str]":
    a, r, events = one_turn(mode)
    acts = [e["type"] for e in events if e["type"].startswith(("action.", "tool."))]
    g = [x["guard"]["verdict"] for x in r.guards]
    x = [f'{e["command"]["action"]} {e["command"]["target"]}' for e in r.executions]
    v = [f'{e["record"]["result"]}/{e["record"]["reason"]}' for e in r.verifications]
    ok = (r.outcome == "executed" and g == ["ALLOW"] and x == ["throttle srv07"] and v == ["VERIFIED/MET"]
          and acts == ["action.dispatch", "action.result"]
          and r.raw["decision"]["state_source"]["kind"] == "state_reader"
          and r.executions[0]["command"]["decision_ref"] == r.decision_id)
    line = (f"{mode:8} {r.outcome}  guard={','.join(g)}  exec={','.join(x)}  verify={','.join(v)}  L0={','.join(acts)}"
            f"  {'OK' if ok else 'FAIL'}")
    return ok, line


def main() -> int:
    results = [summary(mode) for mode in ("shadow", "enforce")]
    for _, line in results:
        print(line)
    return 0 if all(ok for ok, _ in results) else 1


if __name__ == "__main__":
    sys.exit(main())
