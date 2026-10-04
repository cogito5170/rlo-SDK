"""CMD-K15 rev 4 -- ga-sdk 의 지금 프롬프트 글과 check_plan 판정을 시험 fixture 로 뜬다. 손으로만 돌린다(시험은 ga 를 들이지 않는다).

    GA_SDK=<ga-sdk 438a34a 의 사본> python eval/capture_pspec_fixtures.py > tests/fixtures/pspec/ga_438a34a.json

글은 ga 의 코드 그대로 짓는다: `protocol(cfg)` 와 `Supervisor._prompt(rec)`(첫 턴 · --resume 턴 · --resume 없는 agy 턴).
`_prompt` 는 가짜 self(설정 · 호스트의 resumes · 상태 · 결과 글)로 부른다 -- Supervisor 를 띄우지 않는다.
사례 · 도구 표 · 작업 글은 baseline ops/pspec/run.py(7dc9d13 -- 사례 17)의 것 그대로다.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

GA = os.environ.get("GA_SDK")
if not GA:
    raise SystemExit("GA_SDK=<ga-sdk 사본 경로> 가 필요하다")
sys.path.insert(0, GA)
from ga import gemini as G  # noqa: E402

TOOLS = {"read_file": {"about": "read a file from the workspace"},
         "search": {"about": "search the web; returns titles and urls"},
         "list_dir": {"about": "list a directory"},
         "write_note": {"about": "append a note to the user's notes"},
         "fetch_url": {"about": "fetch a url as text"},
         "noop": {}}
TABLES = {"six": TOOLS, "empty": {}, "blank_about": {"x": {"about": ""}}}
TASK = "Find the three most recent design notes about the hero banner and summarise what changed between them."
RESULTS = [{"id": "a", "tool": "search", "text": "3 hits: notes/hero-v1.md, notes/hero-v2.md, notes/hero-v3.md"},
           {"id": "b", "tool": "list_dir", "text": "notes/: hero-v1.md hero-v2.md hero-v3.md footer.md"}]
ASK = "read the three hero notes and compare them"
PLANS = [
    {"schema": "ga-gemini-plan/1", "steps": [{"id": "a", "tool": "search", "args": {"q": "x"}}], "next": {"prompt": "go", "after": ["a"]}, "say": "hi"},
    {"schema": "ga-gemini-plan/1", "steps": [], "next": None},
    {"schema": "ga-gemini-plan/1", "steps": [{"id": "a", "tool": "nope"}]},
    {"schema": "ga-gemini-plan/2", "steps": []},
    {"schema": "ga-gemini-plan/1", "steps": [{"id": "a", "tool": "search", "after": ["b"]}]},
    {"schema": "ga-gemini-plan/1", "steps": [{"id": "a", "tool": "search"}, {"id": "a", "tool": "noop"}]},
    {"schema": "ga-gemini-plan/1", "steps": [{"id": "this-id-is-too-long", "tool": "noop"}]},
    {"schema": "ga-gemini-plan/1", "steps": [{"id": "a", "tool": "noop", "args": []}]},
    {"schema": "ga-gemini-plan/1", "next": {"prompt": "  "}},
    {"schema": "ga-gemini-plan/1", "next": {"prompt": "x", "after": ["z"]}},
    {"schema": "ga-gemini-plan/1", "extra": 1},
    {"schema": "ga-gemini-plan/1", "say": 3},
    {"schema": "ga-gemini-plan/1", "steps": [{"id": "s%d" % i, "tool": "noop"} for i in range(17)]},
    {"schema": "ga-gemini-plan/1", "steps": [{"id": "s%d" % i, "tool": "noop"} for i in range(16)]},
    {"schema": "ga-gemini-plan/1", "steps": [{"id": "a", "tool": "noop", "zz": 1}]},
    "not an object",
    {"schema": "ga-gemini-plan/1", "steps": [{"id": "a", "tool": "noop", "after": ["a"]}]},   # 17 번째(BD-292 P1, run.py 7dc9d13)
]


def prompt(cfg, rec, resumes, results=()):
    """ga 의 Supervisor._prompt 를 가짜 self 로 부른다."""
    by = {r["id"]: r for r in results}
    fake = SimpleNamespace(cfg=cfg, cli=SimpleNamespace(resumes=resumes),
                           st={"steps": [{"first": True, "prompt": TASK}]},
                           _rec=lambda a: {"plan_id": by[a]["id"], "tool": by[a]["tool"]},
                           _result_text=lambda a: by[a]["text"])
    return G.Supervisor._prompt(fake, rec)


def main():
    out = {"source": {"repo": "cogito5170/ga-sdk",
                      "sha": subprocess.run(["git", "-C", GA, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip(),
                      "functions": ["ga.gemini.protocol", "ga.gemini.Supervisor._prompt", "ga.gemini.check_plan"]},
           "task": TASK, "results": RESULTS, "ask": ASK, "tables": {}, "plans": []}
    for name, tools in TABLES.items():
        cfg = G.GeminiConfig(root=Path("."), tools=tools)
        out["tables"][name] = {
            "tools": tools,
            "protocol": G.protocol(cfg),
            "first": prompt(cfg, {"first": True, "prompt": TASK}, True),
            "turn": prompt(cfg, {"prompt": ASK, "needs": ["a", "b"]}, True, RESULTS),
            "turn_noresume": prompt(cfg, {"prompt": ASK, "needs": ["a", "b"]}, False, RESULTS),
        }
    for p in PLANS:
        out["plans"].append({"plan": p, "ga_problems": G.check_plan(p, TOOLS)})
    json.dump(out, sys.stdout, ensure_ascii=False, indent=1, sort_keys=True)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
