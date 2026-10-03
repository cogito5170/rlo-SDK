"""턴 안 ReAct(CMD-K11 · GA_UNIFIED §4) -- 거부마다 **닫힌 대안표**에서 대안 하나를 고른다. 판정은 deny 그대로다.

    (규칙, 원인 라벨) -> 대안 종류          TABLE. 표에 없는 짝은 none. 자유 글 · LLM 없음
    use_tool          모형 안 · (external · irreversible 이면) 허가된 대체 도구로        A1, 운영자의 substitutes 에 있을 때
    refresh_read      읽기 호출 하나 뒤 다시                                           D, 낡음만(K10 STALE_HINT 와 같은 경우)
    wait_previous     앞 호출의 결과를 기다린 뒤 다시                                    D, 모름(나란히 · 결과 없는 앞 호출)
    drop_unknown_args 모형에 없는 인자를 빼고 다시                                       A4, 모르는 인자뿐
    report            다시 하지 않는다. 통로에 올린다(escalate)                           A7 · 대체 없는 A1 · 입력 · 설정 · 판정 오류
    none              대안 없음                                                       그 밖(A4 인자 없음 · 꼴, A5 · A6 · A8 …)

되풀이 상한: transcript 에서 **같은 (도구, 규칙, 원인)** 의 거부(그 도구의 tool_result 에 실린 `-- react:` 줄)를 센다.
그 도구가 거부 아닌 결과를 받으면 다시 0 부터. 세 번째 같은 거부는 report · escalate true 가 된다(attempt 3 of 2).

대안은 허가를 넓히지 않는다: grants 는 읽기만 하고, use_tool 은 모형에 있고 허가가 필요하면 허가된 도구만 이름 짓는다.
대체표(substitutes)는 운영자가 모형 파일에 둔다 -- action-model/1 의 칸이 아니라 rlo 가 그 옆에서 읽고 떼어 낸다
(`split_model`). action-model/1 계약(BD-109)은 그대로다.

통로 도구(channels, CMD-K13): 운영자가 모형 파일에 둔다(substitutes 와 같은 자리). 인자가 **고정된** 통로 호출만 --
    {"tool": "mcp__github__add_issue_comment", "args": {"owner": "o", "repo": "r", "issue_number": 1}}       인자 값이 같아야
    {"tool": "Bash", "argv_prefix": ["ga", "mail"]}             명령이 그 낱말들로 시작하고 셸 특수 문자가 없어야
    "use": "report"(기본) | "read"                               올림에 쓰는 통로인가 · 읽기만 하는 통로인가
D 의 까닭이 낡음뿐이면 고정된 통로 호출은 지나간다(hooks, allowed_while_stale). 올리는(report) 대안은 지금 부를 수 있는
통로 도구를 이름 짓는다 -- 모형에 있고, 허가가 필요하면 허가됐고, D 가 막지 않는 것만(막힌 도구를 가리키지 않는다).
"""
from __future__ import annotations

import json
import re
import shlex

MAX_ATTEMPTS = 2
PREFIX = "-- react: "
KINDS = ("use_tool", "refresh_read", "wait_previous", "drop_unknown_args", "report", "none")
LABEL = re.compile(r"^[A-Za-z0-9_.:+-]{1,40}$")      # 층 사이로 흐르는 라벨(GA_UNIFIED U-P4)

# 닫힌 대안표 -- (규칙, 원인) -> 종류. 여기에 없는 짝은 none 이다
TABLE = {
    ("A1", "has_substitute"): "use_tool",
    ("A1", "no_substitute"): "report",
    ("A4", "unknown_args"): "drop_unknown_args",
    ("A7", "not_granted"): "report",
    ("D", "stale"): "refresh_read",
    ("D", "unavailable"): "wait_previous",
    ("E", "guard_error"): "report",
    ("input", "malformed_input"): "report",
    ("config", "config_error"): "report",
    ("hook", "hook_error"): "report",
    ("hook", "deadline"): "report",                  # 판정이 기한을 넘었다(CMD-K13 S7) -- 다시 해도 같다, 통로로 올린다
}


class SubstitutesError(ValueError):
    pass


EXTENSIONS = ("substitutes", "channels")              # rlo 가 모형 파일에서 읽고 떼어 내는 칸(action-model/1 밖)
SHELL_META = re.compile(r"[;&|<>$`\\(){}\n\r*?!~]")     # 고정된 Bash 통로에 있으면 안 되는 글자(이어 붙이기 · 대체 · 글롭)


def channels_of(d: dict) -> list:
    """모형 JSON 의 channels -> [{tool, args | argv_prefix, use}]. 고정이 없거나 꼴이 틀리면 SubstitutesError."""
    chans = d.get("channels", []) if isinstance(d, dict) else []
    if not isinstance(chans, list):
        raise SubstitutesError("channels: 목록이어야 한다")
    out = []
    for c in chans:
        if not isinstance(c, dict) or set(c) - {"tool", "args", "argv_prefix", "use"} or \
                not (isinstance(c.get("tool"), str) and LABEL.match(c["tool"])):
            raise SubstitutesError(f"channels: {{tool, args | argv_prefix, use}} 이어야 한다 ({c!r})")
        use = c.get("use", "report")
        if use not in ("report", "read"):
            raise SubstitutesError(f"channels: use 는 report · read ({use!r})")
        args, prefix = c.get("args"), c.get("argv_prefix")
        if (args is None) == (prefix is None):
            raise SubstitutesError(f"channels {c['tool']}: args 나 argv_prefix 가운데 하나로 고정해야 한다")
        if args is not None and not (isinstance(args, dict) and args and all(
                isinstance(k, str) and isinstance(v, (str, int, bool)) for k, v in args.items())):
            raise SubstitutesError(f"channels {c['tool']}: args 는 비지 않은 {{이름: 스칼라}}")
        if prefix is not None and not (isinstance(prefix, list) and prefix and all(
                isinstance(x, str) and x and not SHELL_META.search(x) and " " not in x for x in prefix)):
            raise SubstitutesError(f"channels {c['tool']}: argv_prefix 는 비지 않은 낱말 목록")
        out.append({"tool": c["tool"], "args": dict(args) if args else None,
                    "argv_prefix": list(prefix) if prefix else None, "use": use})
    return out


def channel_of(tool: str, tool_input, channels) -> "dict | None":
    """이 호출이 고정된 통로 호출인가. 맞는 통로(dict) 또는 None."""
    if not isinstance(tool_input, dict):
        return None
    for c in channels or ():
        if c["tool"] != tool:
            continue
        if c["args"] is not None and all(k in tool_input and tool_input[k] == v and type(tool_input[k]) is type(v)
                                         for k, v in c["args"].items()):
            return c
        if c["argv_prefix"] is not None:
            cmd = tool_input.get("command")
            if not isinstance(cmd, str) or SHELL_META.search(cmd):
                continue
            try:
                argv = shlex.split(cmd)
            except ValueError:
                continue
            if argv[:len(c["argv_prefix"])] == c["argv_prefix"]:
                return c
    return None


def report_channel(gmodel, channels, dcv=None, stale_only_d: bool = False) -> "str | None":
    """올릴 때 이름 지을 통로 도구: use=report, 모형에 있고, 허가가 필요하면 허가됐고, D 가 막지 않는 것(차례대로 첫째)."""
    from guard.rules import GRANT_RISKS
    if gmodel is None:
        return None
    for c in channels or ():
        spec = gmodel.specs.get(c["tool"])
        if c["use"] != "report" or spec is None:
            continue
        if spec.risk in GRANT_RISKS and c["tool"] not in gmodel.grants:
            continue
        if spec.risk in gmodel.risky and dcv is not None and not dcv.complete and not stale_only_d:
            continue                                     # 상태를 모르면(UNKNOWN) D 가 이 통로도 막는다
        return c["tool"]
    return None


def split_model(d: dict) -> "tuple[dict, dict]":
    """모형 JSON -> (action-model/1 dict, substitutes {도구: [도구, …]}). rlo 의 칸(substitutes · channels)은 떼어 낸다.
    substitutes 가 없으면 {}. 꼴이 틀리면 SubstitutesError(설정 오류 -- 명령 훅은 enforce 에서 닫는다)."""
    if not isinstance(d, dict) or not set(EXTENSIONS) & set(d):
        return d, {}
    channels_of(d)                                       # 꼴 검사
    rest = {k: v for k, v in d.items() if k not in EXTENSIONS}
    if "substitutes" not in d:
        return rest, {}
    subs = d["substitutes"]
    if not isinstance(subs, dict) or not all(
            isinstance(k, str) and LABEL.match(k) and isinstance(v, list) and all(isinstance(x, str) and LABEL.match(x) for x in v)
            for k, v in subs.items()):
        raise SubstitutesError("substitutes: {도구 이름: [도구 이름, …]} 이어야 한다(이름은 라벨)")
    return rest, {k: list(v) for k, v in subs.items()}


def load_model(path: str):
    """(ActionModel, substitutes). 모형 파일의 rlo 칸(substitutes · channels)을 떼어 내고 나머지를 action-model/1 로 읽는다."""
    model, subs, _ = load_hook_model(path)
    return model, subs


def load_hook_model(path: str):
    """(ActionModel, substitutes, channels)."""
    from action.spec import ActionModel
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    d, subs = split_model(raw)
    return ActionModel.from_dict(d), subs, channels_of(raw)


def eligible(tool: str, gmodel, substitutes: dict) -> list:
    """그 도구의 대체 가운데 대안으로 이름 지어도 되는 것(순서 그대로): 모형에 있고, 허가가 필요하면 허가됐고, 자기 자신이 아님."""
    from guard.rules import GRANT_RISKS                  # A7 이 허가를 보는 위험 등급 그대로
    out = []
    for s in (substitutes or {}).get(tool, ()):
        spec = gmodel.specs.get(s) if gmodel is not None else None
        if s == tool or spec is None:
            continue
        if spec.risk in GRANT_RISKS and s not in gmodel.grants:
            continue
        out.append(s)
    return out


def _failing(res) -> set:
    rules = {r[1:].split("]", 1)[0] for r in res.reasons if r.startswith("[")}
    return rules or {res.rule}


def classify(it, res, dcv, gmodel=None, substitutes=None) -> "tuple[str, str, str | None]":
    """거부 -> (규칙, 원인 라벨, 대체 도구 | None). 원인은 구조로 정한다(남의 까닭 글을 읽지 않는다)."""
    from .hooks import stale_only
    failing = _failing(res)
    if "A7" in failing:                                    # 허가는 대안으로 풀 수 없다 -- 다른 것이 함께 걸려도 먼저
        return "A7", "not_granted", None
    if res.rule == "A1":
        subs = eligible(it.action, gmodel, substitutes)
        return ("A1", "has_substitute", subs[0]) if subs else ("A1", "no_substitute", None)
    if res.rule == "A4":
        spec = gmodel.specs.get(it.action) if gmodel is not None else None
        if spec is not None:
            from action.params import check_args
            known = {k: v for k, v in it.args.items() if k in spec.params}
            if set(it.args) - set(spec.params) and not check_args(spec.params, known):
                return "A4", "unknown_args", None
        return "A4", "bad_args", None
    if failing == {"D"}:
        return "D", ("stale" if stale_only(res, dcv) else "unavailable"), None
    if "E" in failing:
        return "E", "guard_error", None
    return res.rule, "other", None


def _texts(content):
    if isinstance(content, str):
        yield content
    elif isinstance(content, list):
        for b in content:
            if isinstance(b, dict) and isinstance(b.get("text"), str):
                yield b["text"]


def react_of(text: str) -> "dict | None":
    """글에서 `-- react: {json}` 줄을 찾아 읽는다(없거나 틀리면 None)."""
    for line in text.splitlines():
        line = line.strip()
        if line.startswith(PREFIX):
            try:
                obj = json.loads(line[len(PREFIX):])
            except ValueError:
                return None
            return obj if isinstance(obj, dict) else None
    return None


def prior_denies(transcript_path: str, tool: str, rule: str, cause: str) -> int:
    """transcript 에서 그 도구의 같은 (규칙, 원인) 거부가 몇 번 이어졌나. 그 도구가 다른 결과를 받으면 0 부터."""
    names, n = {}, 0
    with open(transcript_path, encoding="utf-8") as f:
        for line in f:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            msg = e.get("message") if isinstance(e, dict) else None
            content = msg.get("content") if isinstance(msg, dict) else None
            if not isinstance(content, list):
                continue
            for b in content:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "tool_use":
                    names[b.get("id")] = b.get("name")
                elif b.get("type") == "tool_result" and names.get(b.get("tool_use_id")) == tool:
                    r = next((x for x in map(react_of, _texts(b.get("content"))) if x), None)
                    n = n + 1 if r and (r.get("rule"), r.get("cause")) == (rule, cause) else 0
    return n


def react(rule: str, cause: str, tool: "str | None" = None, prior: int = 0) -> dict:
    """대안 객체. 표에서 종류를 찾고, 같은 거부가 상한을 넘었으면 report · escalate."""
    kind = TABLE.get((rule, cause), "none")
    attempt = prior + 1
    if attempt > MAX_ATTEMPTS:
        kind = "report"
    obj = {"kind": kind, "rule": rule, "cause": cause, "attempt": attempt, "of": MAX_ATTEMPTS,
           "escalate": kind == "report"}
    if kind == "use_tool":
        obj["tool"] = tool
    return obj


def line(obj: dict) -> str:
    """거부 까닭 끝에 붙는 한 줄(앞에 줄바꿈)."""
    return "\n" + PREFIX + json.dumps(obj, sort_keys=True, separators=(",", ":"))
