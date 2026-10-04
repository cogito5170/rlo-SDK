"""prompt-spec/1 -- 우리 프롬프트 언어(baseline PROMPT_SPEC.md b9e7669 §1-§3, CMD-K15 rev 4, BD-291). 원형 ops/pspec/pspec.py 의 제품판.

    spec = load_file("gemini-plan.pspec")                               # 꼴이 틀리면 SpecError(줄 · 열)
    text = compile(spec, "first", {"tools": t, "task": s, "results": [], "ask": ""}, "compact")
    problems = check(spec, answer_value, {"tools": t})                  # 같은 out 선언에서 -- 비면 받는다
    tokens(text)                                                        # ceil(utf-8 바이트 / 4) -- 오프라인 추정

파일 하나에서 셋이 나온다: 프롬프트 글(verbatim = 지금 글과 바이트 같음 · compact = 토큰을 줄인 글), 출력 검사기, 토큰 보고.
문법과 §4 의 결과는 baseline 의 것이다 -- 바꿀 것은 Proposal 로 올린다. 모형의 답은 Opinion 이다(권위 없음): 판정은 check 와
그 뒤의 결정론 규칙이 한다. 이 모듈은 모형을 부르지 않고, 공급자 SDK · 네트워크 · 키를 쓰지 않는다.

**파일 꼴(§1)**

    spec <name>/<n>
    goal <한 줄>
    in <name> : <SEMANTIC_MODEL 낱말> @<근거> once|turn
    let <name> = <정수> | /<정규식>/
    out <스키마>          그 아래 두 칸 들여쓴 줄마다  <칸>[?] : <타입>
    --- <구역>            템플릿 구역(다음 --- 까지). 파일 끝 줄바꿈은 프롬프트에 들어가지 않는다

**템플릿(§2)**: `{{ a.b|json|cap:N|rstrip:" "|sort }}` · `{% for x in xs %}…{% else %}…{% end %}`(dict 는 키 순서로 정렬해
돈다, 원소는 {"key": 키, **값}) · `{% if a %}…{% else %}…{% end %}` · `{% v %}verbatim{% c %}compact{% end %}` ·
`{{ out }}`(compact 한 줄 출력 꼴) · `{{ out_schema }}` · `{% use <구역> %}`. 중괄호 하나는 그냥 글자다.

**출력 타입(§3)**: `"lit"` · `str` · `str+` · `int` · `{}` · `key(<in>)` · `id new` · `id seen` · `[T] <= N` ·
`{a: T, b?: T}`(모르는 칸 거부) · `T | null`.

**타입을 더하는 규칙**: 타입은 문법이다 -- baseline 이 PROMPT_SPEC §3 을 고친 뒤에만 더한다(Proposal). rlo 에서는 `KINDS` 의 한
줄과 세 곳(파서 `_P.atom` · 서명 `_sig` · 검사 `_check`), 그리고 시험(읽기 · 서명 · 받음 · 거부)과 변이 하나다. 이미 있는
명세의 verbatim · compact 글은 바이트 그대로여야 한다(fixture 시험). 문법이 하위 호환이 아니면 언어 판본을 올린다(`LANGUAGE`).

**판본**: 언어는 `LANGUAGE = "prompt-spec/1"`. 명세마다 `spec <name>/<n>` 의 n 이 그 명세의 판(`Spec.version`),
`Spec.digest` 는 파일 글의 sha256 -- 고정 · 기록용.

**원형과 다른 점(문법 · 결과는 같다)**: 오류에 줄 · 열이 붙는다. 구역 템플릿을 적재할 때 한 번 파싱해 둔다(모르는 태그 ·
거르개 · 짝 없는 end · 없는 구역을 쓰는 use · use 순환은 적재 오류). `c` 는 `v` 안에서만, `else` 는 for · if 안에서만 받는다.
같은 이름의 in · let · 구역 · 칸이 두 번이면 오류. `[T] <= 이름` 의 이름은 정수 let 이어야, `key(이름)` 의 이름은 in 이어야 한다.
compile 은 선언되지 않은 입력도 오류로 본다.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from typing import Any

LANGUAGE = "prompt-spec/1"
TERMS = ("Observation", "Measurement", "State", "Evidence", "Model", "DecisionContext", "Opinion")
BASES = ("observed", "runtime", "provider", "operator", "model", "label", "estimate")
WHENS = ("once", "turn")
MODES = ("verbatim", "compact")
KINDS = ("lit", "str", "str+", "int", "any", "key", "id_new", "id_seen", "list", "obj", "nullable")
FILTERS = ("json", "cap", "rstrip", "sort")
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_SPEC_NAME = re.compile(r"[A-Za-z0-9_.-]+/(\d+)")


class SpecError(ValueError):
    """적재 · 템플릿 · 컴파일 오류. line · col 은 1 부터(모르면 None)."""

    def __init__(self, msg: str, line: "int | None" = None, col: "int | None" = None, source: "str | None" = None):
        self.msg, self.line, self.col, self.source = msg, line, col, source
        where = ":".join(str(x) for x in (source, line, col) if x is not None)
        super().__init__(f"{where}: {msg}" if where else msg)


# ── 타입 ──────────────────────────────────────────────────────────────────────────────────────────────────────

@dataclass
class T:
    kind: str                      # KINDS
    arg: Any = None                # lit 값 | key 입력 | list 원소 | obj 칸들 | nullable 안
    max: "str | int | None" = None  # list 한도(정수 또는 let 이름)


@dataclass
class Field:
    name: str
    type: T
    optional: bool = False


class _P:
    """타입 식 하나의 재귀 하강 파서. 오류는 base_col(그 식이 줄에서 시작하는 열) 기준의 열로 낸다."""

    def __init__(self, s: str, line: int, base_col: int, source):
        self.s, self.i, self.line, self.base, self.source = s, 0, line, base_col, source

    def err(self, msg):
        raise SpecError(msg, self.line, self.base + self.i, self.source)

    def ws(self):
        while self.i < len(self.s) and self.s[self.i] == " ":
            self.i += 1

    def eat(self, tok: str) -> bool:
        self.ws()
        if self.s.startswith(tok, self.i):
            self.i += len(tok)
            return True
        return False

    def need(self, tok: str):
        if not self.eat(tok):
            self.err(f"type: expected {tok!r}")

    def word(self) -> str:
        self.ws()
        m = re.compile(r"[A-Za-z_][A-Za-z0-9_+]*").match(self.s, self.i)
        if not m:
            self.err("type: expected a name")
        self.i = m.end()
        return m.group()

    def full(self) -> T:
        t = self.type()
        self.ws()
        if self.i != len(self.s):
            self.err("type: unexpected text")
        return t

    def type(self) -> T:
        t = self.atom()
        if self.eat("|"):
            self.need("null")
            return T("nullable", t)
        return t

    def atom(self) -> T:
        self.ws()
        if self.eat('"'):
            j = self.s.find('"', self.i)
            if j < 0:
                self.err('type: unterminated "literal"')
            v, self.i = self.s[self.i:j], j + 1
            return T("lit", v)
        if self.eat("["):
            item = self.type()
            self.need("]")
            bound = None
            if self.eat("<="):
                self.ws()
                m = re.compile(r"\d+|[A-Za-z_]\w*").match(self.s, self.i)
                if not m:
                    self.err("type: expected a bound after <=")
                self.i = m.end()
                bound = int(m.group()) if m.group().isdigit() else m.group()
            return T("list", item, bound)
        if self.eat("{"):
            if self.eat("}"):
                return T("any")
            fields: list = []
            while True:
                at = self.i
                name = self.word()
                if any(f.name == name for f in fields):
                    self.i = at
                    self.err(f"type: field {name!r} twice")
                opt = self.eat("?")
                self.need(":")
                fields.append(Field(name, self.type(), opt))
                if self.eat("}"):
                    return T("obj", fields)
                self.need(",")
        at = self.i
        w = self.word()
        if w in ("str", "str+", "int"):
            return T(w)
        if w == "key":
            self.need("(")
            inp = self.word()
            self.need(")")
            return T("key", inp)
        if w == "id":
            mode = self.word()
            if mode not in ("new", "seen"):
                self.err("type: id must be `id new` or `id seen`")
            return T("id_" + mode)
        self.i = at
        self.err(f"type: unknown {w!r}")


# ── 명세 ──────────────────────────────────────────────────────────────────────────────────────────────────────

@dataclass
class Input:
    name: str
    term: str
    basis: str
    when: str


@dataclass
class Spec:
    name: str
    version: int
    goal: str = ""
    inputs: "dict[str, Input]" = field(default_factory=dict)
    lets: "dict[str, Any]" = field(default_factory=dict)
    out_schema: str = ""
    out: "list[Field]" = field(default_factory=list)
    sections: "dict[str, str]" = field(default_factory=dict)
    source: "str | None" = None
    digest: str = ""
    language: str = LANGUAGE
    _trees: dict = field(default_factory=dict, repr=False)       # 구역 -> 파싱한 템플릿(적재 때 한 번)
    _starts: dict = field(default_factory=dict, repr=False)      # 구역 -> 본문 첫 줄 번호


def load_file(path) -> Spec:
    with open(path, encoding="utf-8") as f:
        return load(f.read(), source=str(path))


def load(text: str, source: "str | None" = None) -> Spec:
    """*.pspec 글 -> Spec. 꼴 · 템플릿 오류는 SpecError(줄 · 열)."""
    def err(msg, line, col=1):
        raise SpecError(msg, line, col, source)

    spec: "Spec | None" = None
    lines = (text[:-1] if text.endswith("\n") else text).split("\n")   # 파일 끝 줄바꿈은 프롬프트 글이 아니다
    i = 0
    while i < len(lines):
        ln, no = lines[i], i + 1
        if ln.startswith("--- "):
            if spec is None:
                err("the file must start with `spec`", no)
            name = ln[4:].strip()
            if not _NAME.fullmatch(name):
                err(f"section: bad name {name!r}", no, 5)
            if name in spec.sections:
                err(f"section {name!r} twice", no, 5)
            body = []
            i += 1
            spec._starts[name] = i + 1
            while i < len(lines) and not lines[i].startswith("--- "):
                body.append(lines[i])
                i += 1
            spec.sections[name] = "\n".join(body)
            continue
        if not ln.strip() or ln.startswith("#"):
            i += 1
            continue
        kw, _, rest = ln.partition(" ")
        col = len(kw) + 2 + (len(rest) - len(rest.lstrip()))          # rest 의 첫 글자 열
        if kw == "spec":
            if spec is not None:
                err("`spec` twice", no)
            m = _SPEC_NAME.fullmatch(rest.strip())
            if not m:
                err(f"spec: expected <name>/<n>, got {rest.strip()!r}", no, col)
            spec = Spec(rest.strip(), int(m.group(1)), source=source,
                        digest=hashlib.sha256(text.encode("utf-8")).hexdigest())
        elif spec is None:
            err("the file must start with `spec`", no)
        elif kw == "goal":
            spec.goal = rest.strip()
        elif kw == "in":
            m = re.fullmatch(r"(\w+)\s*:\s*(\w+)\s+@(\w+)\s+(once|turn)", rest.strip())
            if not m:
                err("in: expected `in <name> : <term> @<basis> once|turn`", no, col)
            name, term, basis, when = m.groups()
            if name in spec.inputs:
                err(f"in {name}: declared twice", no, col)
            if term not in TERMS:
                err(f"in {name}: {term!r} is not a SEMANTIC_MODEL term", no, col + m.start(2))
            if basis not in BASES:
                err(f"in {name}: unknown basis {basis!r}", no, col + m.start(3))
            spec.inputs[name] = Input(name, term, basis, when)
        elif kw == "let":
            name, eq, v = rest.partition("=")
            name, v = name.strip(), v.strip()
            if not eq or not _NAME.fullmatch(name):
                err("let: expected `let <name> = <int> | /<regex>/`", no, col)
            if name in spec.lets:
                err(f"let {name}: declared twice", no, col)
            vcol = col + rest.index("=") + 1 + (len(rest.split("=", 1)[1]) - len(rest.split("=", 1)[1].lstrip()))
            if v.startswith("/"):
                if len(v) < 2 or not v.endswith("/"):
                    err(f"let {name}: a regex is /.../", no, vcol)
                try:
                    spec.lets[name] = re.compile(v[1:-1])
                except re.error as e:
                    err(f"let {name}: bad regex ({e.msg})", no, vcol)
            elif re.fullmatch(r"-?\d+", v):
                spec.lets[name] = int(v)
            else:
                err(f"let {name}: expected an int or /regex/", no, vcol)
        elif kw == "out":
            if spec.out_schema:
                err("`out` twice", no)
            spec.out_schema = rest.strip()
            if not spec.out_schema:
                err("out: expected a schema name", no, col)
            i += 1
            while i < len(lines) and lines[i].startswith("  "):
                fl, fno = lines[i], i + 1
                m = re.fullmatch(r"(\s+)(\w+)(\?)?\s*:\s*(.+)", fl)
                if not m:
                    err("out: expected `  <field>[?] : <type>`", fno, 3)
                if any(f.name == m.group(2) for f in spec.out):
                    err(f"out: field {m.group(2)!r} twice", fno, m.start(2) + 1)
                tx = m.group(4).rstrip()
                spec.out.append(Field(m.group(2), _P(tx, fno, m.start(4) + 1, source).full(), bool(m.group(3))))
                i += 1
            if not spec.out:
                err("out: no fields", no)
            continue
        else:
            err(f"unknown line keyword {kw!r}", no)
        i += 1
    if spec is None:
        raise SpecError("empty spec", None, None, source)
    _resolve(spec)
    for name in spec.sections:
        spec._trees[name] = _parse_section(spec, name)
    for name in spec.sections:
        _uses(spec, name, ())
    return spec


def _resolve(spec: Spec):
    """타입이 가리키는 이름: [T] <= 이름 은 정수 let, key(이름) 은 in."""
    def walk(t: T, where):
        if t.kind == "list":
            if isinstance(t.max, str) and not isinstance(spec.lets.get(t.max), int):
                raise SpecError(f"out {where}: bound {t.max!r} is not an int let", None, None, spec.source)
            walk(t.arg, where)
        elif t.kind == "key" and t.arg not in spec.inputs:
            raise SpecError(f"out {where}: key({t.arg}) names no input", None, None, spec.source)
        elif t.kind == "nullable":
            walk(t.arg, where)
        elif t.kind == "obj":
            for f in t.arg:
                walk(f.type, f"{where}.{f.name}")
    for f in spec.out:
        walk(f.type, f.name)


# ── 출력 꼴: compact 서명과 검사기 ──────────────────────────────────────────────────────────────────────────────

def _sig(t: T, lets: dict) -> str:
    k = t.kind
    if k == "lit":
        return json.dumps(t.arg)
    if k in ("str", "str+", "int"):
        return k
    if k == "any":
        return "{}"
    if k == "key":
        return t.arg
    if k in ("id_new", "id_seen"):
        return "id"
    if k == "nullable":
        return _sig(t.arg, lets) + "|null"
    if k == "list":
        b = t.max if isinstance(t.max, int) else lets.get(t.max, t.max)
        return "[" + _sig(t.arg, lets) + "]" + (f"≤{b}" if b is not None else "")
    if k == "obj":
        return "{" + ",".join(f'"{f.name}"{"?" if f.optional else ""}:{_sig(f.type, lets)}' for f in t.arg) + "}"
    raise SpecError(f"unknown type kind {k!r}")


def out_form(spec: Spec) -> str:
    """compact 한 줄 출력 꼴 -- 검사기와 같은 선언에서 나온다."""
    return _sig(T("obj", spec.out), spec.lets)


def check(spec: Spec, value: Any, values: "dict | None" = None) -> "list[str]":
    """답 값의 문제(경로 목록). 비면 받는다. values 는 key(<in>) 이 볼 입력."""
    seen: list = []
    out: list = []
    _check(spec, T("obj", spec.out), value, "$", values or {}, seen, out)
    return out


def _check(spec, t: T, v, path, values, seen, out):
    k = t.kind
    if k == "nullable":
        if v is not None:
            _check(spec, t.arg, v, path, values, seen, out)
    elif k == "lit":
        if v != t.arg:
            out.append(path)
    elif k == "str":
        if not isinstance(v, str):
            out.append(path)
    elif k == "str+":
        if not (isinstance(v, str) and v.strip()):
            out.append(path)
    elif k == "int":
        if not (isinstance(v, int) and not isinstance(v, bool)):
            out.append(path)
    elif k == "any":
        if not isinstance(v, dict):
            out.append(path)
    elif k == "key":
        if not isinstance(v, str) or v not in values.get(t.arg, {}):
            out.append(path)
    elif k == "id_new":
        rx = spec.lets.get("id")
        if not (isinstance(v, str) and (rx is None or rx.match(v))) or v in seen:
            out.append(path)
        seen.append(v)
    elif k == "id_seen":
        if v not in seen:
            out.append(path)
    elif k == "list":
        bound = t.max if isinstance(t.max, int) else spec.lets.get(t.max)
        if not isinstance(v, list) or (bound is not None and len(v) > bound):
            out.append(path)
            return
        for i, item in enumerate(v):
            _check(spec, t.arg, item, f"{path}[{i}]", values, seen, out)
    elif k == "obj":
        if not isinstance(v, dict):
            out.append(path)
            return
        names = {f.name for f in t.arg}
        if set(v) - names:
            out.append(path + ".unknown_field")
        for f in t.arg:
            if f.name not in v:
                if not f.optional:
                    out.append(f"{path}.{f.name}")
                continue
            _check(spec, f.type, v[f.name], f"{path}.{f.name}", values, seen, out)
    else:
        raise SpecError(f"unknown type kind {k!r}")


def parse_answer(text: str) -> Any:
    """모형의 답 글 -> 값: 마지막 ```json 울타리 안, 없으면 글 전체. JSON 이 아니면 ValueError."""
    blocks = re.findall(r"```(?:json)?\s*\n(.*?)```", text, re.S)
    return json.loads(blocks[-1] if blocks else text.strip())


def check_text(spec: Spec, text: str, values: "dict | None" = None) -> "list[str]":
    """답 글을 읽고 검사한다. JSON 이 아니면 ["$.not_json"]."""
    try:
        v = parse_answer(text)
    except ValueError:
        return ["$.not_json"]
    return check(spec, v, values)


# ── 템플릿 ──────────────────────────────────────────────────────────────────────────────────────────────────

_TOK = re.compile(r"(\{\{.*?\}\}|\{%.*?%\})", re.S)
_FOR = re.compile(r"for (\w+) in ([\w.]+)((?:\|\w+)*)")


@dataclass
class _Node:
    word: str                      # out(값) · use · for · if · v
    tag: str
    body: list
    alt: list
    line: int
    col: int
    expr: str = ""
    filters: tuple = ()


def _pos(body: str, offset: int, start_line: int) -> "tuple[int, int]":
    before = body[:offset]
    return start_line + before.count("\n"), offset - (before.rfind("\n") + 1) + 1


def _filters(parts, err) -> tuple:
    out = []
    for f in parts:
        name, _, arg = f.partition(":")
        if name not in FILTERS:
            err(f"unknown filter {name!r}")
        if name == "cap" and not re.fullmatch(r"\d+", arg):
            err("filter cap:N needs an integer")
        if name == "rstrip":
            try:
                if not isinstance(json.loads(arg), str):
                    raise ValueError
            except ValueError:
                err('filter rstrip:"chars" needs a JSON string')
        if name in ("json", "sort") and arg:
            err(f"filter {name} takes no argument")
        out.append(f)
    return tuple(out)


def _parse_section(spec: Spec, section: str) -> list:
    body, start = spec.sections[section], spec._starts[section]
    toks = []                                          # (글, 줄, 열)
    last = 0
    for m in _TOK.finditer(body):
        if m.start() > last:
            toks.append((body[last:m.start()],) + _pos(body, last, start))
        toks.append((m.group(),) + _pos(body, m.start(), start))
        last = m.end()
    if last < len(body):
        toks.append((body[last:],) + _pos(body, last, start))

    def err(msg, line, col):
        raise SpecError(f"section {section}: {msg}", line, col, spec.source)

    def parse(i, stops, opener):
        nodes = []
        while i < len(toks):
            t, line, col = toks[i]
            if t.startswith("{%"):
                tag = t[2:-2].strip()
                word = tag.split()[0] if tag else ""
                if word in stops:
                    return nodes, i, word
                if word in ("else", "c", "end"):
                    err(f"unexpected {{% {word} %}}", line, col)
                if word == "use":
                    parts = tag.split()
                    if len(parts) != 2 or parts[1] not in spec.sections:
                        err(f"use: no section {' '.join(parts[1:])!r}", line, col)
                    nodes.append(_Node("use", tag, [], [], line, col, expr=parts[1]))
                elif word in ("for", "if", "v"):
                    if word == "for":
                        m = _FOR.fullmatch(tag)
                        if not m:
                            err("for: expected `for <x> in <name>[|filter]`", line, col)
                        _filters(filter(None, m.group(3).split("|")), lambda msg: err(msg, line, col))
                    elif word == "if" and not re.fullmatch(r"if [\w.]+", tag):
                        err("if: expected `if <name>`", line, col)
                    elif word == "v" and tag != "v":
                        err("v: takes no argument", line, col)
                    inner = ("c", "end") if word == "v" else ("else", "end")
                    body_, i, end = parse(i + 1, inner, (line, col))
                    alt = []
                    if end in ("else", "c"):
                        alt, i, end = parse(i + 1, ("end",), (line, col))
                    nodes.append(_Node(word, tag, body_, alt, line, col))
                else:
                    err(f"unknown tag {tag!r}", line, col)
            elif t.startswith("{{"):
                parts = [p.strip() for p in t[2:-2].split("|")]
                if not re.fullmatch(r"[\w.]+", parts[0]):
                    err(f"value: expected a name, got {parts[0]!r}", line, col)
                nodes.append(_Node("out", t, [], [], line, col, expr=parts[0],
                                   filters=_filters(parts[1:], lambda msg: err(msg, line, col))))
            else:
                nodes.append(t)
            i += 1
        if stops:
            err(f"missing {{% {' / '.join(stops)} %}}", *opener)
        return nodes, i, None

    nodes, _, _ = parse(0, (), None)
    return nodes


def _uses(spec: Spec, section: str, path: tuple):
    if section in path:
        raise SpecError(f"use cycle: {' -> '.join(path + (section,))}", None, None, spec.source)

    def walk(nodes):
        for n in nodes:
            if isinstance(n, _Node):
                if n.word == "use":
                    _uses(spec, n.expr, path + (section,))
                walk(n.body)
                walk(n.alt)
    walk(spec._trees[section])


def _get(name: str, env: dict) -> Any:
    cur: Any = env
    for part in name.split("."):
        cur = cur.get(part, "") if isinstance(cur, dict) else getattr(cur, part, "")
    return cur


def _filter(v: Any, f: str) -> Any:
    name, _, arg = f.partition(":")
    if name == "json":
        return json.dumps(v, ensure_ascii=False)
    if name == "cap":
        return str(v)[: int(arg)]
    if name == "rstrip":
        return str(v).rstrip(json.loads(arg))
    if name == "sort":
        return sorted(v, key=lambda x: x.get("key", "") if isinstance(x, dict) else x)
    raise SpecError(f"unknown filter {name!r}")


def _render(spec: Spec, nodes: list, env: dict, mode: str) -> str:
    out = []
    for n in nodes:
        if isinstance(n, str):
            out.append(n)
            continue
        if n.word == "out":
            v = _get(n.expr, env)
            for f in n.filters:
                v = _filter(v, f)
            out.append(str(v))
        elif n.word == "use":
            out.append(_render(spec, spec._trees[n.expr], env, mode))
        elif n.word == "v":
            out.append(_render(spec, n.body if mode == "verbatim" else n.alt, env, mode))
        elif n.word == "if":
            out.append(_render(spec, n.body if _get(n.tag.split()[1], env) else n.alt, env, mode))
        elif n.word == "for":
            m = _FOR.fullmatch(n.tag)
            seq = _get(m.group(2), env)
            for f in filter(None, m.group(3).split("|")):
                seq = _filter(seq, f)
            if isinstance(seq, dict):
                bad = [k for k, x in seq.items() if not isinstance(x, dict)]
                if bad:
                    raise SpecError(f"for {m.group(2)}: dict values must be objects ({bad[0]!r})", n.line, n.col,
                                    spec.source)
                seq = [{"key": k, **x} for k, x in sorted(seq.items())]
            if not seq:
                out.append(_render(spec, n.alt, env, mode))
            for x in seq:
                out.append(_render(spec, n.body, {**env, m.group(1): x}, mode))
    return "".join(out)


def compile(spec: Spec, section: str, values: dict, mode: str = "verbatim") -> str:
    """구역 하나의 프롬프트 글. 입력은 선언한 것 모두, 그것만."""
    if mode not in MODES:
        raise SpecError(f"mode must be one of {MODES}, got {mode!r}")
    if section not in spec.sections:
        raise SpecError(f"no section {section!r}", None, None, spec.source)
    missing = [n for n in spec.inputs if n not in values]
    if missing:
        raise SpecError(f"missing inputs: {missing}", None, None, spec.source)
    unknown = sorted(set(values) - set(spec.inputs))
    if unknown:
        raise SpecError(f"undeclared inputs: {unknown}", None, None, spec.source)
    env = {**spec.lets, **values, "out": out_form(spec), "out_schema": spec.out_schema}
    return _render(spec, spec._trees[section], env, mode)


def tokens(text: str) -> int:
    """오프라인 추정: ceil(utf-8 바이트 / 4). 공급자 토크나이저가 아니다 -- 실제 값은 Telemetry 사용량."""
    return math.ceil(len(text.encode("utf-8")) / 4)


# ── 대화 · 토큰 보고 ────────────────────────────────────────────────────────────────────────────────────────────

def conversation(spec: Spec, first_values: dict, turns: list, mode: str = "verbatim", resumes: bool = True,
                 first: str = "first", turn: str = "turn", turn_noresume: str = "turn_noresume") -> "list[str]":
    """한 대화의 프롬프트 글들: 첫 턴은 first, 뒤 턴은 turn(호스트가 --resume 으로 앞을 기억할 때) 또는 turn_noresume.
    once 구역은 기억하는 호스트에 다시 보내지 않는다 -- 그 판단은 여기 하나다."""
    sec = turn if resumes else turn_noresume
    return [compile(spec, first, first_values, mode)] + [compile(spec, sec, v, mode) for v in turns]


def usage_report(usage: "dict | None", usage_format: "str | None") -> "dict | None":
    """공급자가 보고한 사용량 -> Telemetry L0 칸(input_tokens · cache_read_input_tokens · output_tokens …). 없으면 None."""
    if usage is None or usage_format is None:
        return None
    from telemetry.usage import l0_usage
    vals, _ = l0_usage(usage_format, usage)
    sent = [vals.get(k) for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")]
    return dict(vals, prompt_tokens=sum(x for x in sent if x is not None) if any(x is not None for x in sent) else None)


def token_report(texts: "list[str]", usages: "list | None" = None, usage_format: "str | None" = None) -> dict:
    """턴마다 오프라인 추정과, 부른 쪽이 넘긴 공급자 사용량(Telemetry 로 읽음)을 나란히. 합계도."""
    usages = list(usages or [None] * len(texts))
    if len(usages) != len(texts):
        raise ValueError("usages: 턴마다 하나(없으면 None)")
    rows = []
    for k, (t, u) in enumerate(zip(texts, usages)):
        rows.append({"turn": k, "bytes": len(t.encode("utf-8")), "estimate": tokens(t),
                     "provider": usage_report(u, usage_format)})
    actual = [r["provider"]["prompt_tokens"] for r in rows if r["provider"] and r["provider"]["prompt_tokens"] is not None]
    return {"turns": rows, "estimate": sum(r["estimate"] for r in rows),
            "provider_prompt_tokens": sum(actual) if len(actual) == len(rows) and rows else None,
            "usage_format": usage_format}
