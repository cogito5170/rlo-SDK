"""변이 시험 -- 지켜야 할 것을 하나씩 깨뜨린 사본에서 시험 전체를 돌려, 모두 빨개지는지(RED) 본다.

    python -B eval/mutation.py          (rlo-sdk 의 의존이 깔린 환경에서. rlo 자신은 깔려 있지 않아도 된다)

사본마다 저장소를 임시 디렉터리에 베끼고, 그 안에서 `python -B -m unittest discover` 를 돈다(낡은 .pyc 를 쓰지 않게 -B).
"""
from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
TEL = "35e8119968758becae94f226ce9ee0168967086e"
ACT, ACT_HEAD = "3995fdb3ba487f31d841d3e11b710e64f0d523db", "2f4791e5c33df6cf19d41f139d95d74e4b86b42e"
GUARD_REQ = '    "guard @ git+https://github.com/cogito5170/guard@be871b9d89fe77badeef901caaa75edc1848f13c",\n'
GUARD_PIN = '    "guard": ("guard", _GH + "guard", "be871b9d89fe77badeef901caaa75edc1848f13c"),\n'

# (이름, [(파일, 옛 글, 새 글), ...])
MUTANTS = [
    # guard 를 선택으로
    ("pyproject: guard 를 extras 로", [("pyproject.toml", GUARD_REQ, ""),
                                      ("pyproject.toml", 'sensor = [', 'guard = ["guard @ git+https://github.com/cogito5170/guard@be871b9d89fe77badeef901caaa75edc1848f13c"]\nsensor = [')]),
    ("_pins: guard 를 extras 로", [("rlo/_pins.py", GUARD_PIN, ""),
                                  ("rlo/_pins.py", 'EXTRAS = {\n', 'EXTRAS = {\n    "guard": {"guard": ("guard", _GH + "guard", "be871b9d89fe77badeef901caaa75edc1848f13c")},\n')]),
    ("autonomy: guard · 실행기 · VERIFY 없이도 선다", [("rlo/autonomy.py", "        if missing:\n", "        if False:\n")]),
    # snapshot 길을 냄
    ("autonomy: DC 길을 꽂지 않음(snapshot 길)", [("rlo/autonomy.py", "        self.runtime.state_reader = MSStateReader(builder, purpose)\n", "")]),
    ("autonomy: state_reader 를 끄는 자리", [("rlo/autonomy.py", "run_state=None, risky=None):", "run_state=None, risky=None, state_reader=True):")]),
    ("autonomy: handle 이 결정 문맥 없이도 돎", [("rlo/autonomy.py", "        if self.runtime.state_reader is None:\n", "        if False:\n")]),
    ("autonomy: risky 를 넘기지 않음", [("rlo/autonomy.py", "run_state=run_state, risky=risky, **kw)", "run_state=run_state, **kw)")]),
    ("autonomy: 기본 guard_mode enforce", [("rlo/autonomy.py", 'guard_mode: str = "shadow"', 'guard_mode: str = "enforce"')]),
    # 훅
    ("hooks: ALLOW 에 \"allow\" 를 냄", [("rlo/hooks.py",
        '+ hint)\n            return {}\n',
        '+ hint)\n            return {"hookSpecificOutput": {"hookEventName": PRE, "permissionDecision": "allow"}}\n')]),
    ("hooks: shadow 도 막음", [("rlo/hooks.py", 'if self.mode == ENFORCE and res.verdict != "ALLOW":', 'if res.verdict != "ALLOW":')]),
    ("hooks: 판정 오류의 메시지가 샘", [("rlo/hooks.py", 'deny(f"guard error: {type(e).__name__}")', 'deny(f"guard error: {e}")')]),
    ("hooks: 판정 오류를 enforce 에서도 허락", [("rlo/hooks.py", 'deny(f"guard error: {type(e).__name__}") if self.mode == ENFORCE else {}',
                                            'deny(f"guard error: {type(e).__name__}") if False else {}')]),
    ("hooks: 실행 뒤 훅이 판정함", [("rlo/hooks.py", "        self.observe(input_data)                   # 거두지 않는다",
                                    "        return self.pre_tool_use(input_data)  # 거두지 않는다")]),
    ("hooks: 실행 뒤 훅이 거둠", [("rlo/hooks.py", "        self.observe(input_data)                   # 거두지 않는다",
                                  "        self.observe(input_data); self.judge.collect(input_data)  # 거두지 않는다")]),
    ("hooks: 거둔 것을 다시 씀(다시 거두지 않음)", [("rlo/hooks.py",
        "        return run, from_l0(events, clock=self.clock, **kw)\n",
        "        if not hasattr(self, '_memo'):\n            self._memo = from_l0(events, clock=self.clock, **kw)\n"
        "        return run, self._memo\n")]),
    ("hooks: 지금 호출을 빼지 않음", [("rlo/hooks.py", "self.collect(input_data, exclude_current=True)",
                                     "self.collect(input_data, exclude_current=False)")]),
    ("hooks: 나란히 부른 다른 호출도 뺌", [("rlo/hooks.py",
        'if e["type"] == "tool.start" and e["data"].get("tool_use_id") == tool_use_id}',
        'if e["type"] == "tool.start"}')]),
    ("hooks: 지금 호출의 tool.end 를 남김", [("rlo/hooks.py",
        'e["type"] in ("tool.start", "tool.end") and e["data"].get("tool_index") in drop',
        'e["type"] == "tool.start" and e["data"].get("tool_index") in drop')]),
    ("hooks: Stop 이 막음", [("rlo/hooks.py", "            self.record(\"collect_error\", {\"event\": ev, \"exception\": type(e).__name__})\n        return {}\n",
                            "            self.record(\"collect_error\", {\"event\": ev, \"exception\": type(e).__name__})\n"
                            "        return deny(\"stop\")\n")]),
    ("hooks: Stop · SessionEnd 에서 거두지 않음", [("rlo/hooks.py", "        if ev in (STOP, SESSION_END):\n            return self.end(input_data)\n", "")]),
    ("hooks: 모형의 도구를 내놓지 않음", [("rlo/hooks.py", "offers = {name: [None] for name in self.gmodel.specs}", "offers = {}")]),
    ("hooks: 의도가 다른 문맥 id 를 가짐", [("rlo/hooks.py", "intent_material(input_data, dcv.dc_id, self.policy)",
                                          "intent_material(input_data, 'dc-0000000000000000', self.policy)")]),
    ("hooks: 기록에 도구 입력 평문", [("rlo/hooks.py", '"tool_name": it.action,', '"tool_name": it.action, "args": it.args,')]),
    ("hooks: 기본 목적이 agent_tool_call 이 아님", [("rlo/hooks.py", 'PURPOSE = "agent_tool_call"', 'PURPOSE = "execution_control"')]),
    ("hooks: 명령 훅 설정 오류에 enforce 도 허락", [("rlo/hooks.py", '                if a.mode == ENFORCE and data.get("hook_event_name") == PRE:\n',
                                                 "                if False:\n")]),
    ("hooks: Sensor 없이도 훅이 섬", [("rlo/hooks.py", "        except ImportError as e:\n            raise ImportError(", "        except ImportError as e:\n            pass\n        if False:\n            raise ImportError(")]),
    ("install: 제자리에서 바꾸지 않고 끝으로 옮김", [("rlo/install.py", "                elif not remove and not placed:\n", "                elif False:\n")]),
    ("install: 남의 훅까지 뗌", [("rlo/install.py", "                if not is_ours(h.get(\"command\", \"\")):\n                    keep.append(h)\n",
                                "                if not is_ours(h.get(\"command\", \"\")) and remove is False:\n                    keep.append(h)\n")]),
    ("install: 백업 이름이 .bak-mba", [("rlo/install.py", 'BACKUP_SUFFIX = ".bak-rlo"', 'BACKUP_SUFFIX = ".bak-mba"')]),
    ("install: 백업을 안 남김", [("rlo/install.py", "    if old is not None:\n        backup", "    if False:\n        backup")]),
    ("install: 깨진 JSON 을 빈 설정으로 덮음", [("rlo/install.py", "        raise SettingsError(f\"{p}: JSON 이 아니다 ({e.msg}) -- 쓰지 않는다\") from e\n",
                                          "        return text, {}\n")]),
    ("install: 틀린 모형도 깖", [("rlo/install.py", "                ActionModel.from_dict(json.load(f))          # 틀린 모형은 깔지 않는다\n", "                pass\n")]),
    ("install: 'rlo' 글자만 들어도 우리 것", [("rlo/install.py", r'_OURS = re.compile(r"(?:^|\s)-m\s+rlo\.hooks(?:\s|$)")', '_OURS = re.compile(r"rlo")')]),
    ("install: Stop 에 matcher", [("rlo/install.py", 'EVENTS = (("PreToolUse", "*"), ("Stop", None), ("SessionEnd", None))', 'EVENTS = (("PreToolUse", "*"), ("Stop", "*"), ("SessionEnd", None))')]),
    ("hooks: 기록에 칸 이름이 없음", [("rlo/hooks.py", '                                  "tool_input_keys": sorted(it.args),', '')]),
    ("hooks: 기록에 칸 값까지", [("rlo/hooks.py", '"tool_input_keys": sorted(it.args),', '"tool_input_keys": sorted(it.args), "args": it.args,')]),
    ("data: 예시 모형에서 Grep 을 뺌", [("rlo/data/cc_tools_model.json", '"name": "Grep"', '"name": "Grep-gone"')]),
    ("data: Edit 의 replace_all 을 필수로", [("rlo/data/cc_tools_model.json",
        '"replace_all": {\n     "type": "bool",\n     "required": false\n    }', '"replace_all": {\n     "type": "bool"\n    }')]),
    ("data: Grep 에 잰 적 없는 칸 glob", [("rlo/data/cc_tools_model.json", '"pattern": {\n     "type": "string"\n    }',
        '"pattern": {\n     "type": "string"\n    },\n    "glob": {\n     "type": "string"\n    }')]),
    ("hooks: 모르는 모드를 받음", [("rlo/hooks.py", "        if mode not in (SHADOW, ENFORCE):\n", "        if False:\n")]),
    # 판본 목록
    ("_pins: Telemetry sha 가 pyproject 와 어긋남", [("rlo/_pins.py", TEL, "0" * 40)]),
    ("둘 다: action 을 stage-3 머리 2f4791e 로", [("rlo/_pins.py", ACT, ACT_HEAD), ("pyproject.toml", ACT, ACT_HEAD)]),
    ("versions: installed 가 고정 목록을 베낌", [("rlo/versions.py", '"installed": {name: _installed_commit(pin[0])', '"installed": {name: pin[2]')]),
    ("versions: 계약 판본을 잘못 읽음", [("rlo/versions.py", '("guard.forms", "GUARD_SCHEMA")', '("guard.forms", "VALIDATION_SCHEMA")')]),
    ("versions: SDK 판본이 pyproject 와 어긋남", [("rlo/versions.py", '__version__ = "0.5.0"', '__version__ = "0.5.1"')]),
    # 꼴이 틀린 입력(CMD-K10 S1)
    ("hooks: 꼴이 틀린 입력을 enforce 에서도 허락", [("rlo/hooks.py",
        'return deny(f"rlo hook input error: {problem}") if self.mode == ENFORCE else {}',
        'return deny(f"rlo hook input error: {problem}") if False else {}')]),
    ("hooks: 꼴 오류 까닭에 문제가 없음", [("rlo/hooks.py", 'deny(f"rlo hook input error: {problem}")', 'deny("rlo hook input error")')]),
    ("hooks: 꼴 오류를 기록하지 않음", [("rlo/hooks.py",
        '        self.record("input_error", {"event": ev if isinstance(ev, str) else None, "problem": problem})\n', '')]),
    ("hooks: hook_event_name 없는 입력을 통과(K10 전)", [("rlo/hooks.py",
        '        return "missing field hook_event_name" if ev is None else "field hook_event_name is not a non-empty string"',
        '        return None')]),
    ("hooks: PreToolUse 칸을 검사하지 않음", [("rlo/hooks.py", "    if ev == PRE:\n        for name, kind in", "    if False:\n        for name, kind in")]),
    ("hooks: JSON 아닌 입력이 예외로 나감(종료 1, K10 전)", [("rlo/hooks.py",
        '    except ValueError:\n        return None, "input is not JSON"', '    except KeyError:\n        return None, "input is not JSON"')]),
    ("hooks: 예상 못 한 예외에 종료 1", [("rlo/hooks.py", "    except Exception as e:                         # 예상 못 한",
                                       "    except KeyError as e:                          # 예상 못 한")]),
    # 낡음만으로 생긴 D 의 안내(CMD-K10 S2)
    ("hooks: 낡음 D 에 안내 없음", [("rlo/hooks.py", 'hint = STALE_HINT if stale_only(res, dcv) else ""', 'hint = ""')]),
    ("hooks: UNKNOWN D 에도 안내", [("rlo/hooks.py", "            and set(dcv.missing_required) <= set(dcv.stale_keys))", "            and True)")]),
    ("hooks: 다른 규칙이 함께 걸려도 안내", [("rlo/hooks.py",
        'return (res.rule == "D" and all(r.startswith("[D]") for r in res.reasons)', 'return (res.rule == "D"')]),
    ("hooks: 낡음 D 를 허락으로 바꿈", [("rlo/hooks.py",
        '                return deny(f"guard {res.verdict}({res.rule}): " + "; ".join(res.reasons)[:500] + hint)',
        '                return {} if hint else deny(f"guard {res.verdict}({res.rule}): " + "; ".join(res.reasons)[:500] + hint)')]),
    # 기록을 읽는 도구(CMD-K9)
    ("suggest-model: 새 칸을 필수로 초안", [("rlo/suggest_model/__init__.py", '"required": False,  # 이전 호출에 없던 칸', '"required": True,  # 이전 호출에 없던 칸')]),
    ("suggest-model: 새 칸에 초안 표시 없음", [("rlo/suggest_model/__init__.py", '"note": "[DRAFT] 기록에서 찾은 새 칸"', '"note": "기록에서 찾은 새 칸"')]),
    ("suggest-model: 기록에 없는 칸도 넣음", [("rlo/suggest_model/__init__.py", "            new_fields = record_fields - model_fields\n",
                                          "            new_fields = (record_fields | {\"timeout\"}) - model_fields\n")]),
    # 시험 도구 자신(CMD-K9): 뜻 비교가 빌드 꼴을 받아들이되 sha 는 여전히 본다
    ("시험: 요구를 글자 그대로 비교(K9 전)", [("tests/test_versions.py",
        "    return (_canon(r.name), tuple(sorted(_canon(x) for x in r.extras)), str(r.specifier), r.url, marker)",
        "    return (s.split(\";\")[0].strip(), (), \"\", s.split(\";\")[0].strip(), marker)")]),
    ("시험: 비교가 sha 를 버림", [("tests/test_versions.py",
        "    return (_canon(r.name), tuple(sorted(_canon(x) for x in r.extras)), str(r.specifier), r.url, marker)",
        "    return (_canon(r.name), tuple(sorted(_canon(x) for x in r.extras)), str(r.specifier), r.url.rsplit(\"@\", 1)[0], marker)")]),
    ("시험: packaging 없을 때의 비교가 sha 를 버림", [("tests/test_versions.py",
        "    return (_canon(name), extras, \"\", url.strip() if at else None, marker)",
        "    return (_canon(name), extras, \"\", url.strip().rsplit(\"@\", 1)[0] if at else None, marker)")]),
    # 예제
    ("examples: 설정 예가 enforce", [("examples/claude_code_settings.json", "--mode shadow --grant Bash", "--mode enforce --grant Bash")]),
    ("examples: Agent SDK 예가 SessionEnd 를 검", [("examples/agent_sdk.py", '"PostToolUseFailure", "Stop")', '"PostToolUseFailure", "Stop", "SessionEnd")')]),
    ("example: 모드를 입구에 넘기지 않음", [("rlo/example.py", "                           guard_mode=mode, l0=sink)", "                           l0=sink)")]),
]


def run(tree: pathlib.Path) -> int:
    return subprocess.run([sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests", "-t", "."], cwd=tree,
                          capture_output=True, text=True).returncode


def copy() -> pathlib.Path:
    d = pathlib.Path(tempfile.mkdtemp(prefix="rlo-mut-"))
    for name in ("rlo", "tests", "examples", "pyproject.toml"):
        src = ROOT / name
        (shutil.copytree if src.is_dir() else shutil.copy)(src, d / name)
    return d


def main() -> int:
    base = copy()
    try:
        if run(base) != 0:
            print("기준 사본이 초록이 아니다 -- 변이를 돌리지 않는다")
            return 2
    finally:
        shutil.rmtree(base)
    red = 0
    for name, edits in MUTANTS:
        d = copy()
        try:
            for f, old, new in edits:
                p = d / f
                text = p.read_text(encoding="utf-8")
                if text.count(old) != 1:
                    raise SystemExit(f"변이 '{name}': {f} 에서 옛 글을 한 번 찾지 못함")
                p.write_text(text.replace(old, new), encoding="utf-8")
            rc = run(d)
        finally:
            shutil.rmtree(d)
        red += rc != 0
        print(f"{'RED  ' if rc else 'GREEN'} {name}")
    print(f"{red}/{len(MUTANTS)} RED")
    return 0 if red == len(MUTANTS) else 1


if __name__ == "__main__":
    sys.exit(main())
