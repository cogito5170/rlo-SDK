"""변이 시험 -- 지켜야 할 것을 하나씩 깨뜨린 사본에서 시험 전체를 돌려, 모두 빨개지는지(RED) 본다.

    python -B eval/mutation.py [이름 조각 …]   (rlo-sdk 의 의존이 깔린 환경에서. 조각을 주면 이름에 그것이 든 변이만)

사본마다 저장소를 임시 디렉터리에 베끼고, 그 안에서 `python -B -m unittest discover` 를 돈다(낡은 .pyc 를 쓰지 않게 -B).
"""
from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
TEL = "f6c7ae26d336965d3558092517e97d37d222c4ea"
ACT, ACT_HEAD = "9d6729fc6809d68d2b5d55b4ad2fd37e5281d998", "2f4791e5c33df6cf19d41f139d95d74e4b86b42e"
GUARD_REQ = '    "guard @ git+https://github.com/cogito5170/guard@3f7b2352c9c9eb198c4020ea5cdd125af135425f",\n'
GUARD_PIN = '    "guard": ("guard", _GH + "guard", "3f7b2352c9c9eb198c4020ea5cdd125af135425f"),\n'

# (이름, [(파일, 옛 글, 새 글), ...])
MUTANTS = [
    # guard 를 선택으로
    ("pyproject: guard 를 extras 로", [("pyproject.toml", GUARD_REQ, ""),
                                      ("pyproject.toml", 'sensor = [', 'guard = ["guard @ git+https://github.com/cogito5170/guard@3f7b2352c9c9eb198c4020ea5cdd125af135425f"]\nsensor = [')]),
    ("_pins: guard 를 extras 로", [("rlo/_pins.py", GUARD_PIN, ""),
                                  ("rlo/_pins.py", 'EXTRAS = {\n', 'EXTRAS = {\n    "guard": {"guard": ("guard", _GH + "guard", "3f7b2352c9c9eb198c4020ea5cdd125af135425f")},\n')]),
    ("autonomy: guard · 실행기 · VERIFY 없이도 선다", [("rlo/autonomy.py", "        if missing:\n", "        if False:\n")]),
    # snapshot 길을 냄
    ("autonomy: DC 길을 꽂지 않음(snapshot 길)", [("rlo/autonomy.py", "        self.runtime.state_reader = MSStateReader(builder, purpose)\n", "")]),
    ("autonomy: state_reader 를 끄는 자리", [("rlo/autonomy.py", "run_state=None, risky=None, governor=None,",
                                                       "run_state=None, risky=None, state_reader=True, governor=None,")]),
    ("autonomy: handle 이 결정 문맥 없이도 돎", [("rlo/autonomy.py", "        if self.runtime.state_reader is None:\n", "        if False:\n")]),
    ("autonomy: risky 를 넘기지 않음", [("rlo/autonomy.py", "run_state=run_state, risky=risky, **kw)", "run_state=run_state, **kw)")]),
    ("autonomy: 기본 guard_mode enforce", [("rlo/autonomy.py", 'guard_mode: str = "shadow"', 'guard_mode: str = "enforce"')]),
    # 훅
    ("hooks: ALLOW 에 \"allow\" 를 냄", [("rlo/hooks.py",
        '+ hint + R.line(obj))\n            return {}\n',
        '+ hint + R.line(obj))\n            return {"hookSpecificOutput": {"hookEventName": PRE, "permissionDecision": "allow"}}\n')]),
    ("hooks: shadow 도 막음", [("rlo/hooks.py", 'if self.mode == ENFORCE and res.verdict != "ALLOW":', 'if res.verdict != "ALLOW":')]),
    ("hooks: 판정 오류의 메시지가 샘", [("rlo/hooks.py", 'deny(f"guard error: {type(e).__name__}" + R.line(obj))', 'deny(f"guard error: {e}" + R.line(obj))')]),
    ("hooks: 판정 오류를 enforce 에서도 허락", [("rlo/hooks.py", 'deny(f"guard error: {type(e).__name__}" + R.line(obj)) if self.mode == ENFORCE else {}',
                                            'deny(f"guard error: {type(e).__name__}" + R.line(obj)) if False else {}')]),
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
    ("install: 틀린 모형도 깖", [("rlo/install.py", "            load_model(a.model)                               # 틀린 모형은 깔지 않는다", "            pass                                              # 틀린 모형은 깔지 않는다")]),
    ("install: 'rlo' 글자만 들어도 우리 것", [("rlo/install.py", r'_OURS = re.compile(r"(?:^|\s)-m\s+rlo\.hooks(?:\s|$)")', '_OURS = re.compile(r"rlo")')]),
    ("install: Stop 에 matcher", [("rlo/install.py", 'EVENTS = (("PreToolUse", "*"), ("Stop", None), ("SessionEnd", None))', 'EVENTS = (("PreToolUse", "*"), ("Stop", "*"), ("SessionEnd", None))')]),
    ("hooks: 기록에 칸 이름이 없음", [("rlo/hooks.py", '                   "tool_input_keys": sorted(it.args),', '')]),
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
    ("versions: SDK 판본이 pyproject 와 어긋남", [("rlo/versions.py", '__version__ = "0.11.1"', '__version__ = "0.11.2"')]),
    # 꼴이 틀린 입력(CMD-K10 S1)
    ("hooks: 꼴이 틀린 입력을 enforce 에서도 허락", [("rlo/hooks.py",
        'return deny(f"rlo hook input error: {problem}" + R.line(obj)) if self.mode == ENFORCE else {}',
        'return deny(f"rlo hook input error: {problem}" + R.line(obj)) if False else {}')]),
    ("hooks: 꼴 오류 까닭에 문제가 없음", [("rlo/hooks.py", 'deny(f"rlo hook input error: {problem}" + R.line(obj))', 'deny("rlo hook input error" + R.line(obj))')]),
    ("hooks: 꼴 오류를 기록하지 않음", [("rlo/hooks.py",
        '        self.record("input_error", {"event": ev if isinstance(ev, str) else None, "problem": problem, "react": obj})\n',
        '')]),
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
        '                return deny(f"guard {res.verdict}({res.rule}): " + "; ".join(res.reasons)[:500] + hint + R.line(obj))',
        '                return {} if hint else deny(f"guard {res.verdict}({res.rule}): " + "; ".join(res.reasons)[:500] + hint)')]),
    # 턴 안 ReAct(CMD-K11)
    ("react: 대체가 허가 없는 external 도구를 고름(허가를 넓힘)", [("rlo/react.py",
        "        if spec.risk in GRANT_RISKS and s not in gmodel.grants:\n            continue\n", "")]),
    ("react: 대체가 모형 밖 도구를 고름", [("rlo/react.py",
        "        if s == tool or spec is None:\n            continue\n        if spec.risk",
        "        if s == tool:\n            continue\n        if spec is not None and spec.risk")]),
    ("react: 되풀이 상한 없음", [("rlo/react.py", "    if attempt > MAX_ATTEMPTS:\n        kind = \"report\"",
                                 "    if False:\n        kind = \"report\"")]),
    ("react: 자유 글 대안", [("rlo/react.py", '    obj = {"kind": kind, "rule": rule,',
                             '    obj = {"text": f"try another way around {rule}", "kind": kind, "rule": rule,')]),
    ("react: 표 밖 종류", [("rlo/react.py", '    ("A4", "unknown_args"): "drop_unknown_args",', '    ("A4", "unknown_args"): "retry_anyhow",')]),
    ("react: escalate 가 늘 거짓", [("rlo/react.py", '"escalate": kind == "report"}', '"escalate": False}')]),
    ("react: A7 을 먼저 보지 않음", [("rlo/react.py", '    if "A7" in failing:', '    if res.rule == "A7":')]),
    ("react: 낡음 D 를 wait_previous 로", [("rlo/react.py", '("stale" if stale_only(res, dcv) else "unavailable")', '"unavailable"')]),
    ("react: 그 도구가 지나도 되풀이 수를 이어 감", [("rlo/react.py",
        "== (rule, cause) else 0", "== (rule, cause) else n")]),
    ("react: transcript 의 거부를 세지 않음", [("rlo/hooks.py",
        '            prior = R.prior_denies(input_data["transcript_path"], input_data["tool_name"], rule, cause)',
        "            prior = 0")]),
    ("hooks: 기록에 react 없음", [("rlo/hooks.py", 'self.record("guard", dict(row, react=obj) if obj else row)', 'self.record("guard", row)')]),
    ("hooks: 거부 까닭에 react 줄 없음", [("rlo/hooks.py", '"; ".join(res.reasons)[:500] + hint + R.line(obj))',
                                         '"; ".join(res.reasons)[:500] + hint)')]),
    ("hooks: 명령 훅이 대체표를 넘기지 않음", [("rlo/hooks.py", "clock=clock, substitutes=substitutes, channels=channels, health_ttl=a.health_ttl,",
                                                "clock=clock, channels=channels, health_ttl=a.health_ttl,")]),
    # D 는 세션을 통로에서 끊지 않는다(CMD-K13)
    ("hooks: 상태를 몰라도(UNKNOWN) 통로 도구를 지나게 함", [("rlo/hooks.py",
        '            obj, stale = None, res.verdict != "ALLOW" and stale_only(res, dcv)',
        '            obj, stale = None, res.verdict != "ALLOW" and res.rule == "D"')]),
    ("react: 고정되지 않은 통로 인자도 받음", [("rlo/react.py",
        "        if c[\"args\"] is not None and all(k in tool_input and tool_input[k] == v and type(tool_input[k]) is type(v)",
        "        if c[\"args\"] is not None and all(True")]),
    ("react: 통로 Bash 의 셸 특수 문자를 받음", [("rlo/react.py",
        "            if not isinstance(cmd, str) or SHELL_META.search(cmd):", "            if not isinstance(cmd, str):")]),
    ("react: 통로 Bash 의 명령 앞부분을 보지 않음", [("rlo/react.py",
        '            if argv[:len(c["argv_prefix"])] == c["argv_prefix"]:', "            if argv:")]),
    ("react: 올림이 막힌 통로 도구를 가리킴(D)", [("rlo/react.py",
        "        if spec.risk in gmodel.risky and dcv is not None and not dcv.complete and not stale_only_d:\n            continue",
        "        if False:\n            continue")]),
    ("react: 올림이 허가 없는 통로 도구를 가리킴", [("rlo/react.py",
        '        if spec.risk in GRANT_RISKS and c["tool"] not in gmodel.grants:\n            continue\n        if spec.risk in gmodel.risky',
        "        if spec.risk in gmodel.risky")]),
    ("hooks: 올림이 통로 도구를 이름 짓지 않음", [("rlo/hooks.py", '            if ch is not None:\n                obj["tool"] = ch',
                                                "            if False:\n                obj[\"tool\"] = ch")]),
    ("hooks: 통로로 지나간 호출을 기록하지 않음", [("rlo/hooks.py", "dict(row, allowed_while_stale=f\"channel:{ch['tool']}\")", "row")]),
    ("hooks: 훅에서도 건강 상태 TTL 을 둠(선택 B 아님)", [("rlo/hooks.py",
        "substitutes=None, channels=None, health_ttl: bool = False,\n",
        "substitutes=None, channels=None, health_ttl: bool = True,\n")]),
    ("hooks: 기한을 넘으면 지나가게 함(열린 쪽)", [("rlo/hooks.py",
        'return deny(f"rlo hook deadline exceeded ({self.deadline_s:g}s)" + R.line(obj)) if self.mode == ENFORCE else {}',
        "return {}")]),
    ("hooks: 기한을 넘으면 통로도 막음", [("rlo/hooks.py",
        "        if ch is not None:\n            self.record(\"guard_deadline\"", "        if False:\n            self.record(\"guard_deadline\"")]),
    ("hooks: 기한을 기다리지 않고 끝까지 기다림", [("rlo/hooks.py", "        t.join(self.deadline_s)\n", "        t.join()\n")]),
    ("hooks: 명령 훅에 기한이 없음", [("rlo/hooks.py", "                       deadline_s=a.deadline_s or None)",
                                      "                       deadline_s=None)")]),
    # 사건 창(CMD-K14)
    ("window: 대기 중인 tool.start 를 창 밖에서 버림", [("rlo/hooks.py",
        'and (e["data"].get("tool_index") not in ended or e["data"].get("tool_index") in late)]',
        'and e["data"].get("tool_index") in late]')]),
    ("window: 남긴 호출의 llm.response 를 버림", [("rlo/hooks.py",
        'if id(e) in ids or (e["type"] == "llm.response" and e["data"].get("call_index") in calls)]',
        'if id(e) in ids]')]),
    ("window: 창을 기록하지 않음", [("rlo/hooks.py", '                row["window"] = lw[1]', '                pass')]),
    ("window: 기본 창이 한도 없음", [("rlo/hooks.py", "WINDOW = 400   ", "WINDOW = None  ")]),
    ("window: 창을 무시하고 전부 넣음", [("rlo/hooks.py", "        events, dropped = window_events(events, self.window)\n",
                                       "        events, dropped = window_events(events, None)\n")]),
    ("window: 명령 훅이 --window 를 넘기지 않음", [("rlo/hooks.py", "window=a.window or None, incremental=", "incremental=")]),
    # 이어 받기(CMD-K14 S2)
    ("incremental: 캐시가 다른 transcript 를 다시 씀", [("rlo/hooks.py",
        "    return old[:2] == new[:2] and new[2] >= old[2] and new[3][:len(old[3])] == old[3]", "    return True")]),
    ("incremental: inode 를 보지 않음", [("rlo/hooks.py", "    return old[:2] == new[:2] and new[2]", "    return new[2]")]),
    ("incremental: 머리 바이트를 보지 않음", [("rlo/hooks.py", " and new[3][:len(old[3])] == old[3]", "")]),
    ("incremental: 줄어든 파일도 같다고 봄", [("rlo/hooks.py", "new[2] >= old[2]", "True")]),
    ("incremental: 지금 호출이 캐시에 있어도 extend", [("rlo/hooks.py",
        '               else "current_seen" if current & c.ids else None)', "               else None)")]),
    ("incremental: extend 뒤 본 사건 id 를 적지 않음", [("rlo/hooks.py", "            c.ids.update(e[\"id\"] for e in events)\n", "")]),
    ("incremental: 이어 받지 않고 늘 새로 지음", [("rlo/hooks.py", "        if why is None:\n            c.rs.extend(events)",
                                              "        if False:\n            c.rs.extend(events)")]),
    ("incremental: 이력 설정에서도 extend", [("rlo/hooks.py",
        'return hasattr(RunState, "extend") and _history_free(self.sensor_config or DEFAULT_CONFIG)',
        'return hasattr(RunState, "extend")')]),
    ("incremental: 캐시 크기에 한도 없음", [("rlo/hooks.py", "        while len(self._cache) > CACHE_MAX:", "        while False:")]),
    ("incremental: 기본이 꺼짐", [("rlo/hooks.py", "window: \"int | None\" = WINDOW, incremental: bool = True):",
                                "window: \"int | None\" = WINDOW, incremental: bool = False):")]),
    ("incremental: 명령 훅이 --no-incremental 을 무시", [("rlo/hooks.py", "incremental=not a.no_incremental", "incremental=True")]),
    # prompt-spec/1(CMD-K15 rev 4, D2)
    ("pspec: compact 의 뒤 턴에 once 를 다시 보냄", [("rlo/pspec.py", "    sec = turn if resumes else turn_noresume",
                                                  '    sec = turn if resumes and mode == "verbatim" else turn_noresume')]),
    ("pspec: dict 를 정렬하지 않고 돎", [("rlo/pspec.py",
        '                seq = [{"key": k, **x} for k, x in sorted(seq.items())]',
        '                seq = [{"key": k, **x} for k, x in seq.items()]')]),
    ("pspec: v/c 스위치가 뒤집힘", [("rlo/pspec.py", '            out.append(_render(spec, n.body if mode == "verbatim" else n.alt, env, mode))',
                                   '            out.append(_render(spec, n.alt if mode == "verbatim" else n.body, env, mode))')]),
    ("pspec: 검사기가 모르는 칸을 받음", [("rlo/pspec.py", '        if set(v) - names:\n            out.append(path + ".unknown_field")\n',
                                         "")]),
    ("pspec: key(tools) 를 보지 않음", [("rlo/pspec.py",
        "        if not isinstance(v, str) or v not in values.get(t.arg, {}):", "        if not isinstance(v, str):")]),
    ("pspec: id seen 을 보지 않음", [("rlo/pspec.py", "        if v not in seen:\n            out.append(path)\n",
                                    "        pass\n")]),
    ("pspec: [T] <= N 이 하나 어긋남", [("rlo/pspec.py", "(bound is not None and len(v) > bound)",
                                       "(bound is not None and len(v) >= bound)")]),
    ("pspec: 모르는 SEMANTIC_MODEL 낱말을 받음", [("rlo/pspec.py", "            if term not in TERMS:", "            if False:")]),
    ("pspec: 모르는 근거를 받음", [("rlo/pspec.py", "            if basis not in BASES:", "            if False:")]),
    ("pspec: 오류 위치가 다른 줄", [("rlo/pspec.py", "        ln, no = lines[i], i + 1", "        ln, no = lines[i], i")]),
    ("pspec: 템플릿 오류 위치가 다른 줄", [("rlo/pspec.py", '    return start_line + before.count("\\n"), offset',
                                          '    return start_line + before.count("\\n") + 1, offset')]),
    ("pspec: 보고에서 Telemetry 사용량을 버림", [("rlo/pspec.py", '                     "provider": usage_report(u, usage_format)})',
                                               '                     "provider": None})')]),
    ("pspec: 빠진 입력을 받음", [("rlo/pspec.py", "    if missing:\n        raise SpecError(f\"missing inputs", "    if False:\n        raise SpecError(f\"missing inputs")]),
    ("pspec: id new 가 겹침을 받음", [("rlo/pspec.py", "rx.fullmatch(v))) or v in seen:", "rx.fullmatch(v))):")]),
    ("pspec: 파일 끝 줄바꿈이 글에 들어감", [("rlo/pspec.py",
        '    lines = (text[:-1] if text.endswith("\\n") else text).split("\\n")', '    lines = text.split("\\n")')]),
    ("pspec: 자기 id 를 이미 본 것으로 셈(P1)", [("rlo/pspec.py",
        '        for f in sorted(t.arg, key=lambda f: f.type.kind == "id_new"):', "        for f in t.arg:")]),
    ("pspec: fullmatch 대신 match(P2)", [("rlo/pspec.py", "rx.fullmatch(v)", "rx.match(v)")]),
    ("pspec: 모르는 템플릿 이름이 빈 글로 나감(P3)", [("rlo/pspec.py",
        "        if root not in base and root not in scope:", "        if False:")]),
    ("pspec: c 를 v 밖에서도 받음", [("rlo/pspec.py", '                inner = ("c", "end") if word == "v" else ("else", "end")',
                                     '                inner = ("c", "else", "end")')]),
    # 컨텍스트 예산(CMD-K17, D2)
    ("budget: 사이드체인 usage 도 셈", [("rlo/ctxbudget.py",
        'd.get("type") != "assistant" or d.get("isSidechain"):', 'd.get("type") != "assistant":')]),
    ("budget: hard 에서 deny 를 allow 로", [("rlo/ctxbudget.py",
        '        "hookEventName": "PreToolUse", "permissionDecision": "deny",', '        "hookEventName": "PreToolUse", "permissionDecision": "allow",')]),
    ("budget: checkpoint 가 아닌 Bash 를 받음(git push; rm)", [("rlo/ctxbudget.py",
        "        return bool(parts) and all(_PART_OK.match(c) for c in parts)", "        return bool(parts) and any(_PART_OK.match(c) for c in parts)")]),
    ("budget: 셸 특수 기호를 보지 않음", [("rlo/ctxbudget.py", "        if _SHELL.search(cmd):", "        if False:")]),
    ("budget: 모름을 0 으로", [("rlo/ctxbudget.py", '    if ctx is None:\n        return "unknown", {}', "    if ctx is None:\n        ctx = 0")]),
    ("budget: 모름을 예산 넘음으로", [("rlo/ctxbudget.py", '    if ctx is None:\n        return "unknown", {}',
                                     "    if ctx is None:\n        ctx = budget.hard")]),
    ("budget: 설정 없이 기본 예산", [("rlo/hooks.py", "        self.budget = Budget.of(context_budget)",
                                    '        self.budget = Budget.of(context_budget or {"soft": 150000, "hard": 200000, "mode": "enforce"})')]),
    ("budget: 명령 훅이 플래그 없이 기본 예산", [("rlo/hooks.py", "        return None                                # 기본 예산은 없다(BD-289)",
                                              '        return {"soft": 150000, "hard": 200000}')]),
    ("budget: shadow 가 막음", [("rlo/hooks.py", '        if b.mode == "enforce" and denies:', "        if denies:")]),
    ("budget: shadow 가 알림을 냄", [("rlo/hooks.py",
        '        if guard or b.mode != "enforce" or not out:', '        if guard or not out:')]),
    ("budget: 가드의 deny 를 덮음", [("rlo/hooks.py",
        '        if guard or b.mode != "enforce" or not out:', '        if b.mode != "enforce" or not out:')]),
    ("budget: 상태 파일 이름 끝만 봄", [("rlo/ctxbudget.py",
        '        return any(p == s or p.endswith("/" + s.lstrip("/")) for s in state_paths)',
        "        return any(p.endswith(s) for s in state_paths)")]),
    # 플러그인 · 모형 중립(CMD-K18, D2)
    ("plugins: 플러그인 오류가 등록부를 깸", [("rlo/plugins.py",
        "        except Exception as e:                            # 들이기 실패", "        except ArithmeticError as e:                      # 들이기 실패")]),
    ("plugins: 겹친 이름을 받음", [("rlo/plugins.py", "            if name in self.plugins[group]:", "            if False:")]),
    ("plugins: API 판이 달라도 받음", [("rlo/plugins.py", "            if obj.api != API_VERSION:", "            if False:")]),
    ("plugins: 설정을 무시하고 읽개를 고름", [("rlo/ctxbudget.py",
        '    reader = plugins.get("rlo.transcripts", runtime or DEFAULT_RUNTIME)\n    raw = reader.last_usage',
        '    reader = plugins.get("rlo.transcripts", DEFAULT_RUNTIME)\n    raw = reader.last_usage')]),
    ("plugins: 모르는 usage 를 0 으로", [("rlo/usage_formats.py",
        "(sum(parts) if all(p is not None for p in parts) else None)", "sum(p or 0 for p in parts)")]),
    ("plugins: 기본 토크나이저가 바뀜(pspec)", [("rlo/pspec.py",
        '    return math.ceil(len(text.encode("utf-8")) / 4)', "    return math.ceil(len(text) / 4)")]),
    ("plugins: 기본 토크나이저가 바뀜(bytes4)", [("rlo/usage_formats.py",
        '        return math.ceil(len(text.encode("utf-8")) / 4)', "        return math.ceil(len(text) / 4)")]),
    ("plugins: 속이 공급자 SDK 를 들임", [("rlo/plugins.py", "from importlib import metadata\n",
        "from importlib import metadata\ntry:\n    import openai  # noqa: F401\nexcept ImportError:\n    pass\n")]),
    ("plugins: gemini 되감기를 무시", [("rlo/readers.py", '            if isinstance(r.get("$rewindTo"), str):', "            if False:")]),
    ("plugins: codex 의 info 없는 줄을 받음", [("rlo/readers.py",
        ' or not isinstance(p.get("info"), dict):', ":")]),
    ("window: 잘못된 창 크기를 받음", [("rlo/hooks.py", "    if k is not None and (not isinstance(k, int) or isinstance(k, bool) or k <= 0):",
                                     "    if False:")]),
    # 분당 한도 지킴이 · 걸음 차례(CMD-K12)
    ("scheduler: 바쁘게 다시 묻기(자지 않고 돈다)", [("rlo/scheduler.py", "            d = min(wake) - now\n", "            d = 0.0\n")]),
    ("scheduler: 예산이 비어도 모형 걸음을 보냄", [("rlo/scheduler.py", "                if g.ok:\n                    self._dispatch(m, g.ticket)",
                                                "                if True:\n                    self._dispatch(m, g.ticket)")]),
    ("scheduler: 세운 모형 걸음 뒤에 도구 걸음이 막힘", [("rlo/scheduler.py",
        "                if self.kinds[s.name] == \"tool\" and self.state[sid] == PENDING and self._deps(s) == \"ok\" \\\n",
        "                if self.kinds[s.name] == \"tool\" and self.state[sid] == PENDING and self._deps(s) == \"ok\" \\\n"
        "                        and PARKED not in self.state.values() \\\n")]),
    ("governor: retryDelay 를 무시", [("rlo/governor.py", "        if t.retry_after_ms is not None:", "        if False:")]),
    ("scheduler: 걸음 표에 없는 걸음을 받음", [("rlo/scheduler.py", "        if k is None:\n            raise StepKindError(",
                                            "        if k is None:\n            return \"tool\"\n            raise StepKindError(")]),
    ("governor: rpm 을 세지 않음", [("rlo/governor.py", "        if b.rpm is not None and len(q) + calls > b.rpm:", "        if False:")]),
    ("governor: tpm 을 세지 않음", [("rlo/governor.py", "        if b.tpm is not None and q:", "        if False:")]),
    ("governor: 실제 사용량을 적지 않음", [("rlo/governor.py", "                x[1] = tok\n", "                pass\n")]),
    ("governor: 하루 할당도 1 분 뒤 다시", [("rlo/governor.py", '{"day": math.inf, "minute": WINDOW_S}', '{"day": WINDOW_S, "minute": WINDOW_S}')]),
    ("scheduler: 세운 걸음 VERIFY 없음", [("rlo/scheduler.py", "        self.verify.park(s.id, self.clock(), wait_s)\n", "")]),
    ("scheduler: 뒤 모형 걸음이 앞지름", [("rlo/scheduler.py", '                return s if self._deps(s) == "ok" else None',
                                         '                if self._deps(s) == "ok":\n                    return s')]),
    ("autonomy: 한도로 끝난 실행을 미루지 않음", [("rlo/autonomy.py",
        '        if self._gp.deferred is not None and res.outcome == "llm_error":', '        if False:')]),
    ("autonomy: 새 요청이 세운 걸음을 앞지름", [("rlo/autonomy.py", "        if self.parked:\n            return self._park(job,",
                                              "        if False:\n            return self._park(job,")]),
    ("autonomy: 실행 안 둘째 부름도 미룸(끝나지 않음)", [("rlo/autonomy.py",
        "            if self.calls_in_run == 0 or not (0 < wait_s <= max_inline_wait_s):", "            if True:")]),
    ("autonomy: close_windows 가 다시 보내지 않음", [("rlo/autonomy.py",
        "        return self.runtime.close_windows() + self.tick()", "        return self.runtime.close_windows()")]),
    ("scheduler: 저장한 지킴이 창을 되살리지 않음", [("rlo/scheduler.py", '        self.gov.load(d["governor"])\n', "")]),
    ("scheduler: 상태를 저장하지 않음", [("rlo/scheduler.py", "        os.replace(tmp, path)", "        os.remove(tmp)")]),
    ("scheduler: 끝난 걸음 결과를 되살리지 않음", [("rlo/scheduler.py", '                self.results[sid] = d["results"][sid]', "                pass")]),
    ("scheduler: wait=False 인데 잔다", [("rlo/scheduler.py", "            if not wait:\n", "            if False:\n")]),
    ("scheduler: status 의 running 이 모형 걸음을 모름", [("rlo/scheduler.py",
        "        payload = s.payload(self.results) if callable(s.payload) else s.payload\n        self.running = s.id\n",
        "        payload = s.payload(self.results) if callable(s.payload) else s.payload\n")]),
    ("scheduler: 도구 풀을 1 로(함께 돌지 않음)", [("rlo/scheduler.py", "                    elif self._busy() < self.max_parallel:",
                                                  "                    elif self._busy() < 1:")]),
    ("scheduler: 도구 풀 크기를 지키지 않음", [("rlo/scheduler.py", "                    elif self._busy() < self.max_parallel:",
                                              "                    elif self._busy() < 32:"),
                                             ("rlo/scheduler.py", "max_workers=self.max_parallel", "max_workers=32")]),
    ("scheduler: 도구 하나가 실패하면 나머지를 버림", [("rlo/scheduler.py",
        "                self._end_tool(fut, s, cm, h, error=type(e).__name__)\n",
        "                self._end_tool(fut, s, cm, h, error=type(e).__name__)\n                self._inflight.clear()\n")]),
    ("scheduler: 도구 시간 한도를 무시", [("rlo/scheduler.py", "            if limit is not None and now - t0 >= limit:",
                                         "            if False:")]),
    ("scheduler: 다시 띄우면 돌던 걸음을 다시 돌리지 않음", [("rlo/scheduler.py",
        ' or st["state"] == RUNNING:', ":")]),
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


TIMEOUT_S = 600     # 변이 하나의 시험이 이보다 오래 걸리면 RED(멈추지 않는 고리 -- 예: 바쁘게 다시 묻기)로 센다


def run(tree: pathlib.Path) -> int:
    try:
        return subprocess.run([sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests", "-t", "."], cwd=tree,
                              capture_output=True, text=True, timeout=TIMEOUT_S).returncode
    except subprocess.TimeoutExpired:
        return 124


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
    only = sys.argv[1:]                                  # 이름에 이 글자가 든 변이만(없으면 모두)
    chosen = [(n, e) for n, e in MUTANTS if not only or any(o in n for o in only)]
    red = 0
    for name, edits in chosen:
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
        print(f"{'RED  ' if rc else 'GREEN'} {name}" + ("  (시간 초과)" if rc == 124 else ""), flush=True)
    print(f"{red}/{len(chosen)} RED")
    return 0 if red == len(chosen) else 1


if __name__ == "__main__":
    sys.exit(main())
