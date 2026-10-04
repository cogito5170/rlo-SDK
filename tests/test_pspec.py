"""prompt-spec/1 (rlo.pspec, CMD-K15 rev 4) -- PROMPT_SPEC.md §4 의 바닥을 fixture 로, §5 의 SDK 몫(오류 위치 · 거르개 ·
Telemetry · 판본 · 성능)을 시험한다. ga 를 들이지 않는다: 기대 글 · 판정은 ga-sdk 438a34a 에서 뜬 fixture 다
(eval/capture_pspec_fixtures.py). 모형을 부르지 않는다."""
import json
import pathlib
import time
import unittest

from rlo import pspec as P

FX = pathlib.Path(__file__).parent / "fixtures" / "pspec"
GA = json.loads((FX / "ga_438a34a.json").read_text(encoding="utf-8"))
SPEC_TEXT = (FX / "gemini-plan.pspec").read_text(encoding="utf-8")
SPEC = P.load(SPEC_TEXT, source="gemini-plan.pspec")
TOOLS = GA["tables"]["six"]["tools"]


def vals(tools=TOOLS, task="", results=(), ask=""):
    return {"tools": tools, "task": task, "results": list(results), "ask": ask}


def turns():
    res = GA["results"]
    return [vals(task=GA["task"], results=res[: (i % 2) + 1], ask=GA["ask"]) for i in range(7)]


try:
    import telemetry.usage  # noqa: F401
    TELEMETRY = True
except ImportError:
    TELEMETRY = False


class Floor(unittest.TestCase):
    """PROMPT_SPEC.md §4 의 줄마다."""

    def test_verbatim_is_byte_identical_to_ga(self):
        self.assertEqual(GA["source"]["sha"][:7], "438a34a")
        for name, t in GA["tables"].items():
            v_once = vals(tools=t["tools"])
            v_first = vals(tools=t["tools"], task=GA["task"])
            v_turn = vals(tools=t["tools"], task=GA["task"], results=GA["results"], ask=GA["ask"])
            for section, v, want in [("once", v_once, t["protocol"]), ("first", v_first, t["first"]),
                                     ("turn", v_turn, t["turn"]), ("turn_noresume", v_turn, t["turn_noresume"])]:
                with self.subTest(table=name, section=section):
                    got = P.compile(SPEC, section, v)
                    self.assertEqual(got.encode("utf-8"), want.encode("utf-8"))

    def test_check_agrees_with_check_plan_on_17_cases(self):
        """run.py(7dc9d13) 의 사례 17: 처음 16 + 자기를 가리키는 after(BD-292 P1)."""
        self.assertEqual(len(GA["plans"]), 17)
        for k, c in enumerate(GA["plans"]):
            with self.subTest(case=k):
                self.assertEqual(bool(P.check(SPEC, c["plan"], {"tools": TOOLS})), bool(c["ga_problems"]))

    def test_unknown_term_unknown_basis_and_missing_input_are_errors(self):
        with self.assertRaises(P.SpecError):
            P.load(SPEC_TEXT.replace("in tools : Model @operator once", "in tools : Vibe @operator once"))
        with self.assertRaises(P.SpecError):
            P.load(SPEC_TEXT.replace("in tools : Model @operator once", "in tools : Model @hunch once"))
        v = vals()
        del v["ask"]
        with self.assertRaises(P.SpecError):
            P.compile(SPEC, "turn", v)

    def test_dict_order_does_not_matter(self):
        rev = {k: dict(reversed(list(v.items()))) for k, v in reversed(list(TOOLS.items()))}
        for section in ("once", "first", "turn", "turn_noresume"):
            for mode in P.MODES:
                with self.subTest(section=section, mode=mode):
                    a = P.compile(SPEC, section, vals(task="t", results=GA["results"], ask="x"), mode)
                    b = P.compile(SPEC, section, vals(tools=rev, task="t", results=GA["results"], ask="x"), mode)
                    self.assertEqual(a, b)

    def test_eight_turn_tokens_no_worse_than_the_prototype(self):
        first = vals(task=GA["task"])
        for resume, verbatim, compact in ((True, 646, 447), (False, 2263, 1574)):
            v = P.token_report(P.conversation(SPEC, first, turns(), "verbatim", resume))["estimate"]
            c = P.token_report(P.conversation(SPEC, first, turns(), "compact", resume))["estimate"]
            with self.subTest(resume=resume):
                self.assertEqual(v, verbatim)                     # verbatim 은 ga 의 글 그대로라 같아야 한다
                self.assertLessEqual(c, compact)
        f = P.compile(SPEC, "first", first)
        self.assertEqual((P.tokens(f), P.tokens(P.compile(SPEC, "first", first, "compact"))), (245, 161))


class Conversation(unittest.TestCase):
    def test_once_is_not_resent_to_a_resuming_host(self):
        texts = P.conversation(SPEC, vals(task=GA["task"]), turns(), "compact", resumes=True)
        once = P.compile(SPEC, "once", vals(), "compact")
        self.assertTrue(texts[0].startswith(once))
        for t in texts[1:]:
            self.assertNotIn("Reply each turn", t)
            self.assertNotIn("Tools:", t)
            self.assertTrue(t.startswith("Results:\n"))

    def test_a_host_without_resume_gets_it_every_turn(self):
        texts = P.conversation(SPEC, vals(task=GA["task"]), turns(), "compact", resumes=False)
        self.assertTrue(all(t.startswith("Reply each turn") for t in texts))


class Template(unittest.TestCase):
    def spec(self, body, header="spec t/1\nin a : Observation @observed turn\n"):
        return P.load(header + "--- s\n" + body)

    def test_single_braces_are_text_and_the_final_newline_is_not(self):
        s = self.spec('{"k": {}} {{ a }}\n')
        self.assertEqual(P.compile(s, "s", {"a": "x"}), '{"k": {}} x')

    def test_filters(self):
        s = self.spec('{{ a|json }}|{{ a|cap:3 }}|{{ a|rstrip:" ." }}|{% for x in a|sort %}{{ x }}{% end %}')
        self.assertEqual(P.compile(s, "s", {"a": "héllo. "}), '"héllo. "|hél|héllo|' + "".join(sorted("héllo. ")))
        s = self.spec("{% for x in a|sort %}{{ x.key }}{% end %}")
        self.assertEqual(P.compile(s, "s", {"a": [{"key": "b"}, {"key": "a"}]}), "ab")

    def test_for_else_if_else_and_dict_iteration(self):
        s = self.spec("{% for t in a %}{{ t.key }}={{ t.n }};{% else %}none{% end %}{% if a %}!{% else %}?{% end %}")
        self.assertEqual(P.compile(s, "s", {"a": {"b": {"n": 2}, "a": {"n": 1}}}), "a=1;b=2;!")
        self.assertEqual(P.compile(s, "s", {"a": {}}), "none?")
        with self.assertRaises(P.SpecError):
            P.compile(s, "s", {"a": {"x": 3}})                      # dict 값은 객체여야

    def test_the_v_c_switch(self):
        s = self.spec("A{% v %}long words{% c %}short{% end %}B{% v %}only verbatim{% end %}")
        self.assertEqual(P.compile(s, "s", {"a": 1}, "verbatim"), "Along wordsBonly verbatim")
        self.assertEqual(P.compile(s, "s", {"a": 1}, "compact"), "AshortB")
        with self.assertRaises(P.SpecError):
            P.compile(s, "s", {"a": 1}, "tiny")

    def test_out_and_out_schema(self):
        self.assertEqual(P.out_form(SPEC), '{"schema":"ga-gemini-plan/1","steps"?:[{"id":id,"tool":tools,"args"?:{},'
                                           '"after"?:[id]}]≤16,"next"?:{"prompt":str+,"after"?:[id]}|null,"say"?:str}')
        self.assertIn(P.out_form(SPEC), P.compile(SPEC, "once", vals(), "compact"))
        self.assertIn('"schema": "ga-gemini-plan/1"', P.compile(SPEC, "once", vals()))

    def test_inputs_must_be_declared(self):
        s = self.spec("{{ a }}")
        with self.assertRaises(P.SpecError):
            P.compile(s, "s", {"a": 1, "b": 2})
        with self.assertRaises(P.SpecError):
            P.compile(s, "nope", {"a": 1})


class Checker(unittest.TestCase):
    def ok(self, plan):
        return P.check(SPEC, plan, {"tools": TOOLS})

    def test_types(self):
        base = {"schema": "ga-gemini-plan/1"}
        self.assertEqual(self.ok(base), [])
        self.assertEqual(self.ok(dict(base, schema="x")), ["$.schema"])                       # "lit"
        self.assertEqual(self.ok(dict(base, say=3)), ["$.say"])                               # str
        self.assertEqual(self.ok(dict(base, next={"prompt": " "})), ["$.next.prompt"])         # str+
        self.assertEqual(self.ok(dict(base, next=None)), [])                                   # T | null
        self.assertEqual(self.ok(dict(base, steps=[{"id": "a", "tool": "noop", "args": []}])), ["$.steps[0].args"])  # {}
        self.assertEqual(self.ok(dict(base, steps=[{"id": "a", "tool": "nope"}])), ["$.steps[0].tool"])  # key(tools)
        self.assertEqual(self.ok(dict(base, steps=[{"id": "a", "tool": 3}])), ["$.steps[0].tool"])
        self.assertEqual(self.ok(dict(base, steps=[{"id": "a b", "tool": "noop"}])), ["$.steps[0].id"])  # id new: let id
        self.assertEqual(self.ok(dict(base, steps=[{"id": "a", "tool": "noop"}, {"id": "a", "tool": "noop"}])),
                         ["$.steps[1].id"])                                                    # id new: 겹침
        self.assertEqual(self.ok(dict(base, steps=[{"id": "a", "tool": "noop"}, {"id": "b", "tool": "noop", "after": ["a"]}],
                                      next={"prompt": "x", "after": ["b"]})), [])             # id seen
        self.assertEqual(self.ok(dict(base, steps=[{"id": "a", "tool": "noop", "after": ["z"]}])), ["$.steps[0].after[0]"])
        self.assertEqual(self.ok(dict(base, extra=1)), ["$.unknown_field"])                   # 모르는 칸
        self.assertEqual(self.ok(dict(base, steps=[{"id": "a", "tool": "noop", "x": 1}])), ["$.steps[0].unknown_field"])
        self.assertEqual(self.ok({}), ["$.schema"])                                            # 빠진 칸
        self.assertEqual(self.ok("x"), ["$"])

    def test_id_seen_is_earlier_objects_only(self):
        """BD-292 P1: 객체의 id new 는 다른 칸 뒤에 등록된다 -- 자기를 가리키는 after 는 거부."""
        self.assertEqual(self.ok({"schema": "ga-gemini-plan/1", "steps": [{"id": "a", "tool": "noop", "after": ["a"]}]}),
                         ["$.steps[0].after[0]"])
        self.assertEqual(self.ok({"schema": "ga-gemini-plan/1", "steps": [{"after": ["a"], "id": "a", "tool": "noop"}]}),
                         ["$.steps[0].after[0]"])                       # 칸 차례와 상관없이
        self.assertEqual(self.ok({"schema": "ga-gemini-plan/1", "steps": [{"id": "a", "tool": "noop"},
                                                                          {"id": "b", "tool": "noop", "after": ["a"]}]}), [])

    def test_id_regex_is_a_fullmatch(self):
        """BD-292 P2: "a\n" 은 `^...$` 에 match 로는 맞지만 통째로는 아니다."""
        for bad in ("a\n", "a\nb", "a" * 13, ""):
            with self.subTest(id=bad):
                self.assertEqual(self.ok({"schema": "ga-gemini-plan/1", "steps": [{"id": bad, "tool": "noop"}]}),
                                 ["$.steps[0].id"])
        self.assertEqual(self.ok({"schema": "ga-gemini-plan/1", "steps": [{"id": "a" * 12, "tool": "noop"}]}), [])

    def test_list_bound_is_exact(self):
        steps = lambda n: [{"id": f"s{i}", "tool": "noop"} for i in range(n)]
        self.assertEqual(self.ok({"schema": "ga-gemini-plan/1", "steps": steps(16)}), [])
        self.assertEqual(self.ok({"schema": "ga-gemini-plan/1", "steps": steps(17)}), ["$.steps"])
        s = P.load("spec t/1\nout t/1\n  xs : [int] <= 2\n--- s\nx")
        self.assertEqual([bool(P.check(s, {"xs": [1] * n})) for n in range(4)], [False, False, False, True])
        self.assertEqual(P.check(s, {"xs": [True]}), ["$.xs[0]"])                          # bool 은 int 가 아니다

    def test_key_without_the_input_rejects(self):
        self.assertEqual(P.check(SPEC, {"schema": "ga-gemini-plan/1", "steps": [{"id": "a", "tool": "noop"}]}, {}),
                         ["$.steps[0].tool"])

    def test_answer_text(self):
        good = 'ok\n```json\n{"schema": "ga-gemini-plan/1", "steps": [], "next": null}\n```'
        self.assertEqual(P.check_text(SPEC, good, {"tools": TOOLS}), [])
        self.assertEqual(P.check_text(SPEC, "no json here", {"tools": TOOLS}), ["$.not_json"])


class Positions(unittest.TestCase):
    """적재 · 템플릿 오류는 줄 · 열을 단다(1 부터)."""

    def where(self, text):
        with self.assertRaises(P.SpecError) as e:
            P.load(text, source="x.pspec")
        return e.exception.line, e.exception.col, str(e.exception)

    def test_header_errors(self):
        h = "spec t/1\ngoal g\n"
        self.assertEqual(self.where(h + "in a : Vibe @observed once\n")[:2], (3, 8))
        self.assertEqual(self.where(h + "in a : State @hunch once\n")[:2], (3, 15))
        self.assertEqual(self.where(h + "in a State\n")[:2], (3, 4))
        self.assertEqual(self.where(h + "let n = x\n")[:2], (3, 9))
        self.assertEqual(self.where(h + "let r = /(/\n")[:2], (3, 9))
        self.assertEqual(self.where(h + "out o/1\n  a : [int] <= \n")[:2], (4, 15))
        self.assertEqual(self.where(h + "out o/1\n  a : bogus\n")[:2], (4, 7))
        self.assertEqual(self.where(h + "out o/1\n  a : {b: int, b: str}\n")[:2], (4, 15))
        self.assertEqual(self.where(h + "frob x\n")[:2], (3, 1))
        self.assertEqual(self.where("goal g\n")[:2], (1, 1))
        self.assertEqual(self.where("spec t\n")[:2], (1, 6))
        self.assertTrue(self.where(h + "frob x\n")[2].startswith("x.pspec:3:1: "))

    def test_template_errors(self):
        h = "spec t/1\nin a : State @observed once\n--- s\n"
        self.assertEqual(self.where(h + "line one\nab {% frob %}\n")[:2], (5, 4))
        self.assertEqual(self.where(h + "x {{ a|loud }}\n")[:2], (4, 3))
        self.assertEqual(self.where(h + "x\n  {% for t in a %}\nno end\n")[:2], (5, 3))
        self.assertEqual(self.where(h + "{% c %}\n")[:2], (4, 1))
        self.assertEqual(self.where(h + "{% if a %}x{% c %}y{% end %}\n")[:2], (4, 12))
        self.assertEqual(self.where(h + "{% use nope %}\n")[:2], (4, 1))
        self.assertEqual(self.where(h + "{{ a|cap:x }}\n")[:2], (4, 1))
        self.assertEqual(self.where(h + "x\n--- u\n{% end %}\n")[:2], (6, 1))
        with self.assertRaises(P.SpecError):
            P.load(h + "{% use u %}\n--- u\n{% use s %}\n")                 # use 순환

    def test_unknown_template_names_are_load_errors(self):
        """BD-292 P3: 입력 · let · out · out_schema · 반복 변수(와 그 속성) 밖의 이름은 적재 오류(줄 · 열)."""
        h = "spec t/1\nin a : State @observed once\nlet n = 2\nout o/1\n  x : int\n--- s\n"
        ok = P.load(h + "{{ a }} {{ a.b }} {{ n }} {{ out }} {{ out_schema }} {% for t in a %}{{ t.key }}{% end %}"
                        "{% if a %}y{% end %}\n")
        self.assertIn("s", ok.sections)
        self.assertEqual(self.where(h + "x\nab {{ tsk }}\n")[:2], (8, 4))
        self.assertEqual(self.where(h + "{% if tsk %}y{% end %}\n")[:2], (7, 1))
        self.assertEqual(self.where(h + "{% for t in tsk %}y{% end %}\n")[:2], (7, 1))
        self.assertEqual(self.where(h + "{% for t in a %}y{% end %}{{ t }}\n")[:2], (7, 27))       # 반복 밖의 반복 변수
        self.assertEqual(self.where(h + "{% for t in a %}y{% else %}{{ t }}{% end %}\n")[:2], (7, 28))   # else 에는 없다

    def test_names_types_point_at(self):
        with self.assertRaises(P.SpecError):
            P.load("spec t/1\nout o/1\n  xs : [int] <= nope\n--- s\nx")
        with self.assertRaises(P.SpecError):
            P.load("spec t/1\nout o/1\n  k : key(nope)\n--- s\nx")
        with self.assertRaises(P.SpecError):
            P.load("spec t/1\nin a : State @observed once\nin a : State @observed once\n")


class Report(unittest.TestCase):
    @unittest.skipUnless(TELEMETRY, "Telemetry 가 없다")
    def test_provider_usage_next_to_the_estimate(self):
        texts = P.conversation(SPEC, vals(task=GA["task"]), turns()[:1], "compact")
        usage = [{"prompt_token_count": 150, "cached_content_token_count": 0, "candidates_token_count": 40},
                 {"prompt_token_count": 60, "cached_content_token_count": 10, "candidates_token_count": 20}]
        r = P.token_report(texts, usage, "gemini")
        self.assertEqual([t["estimate"] for t in r["turns"]], [P.tokens(t) for t in texts])
        self.assertEqual([t["provider"]["input_tokens"] for t in r["turns"]], [150, 50])
        self.assertEqual([t["provider"]["prompt_tokens"] for t in r["turns"]], [150, 60])
        self.assertEqual(r["provider_prompt_tokens"], 210)
        self.assertEqual(r["usage_format"], "gemini")
        self.assertIsNone(P.token_report(texts)["provider_prompt_tokens"])
        with self.assertRaises(ValueError):
            P.token_report(texts, usage[:1], "gemini")

    def test_tokens_is_bytes_over_four_rounded_up(self):
        self.assertEqual([P.tokens(x) for x in ("", "a", "abcd", "abcde", "é")], [0, 1, 1, 2, 1])


class Versions(unittest.TestCase):
    def test_language_and_spec_version(self):
        self.assertEqual((SPEC.language, SPEC.name, SPEC.version), ("prompt-spec/1", "gemini-plan/1", 1))
        self.assertEqual(len(SPEC.digest), 64)
        self.assertNotEqual(P.load(SPEC_TEXT + "\n").digest, SPEC.digest)


class Performance(unittest.TestCase):
    def test_per_turn_compile_is_cheap(self):
        v = vals(task=GA["task"], results=GA["results"], ask=GA["ask"])
        t0 = time.perf_counter()
        for _ in range(200):
            P.compile(SPEC, "turn_noresume", v, "compact")
            P.check(SPEC, GA["plans"][0]["plan"], {"tools": TOOLS})
        self.assertLess((time.perf_counter() - t0) / 200, 0.01)            # 넉넉한 한도 -- 실제는 0.1 ms 안팎


if __name__ == "__main__":
    unittest.main()
