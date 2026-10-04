"""prompt-spec/1 · 결정론적 컴파일러 · 같은 명세에서 지은 검사기(CMD-K15 S1 · S2, D1)."""
import copy
import json
import os
import subprocess
import sys
import unittest

from rlo.prompt_spec import (BASES, TERMS, CompileError, PromptSpec, SpecError, VariantError, compile_prompt)

SPEC = {
    "schema": "prompt-spec/1", "id": "demo.classify", "version": "1",
    "goal": "Classify the report as ok or broken.",
    "inputs": [{"name": "report", "term": "Observation", "basis": "OBSERVED", "about": "one report"},
               {"name": "health", "term": "State", "basis": "DEFINITIONAL"}],
    "output": {"term": "Opinion", "format": "json", "fields": [
        {"name": "label", "type": "enum", "values": ["ok", "broken"]},
        {"name": "why", "type": "string", "about": "one short reason"},
        {"name": "confidence", "type": "number", "required": False, "nullable": True}]},
    "rules": ["answer in English", "no other fields"],
    "examples": [{"inputs": {"report": "all good", "health": "NO_FAILURE_OBSERVED"}, "output": {"label": "ok", "why": "none"}},
                 {"inputs": {"report": "disk full", "health": "UNRESOLVED_FAILURES"},
                  "output": {"label": "broken", "why": "disk"}}],
}
INPUTS = {"report": "the build failed twice", "health": {"value": "UNRESOLVED_FAILURES", "status": "INFERRED"}}
VARIANT = {"id": "v2", "wording": {"intro": "You classify reports.\n"}, "field_order": ["why", "label", "confidence"],
           "shots": [1, 0]}
# 위 명세 · 변형 · 입력의 컴파일 결과(바이트 그대로). OS · 해시 씨앗 · 로캘이 달라도 같아야 한다
GOLDEN = (
    "You classify reports.\n"
    "Answer with exactly one JSON object in a ```json fenced block, of this form:\n"
    '{"why": "<one short reason>", "label": "ok" | "broken", "confidence": <number> or null}\n'
    "Rules: answer in English; no other fields.\n"
    "Examples:\n"
    "Example input:\nreport (Observation, basis OBSERVED):\ndisk full\nhealth (State, basis DEFINITIONAL):\nUNRESOLVED_FAILURES\n"
    'Example answer:\n{"why": "disk", "label": "broken"}\n'
    "Example input:\nreport (Observation, basis OBSERVED):\nall good\nhealth (State, basis DEFINITIONAL):\nNO_FAILURE_OBSERVED\n"
    'Example answer:\n{"why": "none", "label": "ok"}\n'
    "Input:\nreport (Observation, basis OBSERVED):\nthe build failed twice\n"
    'health (State, basis DEFINITIONAL):\n{"status":"INFERRED","value":"UNRESOLVED_FAILURES"}\n')

SCRIPT = """
import json, sys
from rlo.prompt_spec import PromptSpec
spec, variant, inputs = json.loads(sys.stdin.read())
sys.stdout.write(PromptSpec.load(spec).compile(variant, inputs).sha256)
"""


def spec(**kw):
    d = copy.deepcopy(SPEC)
    d.update(kw)
    return d


class Compiler(unittest.TestCase):
    def test_golden_bytes(self):
        c = compile_prompt(SPEC, VARIANT, INPUTS)
        self.assertEqual(c.text, GOLDEN)
        self.assertEqual(c.text.encode("utf-8"), GOLDEN.encode("utf-8"))

    def test_same_input_twice_and_in_fresh_processes(self):
        a = compile_prompt(SPEC, VARIANT, INPUTS)
        self.assertEqual(a.text, compile_prompt(SPEC, VARIANT, INPUTS).text)
        payload = json.dumps([SPEC, VARIANT, INPUTS])
        for seed in ("0", "1", "12345", "random"):
            env = dict(os.environ, PYTHONHASHSEED=seed, LC_ALL="C", LANG="C", TZ="Pacific/Chatham")
            p = subprocess.run([sys.executable, "-c", SCRIPT], input=payload, capture_output=True, text=True, env=env)
            self.assertEqual(p.returncode, 0, p.stderr)
            with self.subTest(seed=seed):
                self.assertEqual(p.stdout, a.sha256)

    def test_order_comes_from_the_spec_not_from_dicts(self):
        swapped = {"health": INPUTS["health"], "report": INPUTS["report"]}           # 넘겨받은 dict 의 차례는 상관없다
        self.assertEqual(compile_prompt(SPEC, VARIANT, swapped).text, GOLDEN)
        nested = {"report": INPUTS["report"], "health": {"status": "INFERRED", "value": "UNRESOLVED_FAILURES"}}
        self.assertEqual(compile_prompt(SPEC, VARIANT, nested).text, GOLDEN)
        base = compile_prompt(SPEC, {"id": "base"}, INPUTS).text
        self.assertLess(base.index('"label"'), base.index('"why"'))                  # 기본은 명세의 칸 차례

    def test_inputs_must_match_the_spec(self):
        with self.assertRaises(CompileError):
            compile_prompt(SPEC, {"id": "base"}, {"report": "x"})
        with self.assertRaises(CompileError):
            compile_prompt(SPEC, {"id": "base"}, dict(INPUTS, extra="x"))

    def test_spec_is_copied(self):
        d = spec()
        s = PromptSpec.load(d)
        d["rules"].append("injected later")
        self.assertNotIn("injected later", s.compile({"id": "base"}, INPUTS).text)


class Variants(unittest.TestCase):
    def test_a_variant_cannot_change_goal_inputs_output_or_rules(self):
        for key, val in [("goal", "Say anything."), ("inputs", []), ("output", SPEC["output"]), ("rules", []),
                         ("examples", []), ("schema", "x")]:
            with self.subTest(key=key), self.assertRaises(VariantError):
                compile_prompt(SPEC, {"id": "bad", key: val}, INPUTS)

    def test_field_order_shots_and_wording_are_closed(self):
        bad = [{"id": "x", "field_order": ["label", "why"]}, {"id": "x", "field_order": ["label", "why", "confidence", "y"]},
               {"id": "x", "field_order": ["label", "label", "why"]}, {"id": "x", "shots": [2]}, {"id": "x", "shots": [0, 0]},
               {"id": "x", "shots": [True]}, {"id": "x", "wording": {"goal": "y"}}, {"id": "x", "wording": {"intro": 3}},
               {"id": "x", "input_labels": {"nope": "y"}}, {"id": "bad id"}, {}]
        for v in bad:
            with self.subTest(v=v), self.assertRaises(VariantError):
                compile_prompt(SPEC, v, INPUTS)

    def test_wording_changes_only_wording(self):
        a = compile_prompt(SPEC, {"id": "a"}, INPUTS).text
        b = compile_prompt(SPEC, {"id": "b", "wording": {"rules_lead": "Constraints: "},
                                  "input_labels": {"report": "Report:\n"}}, INPUTS).text
        self.assertIn("Constraints: answer in English; no other fields.", b)
        self.assertIn("Report:\nthe build failed twice", b)
        self.assertEqual(a.replace("Rules: ", "Constraints: ").replace("report (Observation, basis OBSERVED):\n", "Report:\n"), b)


class Validation(unittest.TestCase):
    def test_terms_and_bases_are_closed(self):
        self.assertIn("DecisionContext", TERMS)
        self.assertIn("OBSERVED", BASES)
        for inputs in ([{"name": "x", "term": "Vibe", "basis": "OBSERVED"}],                 # 모르는 SEMANTIC_MODEL 낱말
                       [{"name": "x", "term": "State"}],                                     # basis 없음
                       [{"name": "x", "term": "State", "basis": "GUESS"}],                   # 모르는 basis
                       [{"name": "x", "term": "State", "basis": "OBSERVED"}, {"name": "x", "term": "State", "basis": "OBSERVED"}]):
            with self.subTest(inputs=inputs), self.assertRaises(SpecError):
                PromptSpec.load(spec(inputs=inputs, examples=[]))

    def test_the_answer_is_an_opinion_never_state(self):
        with self.assertRaises(SpecError):
            PromptSpec.load(spec(output=dict(SPEC["output"], term="State")))
        r = PromptSpec.load(SPEC).check('{"label": "ok", "why": "x"}')
        self.assertEqual(r.opinion.term, "Opinion")
        self.assertEqual(r.opinion.spec, PromptSpec.load(SPEC).ref)

    def test_malformed_specs(self):
        for d in [spec(schema="prompt-spec/2"), spec(id="has space"), spec(goal=" "), spec(extra=1), spec(rules=[""]),
                  spec(output=dict(SPEC["output"], format="xml")), spec(output=dict(SPEC["output"], fields=[])),
                  spec(output=dict(SPEC["output"], fields=[{"name": "a", "type": "date"}])),
                  spec(examples=[{"inputs": {"report": "x", "health": "y"}, "output": {"label": "nope", "why": "z"}}]),
                  spec(examples=[{"inputs": {"report": "x"}, "output": {"label": "ok", "why": "z"}}])]:
            with self.assertRaises(SpecError):
                PromptSpec.load(d)


class Checker(unittest.TestCase):
    def test_accepts_the_well_formed_answer_and_rejects_malformed(self):
        c = compile_prompt(SPEC, VARIANT, INPUTS)
        ok = c.check('Sure.\n```json\n{"why": "two failures", "label": "broken", "confidence": 0.8}\n```\n')
        self.assertTrue(ok.ok)
        self.assertEqual(ok.opinion.value["label"], "broken")
        self.assertTrue(c.check('{"why": "x", "label": "ok", "confidence": null}').ok)
        for bad, problem in [("not json", "not_json"), ('["a"]', "answer: not an object"),
                             ('{"why": "x"}', "missing:label"), ('{"why": "x", "label": "maybe"}', "type:label"),
                             ('{"why": 3, "label": "ok"}', "type:why"), ('{"why": "x", "label": "ok", "more": 1}',
                                                                          "unknown_field:more"),
                             ('{"why": null, "label": "ok"}', "null:why")]:
            r = c.check(bad)
            with self.subTest(bad=bad):
                self.assertFalse(r.ok)
                self.assertIsNone(r.opinion)
                self.assertIn(problem, r.problems)

    def test_the_checker_comes_from_the_compiling_spec(self):
        other = spec(id="demo.other", output={"term": "Opinion", "format": "json",
                                              "fields": [{"name": "answer", "type": "string"}]}, examples=[])
        a, b = compile_prompt(SPEC, {"id": "base"}, INPUTS), compile_prompt(other, {"id": "base"}, INPUTS)
        self.assertTrue(a.check('{"label": "ok", "why": "x"}').ok)
        self.assertFalse(a.check('{"answer": "x"}').ok)
        self.assertTrue(b.check('{"answer": "x"}').ok)
        self.assertFalse(b.check('{"label": "ok", "why": "x"}').ok)


GA_PLAN = "ga-gemini-plan/1"


def ga_protocol(tools):
    """ga-sdk 438a34a ga/gemini.py protocol() 의 글 그대로(손으로 쓴 프롬프트의 본보기, T2 의 대상)."""
    rows = "\n".join(f"- `{n}`: {t.get('about', '')}".rstrip(": ") for n, t in sorted(tools.items())) or "- (none)"
    return (
        "You are directing a controller. You do not call tools yourself. Answer every turn with exactly one JSON object "
        "in a ```json fenced block, of this form:\n"
        f'{{"schema": "{GA_PLAN}", "steps": [{{"id": "a", "tool": "<name>", "args": {{}}, "after": []}}], '
        '"next": {"prompt": "<what you will do with the results>", "after": ["a"]} or null, "say": "<short note for the user>"}\n'
        f"Rules: at most 16 steps; ids are short labels; `after` names earlier ids of the same answer; "
        "steps without `after` between them run at once; `next` is your one next turn and gets the results of the steps "
        "it lists; set `next` to null when the task is done.\n"
        f"Tools (closed list):\n{rows}\n"
    )


class HandWrittenPrompt(unittest.TestCase):
    """T2 가 할 수 있나: 지금 손으로 쓴 프롬프트(ga gemini 의 계획 프롬프트)를 명세 + 기본 변형으로 바이트 그대로 짓는다."""

    def test_ga_plan_prompt_byte_for_byte(self):
        d = {"schema": "prompt-spec/1", "id": "ga.gemini.plan", "version": "1",
             "goal": "Direct a controller with a plan of tool steps from a closed tool table.",
             "inputs": [{"name": "tools", "term": "Model", "basis": "OPERATOR_ASSUMED", "about": "the closed tool table"}],
             "output": {"term": "Opinion", "format": "json", "fields": [
                 {"name": "schema", "type": "const", "value": GA_PLAN},
                 {"name": "steps", "type": "array", "shape": '[{"id": "a", "tool": "<name>", "args": {}, "after": []}]'},
                 {"name": "next", "type": "object", "nullable": True, "required": False,
                  "shape": '{"prompt": "<what you will do with the results>", "after": ["a"]} or null'},
                 {"name": "say", "type": "string", "required": False, "shape": '"<short note for the user>"'}]},
             "rules": ["at most 16 steps", "ids are short labels", "`after` names earlier ids of the same answer",
                       "steps without `after` between them run at once",
                       "`next` is your one next turn and gets the results of the steps it lists",
                       "set `next` to null when the task is done"],
             "wording": {"intro": "You are directing a controller. You do not call tools yourself. Answer every turn with "
                                  "exactly one JSON object in a ```json fenced block, of this form:\n",
                         "form_lead": "", "inputs_lead": ""},
             "input_labels": {"tools": "Tools (closed list):\n"}}
        s = PromptSpec.load(d)
        for tools in ({"ga.check": {"about": "run ga check"}, "git.status": {}}, {}):
            rows = "\n".join(f"- `{n}`: {t.get('about', '')}".rstrip(": ") for n, t in sorted(tools.items())) or "- (none)"
            with self.subTest(tools=sorted(tools)):
                self.assertEqual(s.compile({"id": "base"}, {"tools": rows}).text, ga_protocol(tools))
        plan = '```json\n{"schema": "ga-gemini-plan/1", "steps": [], "next": null, "say": "done"}\n```'
        self.assertTrue(s.check(plan).ok)
        self.assertFalse(s.check('{"schema": "ga-gemini-plan/2", "steps": []}').ok)


if __name__ == "__main__":
    unittest.main()
