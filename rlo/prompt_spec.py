"""prompt-spec/1 -- LLM 일 하나의 의미 서명, 그것에서 짓는 결정론적 프롬프트와 답 검사기(CMD-K15 S1 · S2, BD-288).

    spec = PromptSpec.load(json.load(open("plan.prompt-spec.json")))     # 꼴이 틀리면 SpecError
    c = spec.compile({"id": "base"}, {"tools": rows})                    # 같은 입력 -> 같은 바이트(어느 OS 에서나)
    r = c.check(answer_text)                                             # 같은 명세에서 지은 검사기: r.ok · r.opinion · r.problems

명세는 코드가 아니라 데이터다. 입력마다 SEMANTIC_MODEL 낱말(§1 · §2.17)과 basis 를 단다. 답은 **Opinion** 이다(§2.18) --
권위가 없고, State 가 아니다. 그래서 output.term 은 "Opinion" 만 받는다.

변형(variant)은 지시 글(wording 칸 · 입력 이름표) · 출력 칸 차례 · 예시 고르기만 바꾼다. goal · inputs · output · rules 를 바꾸려는
변형은 VariantError 다. 컴파일은 명세 목록의 차례만 쓴다(dict · set 의 차례, 시각, 로캘, 난수에 기대지 않는다).
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import re

SCHEMA = "prompt-spec/1"
# SEMANTIC_MODEL §1 의 낱말과 §2.17(Constraint · Capability · Purpose). Safety 는 Guard 라고도 부른다
TERMS = ("Observation", "Telemetry", "Measurement", "State", "Assessment", "Model", "Relationship", "Evidence",
         "DecisionContext", "Policy", "Decision", "Arbitration", "Guard", "Action", "Verification", "Runtime", "Opinion",
         "Constraint", "Capability", "Purpose")
# SEMANTIC_MODEL §2.3 의 basis 어휘
BASES = ("OBSERVED", "DEFINITIONAL", "RUNTIME_DECLARED", "PROVIDER_DECLARED", "OPERATOR_ASSUMED", "EXTERNAL_LABEL",
         "VALIDATED_EXPERIMENT", "ESTIMATE")
TYPES = ("string", "integer", "number", "boolean", "array", "object", "const", "enum")
# 변형이 바꿀 수 있는 지시 글 칸(이것 밖은 없다). 기본 글은 DEFAULT_WORDING
DEFAULT_WORDING = {
    "intro": None,          # None 이면 goal + "\n"
    "form_lead": "Answer with exactly one JSON object in a ```json fenced block, of this form:\n",
    "form_end": "\n",
    "rules_lead": "Rules: ",
    "rules_join": "; ",
    "rules_end": ".\n",
    "examples_lead": "Examples:\n",
    "example_in": "Example input:\n",
    "example_out": "Example answer:\n",
    "inputs_lead": "Input:\n",
    "outro": "",
}
SLOTS = tuple(DEFAULT_WORDING)
VARIANT_KEYS = ("id", "wording", "input_labels", "field_order", "shots")
NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
LABEL = re.compile(r"^[A-Za-z0-9_.:+-]{1,64}$")


class SpecError(ValueError):
    """명세의 꼴이 틀렸다."""


class VariantError(ValueError):
    """변형이 바꿀 수 없는 것을 바꾸려 했거나 꼴이 틀렸다."""


class CompileError(ValueError):
    """컴파일 입력이 명세와 맞지 않는다(빠진 입력 · 모르는 입력)."""


def canonical(x) -> str:
    """결정론적 JSON(키 정렬 · 빈칸 없음 · 유니코드 그대로)."""
    return json.dumps(x, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def digest(x) -> str:
    return hashlib.sha256((x if isinstance(x, str) else canonical(x)).encode("utf-8")).hexdigest()


def _keys(d, allowed, required, where, err=SpecError):
    if not isinstance(d, dict):
        raise err(f"{where}: 객체여야 한다")
    extra = set(d) - set(allowed)
    if extra:
        raise err(f"{where}: 모르는 칸 {sorted(extra)}")
    missing = [k for k in required if k not in d]
    if missing:
        raise err(f"{where}: 빠진 칸 {missing}")


def _text(v, where, err=SpecError, empty=False):
    if not isinstance(v, str) or (not empty and not v.strip()):
        raise err(f"{where}: {'글' if empty else '비지 않은 글'}이어야 한다")
    return v


@dataclasses.dataclass(frozen=True)
class Field:
    name: str
    type: str
    required: bool = True
    nullable: bool = False
    about: str = ""
    shape: "str | None" = None       # 출력 꼴 줄에 보일 글(없으면 타입에서 짓는다)
    value: object = None             # const
    values: tuple = ()               # enum

    def shown(self) -> str:
        if self.shape is not None:
            return self.shape
        if self.type == "const":
            return json.dumps(self.value, ensure_ascii=False)
        if self.type == "enum":
            return " | ".join(json.dumps(v, ensure_ascii=False) for v in self.values)
        base = {"string": json.dumps(f"<{self.about or self.name}>", ensure_ascii=False), "integer": "<integer>",
                "number": "<number>", "boolean": "true | false", "array": "[...]", "object": "{...}"}[self.type]
        return base + (" or null" if self.nullable else "")


@dataclasses.dataclass(frozen=True)
class Opinion:
    """LLM 의 답(SEMANTIC_MODEL §2.18). 권위가 없다 -- State · DC 에 들어가지 않는다."""
    spec: str
    value: dict
    term: str = "Opinion"


@dataclasses.dataclass(frozen=True)
class Check:
    ok: bool
    opinion: "Opinion | None"
    problems: tuple


@dataclasses.dataclass(frozen=True)
class Compiled:
    text: str
    spec: "PromptSpec"
    variant: str

    @property
    def sha256(self) -> str:
        return digest(self.text)

    def check(self, answer: str) -> Check:
        """이 프롬프트를 지은 바로 그 명세의 검사기로 답을 본다(프롬프트와 파서가 어긋나지 않는다)."""
        return self.spec.check(answer)


class PromptSpec:
    """검증된 prompt-spec/1. 만들면 바꾸지 않는다."""

    def __init__(self, raw: dict):
        self.raw = json.loads(canonical(raw))                       # 사본(밖에서 고쳐도 이 명세는 그대로)
        self._validate(self.raw)

    @classmethod
    def load(cls, raw: dict) -> "PromptSpec":
        return cls(raw)

    # ── 검증 ──
    def _validate(self, d):
        _keys(d, ("schema", "id", "version", "goal", "inputs", "output", "rules", "examples", "wording", "input_labels"),
              ("schema", "id", "version", "goal", "inputs", "output"), "prompt-spec")
        if d["schema"] != SCHEMA:
            raise SpecError(f"schema 는 {SCHEMA} 이어야 한다")
        if not (isinstance(d["id"], str) and LABEL.match(d["id"])):
            raise SpecError("id: 라벨이어야 한다")
        _text(d["version"], "version")
        self.goal = _text(d["goal"], "goal")
        if not isinstance(d["inputs"], list):
            raise SpecError("inputs: 목록이어야 한다")
        names = []
        for i, x in enumerate(d["inputs"]):
            _keys(x, ("name", "term", "basis", "about"), ("name", "term", "basis"), f"inputs[{i}]")
            if not (isinstance(x["name"], str) and NAME.match(x["name"])) or x["name"] in names:
                raise SpecError(f"inputs[{i}].name: 겹치지 않는 이름이어야 한다")
            if x["term"] not in TERMS:
                raise SpecError(f"inputs[{i}].term: SEMANTIC_MODEL 낱말이 아니다 ({x['term']!r})")
            if x["basis"] not in BASES:
                raise SpecError(f"inputs[{i}].basis: basis 어휘가 아니다 ({x['basis']!r})")
            if "about" in x:
                _text(x["about"], f"inputs[{i}].about", empty=True)
            names.append(x["name"])
        self.inputs = tuple(dict(x) for x in d["inputs"])
        o = d["output"]
        _keys(o, ("term", "format", "fields"), ("term", "format", "fields"), "output")
        if o["term"] != "Opinion":
            raise SpecError("output.term: LLM 의 답은 Opinion 이다(State 가 아니다)")
        if o["format"] != "json":
            raise SpecError("output.format: json 만 안다")
        if not isinstance(o["fields"], list) or not o["fields"]:
            raise SpecError("output.fields: 비지 않은 목록이어야 한다")
        fields, seen = [], set()
        for i, f in enumerate(o["fields"]):
            w = f"output.fields[{i}]"
            _keys(f, ("name", "type", "required", "nullable", "about", "shape", "value", "values"), ("name", "type"), w)
            if not (isinstance(f["name"], str) and f["name"]) or f["name"] in seen:
                raise SpecError(f"{w}.name: 겹치지 않는 이름이어야 한다")
            if f["type"] not in TYPES:
                raise SpecError(f"{w}.type: {TYPES} 가운데 하나")
            for k in ("required", "nullable"):
                if k in f and not isinstance(f[k], bool):
                    raise SpecError(f"{w}.{k}: 참 · 거짓")
            if f["type"] == "const" and "value" not in f:
                raise SpecError(f"{w}: const 는 value 가 있어야 한다")
            if f["type"] == "enum" and not (isinstance(f.get("values"), list) and f["values"]):
                raise SpecError(f"{w}: enum 은 values 가 있어야 한다")
            for k in ("about", "shape"):
                if k in f:
                    _text(f[k], f"{w}.{k}", empty=True)
            seen.add(f["name"])
            fields.append(Field(f["name"], f["type"], f.get("required", True), f.get("nullable", False), f.get("about", ""),
                                f.get("shape"), f.get("value"), tuple(f.get("values", ()))))
        self.fields = tuple(fields)
        rules = d.get("rules", [])
        if not isinstance(rules, list):
            raise SpecError("rules: 목록이어야 한다")
        self.rules = tuple(_text(r, f"rules[{i}]") for i, r in enumerate(rules))
        self.wording = self._wording(d.get("wording", {}), SpecError, "wording")
        self.input_labels = self._labels(d.get("input_labels", {}), SpecError)
        ex = d.get("examples", [])
        if not isinstance(ex, list):
            raise SpecError("examples: 목록이어야 한다")
        for i, e in enumerate(ex):
            _keys(e, ("inputs", "output"), ("inputs", "output"), f"examples[{i}]")
            self._inputs(e["inputs"], SpecError, f"examples[{i}].inputs")
            p = self.problems(e["output"])
            if p:
                raise SpecError(f"examples[{i}].output: 이 명세의 검사기를 지나지 않는다 {p}")
        self.examples = tuple(ex)
        self.ref = f"{d['id']}@{d['version']}#{digest(d)[:12]}"

    def _wording(self, w, err, where):
        _keys(w, SLOTS, (), where, err)
        for k, v in w.items():
            _text(v, f"{where}.{k}", err, empty=True)
        return dict(w)

    def _labels(self, labels, err):
        _keys(labels, [x["name"] for x in self.inputs], (), "input_labels", err)
        for k, v in labels.items():
            _text(v, f"input_labels.{k}", err, empty=True)
        return dict(labels)

    def _inputs(self, values, err, where):
        if not isinstance(values, dict):
            raise err(f"{where}: 객체여야 한다")
        names = [x["name"] for x in self.inputs]
        missing, extra = [n for n in names if n not in values], sorted(set(values) - set(names))
        if missing or extra:
            raise err(f"{where}: 빠진 입력 {missing} · 모르는 입력 {extra}")

    # ── 변형 ──
    def variant(self, v: dict) -> dict:
        """변형을 검사하고 명세의 기본과 합친다. 바꿀 수 없는 것을 건드리면 VariantError."""
        if not isinstance(v, dict):
            raise VariantError("변형은 객체여야 한다")
        extra = sorted(set(v) - set(VARIANT_KEYS))
        if extra:
            raise VariantError(f"변형은 {list(VARIANT_KEYS)} 만 바꾼다 -- {extra} 는 명세의 것이다(goal · inputs · output · rules)")
        if not (isinstance(v.get("id"), str) and LABEL.match(v["id"])):
            raise VariantError("변형 id: 라벨이어야 한다")
        wording = {**DEFAULT_WORDING, **self.wording, **self._wording(v.get("wording", {}), VariantError, "wording")}
        labels = {**self.input_labels, **self._labels(v.get("input_labels", {}), VariantError)}
        names = [f.name for f in self.fields]
        order = v.get("field_order", names)
        if not isinstance(order, list) or sorted(order) != sorted(names) or len(order) != len(names):
            raise VariantError("field_order: 출력 칸 이름의 순열이어야 한다(칸을 빼거나 더하지 않는다)")
        shots = v.get("shots", [])
        if not isinstance(shots, list) or len(set(shots)) != len(shots) or not all(
                isinstance(i, int) and not isinstance(i, bool) and 0 <= i < len(self.examples) for i in shots):
            raise VariantError("shots: 명세 예시의 겹치지 않는 번호 목록이어야 한다")
        return {"id": v["id"], "wording": wording, "input_labels": labels, "field_order": list(order), "shots": list(shots)}

    # ── 컴파일 ──
    def _value(self, v) -> str:
        return v if isinstance(v, str) else canonical(v)

    def _render_inputs(self, values, labels) -> str:
        out = []
        for x in self.inputs:                                      # 명세의 차례 -- 넘겨받은 dict 의 차례가 아니다
            label = labels.get(x["name"], f"{x['name']} ({x['term']}, basis {x['basis']}):\n")
            out.append(label + self._value(values[x["name"]]) + "\n")
        return "".join(out)

    def compile(self, variant: dict, inputs: dict) -> Compiled:
        v = self.variant(variant)
        self._inputs(inputs, CompileError, "inputs")
        w = v["wording"]
        by = {f.name: f for f in self.fields}
        order = [by[n] for n in v["field_order"]]
        parts = [w["intro"] if w["intro"] is not None else self.goal + "\n", w["form_lead"],
                 "{" + ", ".join(f"{json.dumps(f.name, ensure_ascii=False)}: {f.shown()}" for f in order) + "}",
                 w["form_end"]]
        if self.rules:
            parts.append(w["rules_lead"] + w["rules_join"].join(self.rules) + w["rules_end"])
        if v["shots"]:
            parts.append(w["examples_lead"])
            for i in v["shots"]:
                e = self.examples[i]
                body = "{" + ", ".join(f"{json.dumps(f.name, ensure_ascii=False)}: "
                                       f"{json.dumps(e['output'][f.name], ensure_ascii=False, sort_keys=True)}"
                                       for f in order if f.name in e["output"]) + "}"
                parts.append(w["example_in"] + self._render_inputs(e["inputs"], v["input_labels"]) + w["example_out"]
                             + body + "\n")
        parts.append(w["inputs_lead"] + self._render_inputs(inputs, v["input_labels"]) + w["outro"])
        return Compiled("".join(parts), self, v["id"])

    # ── 검사기 ──
    def problems(self, obj) -> list:
        """답 객체의 문제(칸 이름 · 종류만 -- 값은 되풀이하지 않는다)."""
        if not isinstance(obj, dict):
            return ["answer: not an object"]
        out = [f"unknown_field:{k}" for k in sorted(set(obj) - {f.name for f in self.fields})]
        for f in self.fields:
            if f.name not in obj:
                if f.required:
                    out.append(f"missing:{f.name}")
                continue
            x = obj[f.name]
            if x is None:
                if not f.nullable:
                    out.append(f"null:{f.name}")
                continue
            ok = {"string": isinstance(x, str), "integer": isinstance(x, int) and not isinstance(x, bool),
                  "number": isinstance(x, (int, float)) and not isinstance(x, bool), "boolean": isinstance(x, bool),
                  "array": isinstance(x, list), "object": isinstance(x, dict),
                  "const": x == f.value and type(x) is type(f.value), "enum": x in f.values}[f.type]
            if not ok:
                out.append(f"type:{f.name}")
        return out

    def check(self, answer: str) -> Check:
        """답 글 -> Check. JSON 은 마지막 ```json 울타리 안, 없으면 글 전체다."""
        if not isinstance(answer, str):
            return Check(False, None, ("answer: not text",))
        blocks = re.findall(r"```(?:json)?\s*\n(.*?)```", answer, re.S)
        src = blocks[-1] if blocks else answer.strip()
        try:
            obj = json.loads(src)
        except ValueError:
            return Check(False, None, ("not_json",))
        p = self.problems(obj)
        return Check(not p, None if p else Opinion(self.ref, obj), tuple(p))


def compile_prompt(spec: "PromptSpec | dict", variant: dict, inputs: dict) -> Compiled:
    return (spec if isinstance(spec, PromptSpec) else PromptSpec.load(spec)).compile(variant, inputs)
