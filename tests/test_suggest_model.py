"""suggest_model 테스트 -- 기록(JSONL)에서 모형에 없는 도구·칸을 찾아 초안을 낸다.

CMD-WA1 에서 요구한 기능:
  - 모형에 없는 도구마다 초안 spec 하나
  - 모형에 있는 도구의 새 칸 목록
  - 칸 타입은 추측 (기록에는 값이 없으므로)
  - 위험 등급(risk)은 자동으로 정하지 않음
"""
import io
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

import rlo
from rlo.suggest_model import (
    collect_tools_and_fields,
    get_model_tools,
    infer_field_type,
    load_model,
    suggest_new_fields,
    suggest_new_specs,
    main,
)
from rlo.example_hooks import data


class InferFieldType(unittest.TestCase):
    """필드 이름에서 타입을 추측한다."""

    def test_number_fields(self):
        """_ms, timeout, duration 은 number."""
        self.assertEqual(infer_field_type("duration_ms"), "number")
        self.assertEqual(infer_field_type("timeout"), "number")
        self.assertEqual(infer_field_type("request_timeout"), "number")
        self.assertEqual(infer_field_type("duration"), "number")

    def test_bool_fields(self):
        """_flag, _enabled, is_* 은 bool."""
        self.assertEqual(infer_field_type("is_enabled"), "bool")
        self.assertEqual(infer_field_type("verbose_flag"), "bool")
        self.assertEqual(infer_field_type("async_enabled"), "bool")

    def test_string_fields(self):
        """그 외는 string."""
        self.assertEqual(infer_field_type("command"), "string")
        self.assertEqual(infer_field_type("file_path"), "string")
        self.assertEqual(infer_field_type("pattern"), "string")


class CollectToolsAndFields(unittest.TestCase):
    """JSONL 기록에서 도구와 칸을 수집한다."""

    def test_collect_from_hook_record(self):
        """훅 기록 파일에서 도구들을 수집한다."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False, encoding="utf-8") as f:
            f.write(json.dumps({
                "kind": "guard",
                "tool_name": "Bash",
                "tool_input_keys": ["command"]
            }) + "\n")
            f.flush()

            tools = collect_tools_and_fields(f.name)
            # 기록에는 Bash 도구가 있다
            self.assertIn("Bash", tools)
            self.assertIn("command", tools["Bash"])

    def test_only_guard_records(self):
        """kind=guard 인 기록만 센다(guard_error 등은 제외)."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False, encoding="utf-8") as f:
            # guard 기록
            f.write(json.dumps({
                "kind": "guard",
                "tool_name": "Tool1",
                "tool_input_keys": ["field1", "field2"]
            }) + "\n")
            # guard_error 는 제외
            f.write(json.dumps({
                "kind": "guard_error",
                "tool_name": "Tool2",
                "exception": "SomeError"
            }) + "\n")
            # 유효한 guard 기록
            f.write(json.dumps({
                "kind": "guard",
                "tool_name": "Tool3",
                "tool_input_keys": ["field3"]
            }) + "\n")
            f.flush()

            tools = collect_tools_and_fields(f.name)
            self.assertIn("Tool1", tools)
            self.assertNotIn("Tool2", tools)
            self.assertIn("Tool3", tools)
            self.assertEqual(tools["Tool1"], {"field1", "field2"})
            self.assertEqual(tools["Tool3"], {"field3"})

    def test_empty_record(self):
        """빈 기록 파일은 빈 도구 dict 를 낸다."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False, encoding="utf-8") as f:
            f.write("")
            f.flush()

            tools = collect_tools_and_fields(f.name)
            self.assertEqual(tools, {})

    def test_accumulate_fields(self):
        """같은 도구가 여러 번 나타나면 칸들을 누적한다."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False, encoding="utf-8") as f:
            f.write(json.dumps({
                "kind": "guard",
                "tool_name": "Tool1",
                "tool_input_keys": ["field1"]
            }) + "\n")
            f.write(json.dumps({
                "kind": "guard",
                "tool_name": "Tool1",
                "tool_input_keys": ["field2"]
            }) + "\n")
            f.flush()

            tools = collect_tools_and_fields(f.name)
            self.assertEqual(tools["Tool1"], {"field1", "field2"})


class LoadModel(unittest.TestCase):
    """action-model/1 JSON 을 읽는다."""

    def test_load_cc_tools_model(self):
        """cc_tools_model.json 을 읽을 수 있다."""
        model_path = str(data("cc_tools_model.json"))
        model = load_model(model_path)
        self.assertEqual(model["schema"], "action-model/1")
        self.assertIn("specs", model)
        # cc_tools_model 에는 Bash, Edit, Grep, Read, Write 가 있다
        specs = {s["name"] for s in model["specs"]}
        self.assertIn("Bash", specs)


class GetModelTools(unittest.TestCase):
    """모형에서 도구 목록을 추출한다."""

    def test_extract_tools_from_model(self):
        """모형의 도구들을 {tool_name: {field_name: spec, ...}, ...} 로 낸다."""
        model_path = str(data("cc_tools_model.json"))
        model = load_model(model_path)
        model_tools = get_model_tools(model)

        self.assertIn("Bash", model_tools)
        self.assertIn("command", model_tools["Bash"])
        self.assertIn("Read", model_tools)
        self.assertIn("file_path", model_tools["Read"])


class SuggestNewSpecs(unittest.TestCase):
    """모형에 없는 도구의 초안 spec 들을 낸다."""

    def test_new_tool_draft(self):
        """모형에 없는 도구의 초안을 낸다."""
        record_tools = {
            "NewTool": {"field1", "field2"},
            "Bash": {"command"}
        }
        model_tools = {"Bash": {"command": {}}}

        new_specs = suggest_new_specs(record_tools, model_tools)

        self.assertEqual(len(new_specs), 1)
        spec = new_specs[0]
        self.assertEqual(spec["name"], "NewTool")
        self.assertEqual(spec["schema"], "action-spec/1")
        self.assertIsNone(spec["risk"])  # 위험 등급은 미정
        self.assertIn("field1", spec["params"])
        self.assertIn("field2", spec["params"])
        # 순서는 정렬되어 있어야 한다
        self.assertEqual(list(spec["params"].keys()), ["field1", "field2"])

    def test_no_new_tools(self):
        """모든 도구가 모형에 있으면 빈 리스트를 낸다."""
        record_tools = {"Bash": {"command"}}
        model_tools = {"Bash": {"command": {}}}

        new_specs = suggest_new_specs(record_tools, model_tools)
        self.assertEqual(new_specs, [])


class SuggestNewFields(unittest.TestCase):
    """모형의 각 도구마다 새로 나타난 칸들을 낸다."""

    def test_new_field_in_existing_tool(self):
        """모형에 있는 도구에 새 칸이 있으면 낸다."""
        record_tools = {
            "Bash": {"command", "new_field", "another_new"}
        }
        model_tools = {
            "Bash": {"command": {"type": "string"}}
        }

        new_fields = suggest_new_fields(record_tools, model_tools)

        self.assertIn("Bash", new_fields)
        fields = new_fields["Bash"]
        self.assertEqual(len(fields), 2)
        # 정렬되어야 한다
        names = [f["name"] for f in fields]
        self.assertEqual(names, ["another_new", "new_field"])

        # 각 필드가 위험 등급 미정 표기를 가져야 한다
        for field in fields:
            self.assertIn("note", field)
            self.assertIn("DRAFT", field["note"])
            # 이전 호출에 없던 칸 -- 선택으로 초안한다(CMD-K9)
            self.assertIs(field["required"], False)

    def test_no_new_fields(self):
        """새 칸이 없으면 그 도구는 목록에 없다."""
        record_tools = {
            "Bash": {"command"}
        }
        model_tools = {
            "Bash": {"command": {"type": "string"}}
        }

        new_fields = suggest_new_fields(record_tools, model_tools)
        self.assertNotIn("Bash", new_fields)


class SuggestModelCommand(unittest.TestCase):
    """suggest_model 명령을 테스트한다."""

    def test_command_line_interface(self):
        """명령줄 인터페이스가 작동한다."""
        # 테스트 기록과 모형을 준비한다
        model_path = str(data("cc_tools_model.json"))

        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False, encoding="utf-8") as f:
            # Bash 는 모형에 있지만 새 필드가 있다
            f.write(json.dumps({
                "kind": "guard",
                "tool_name": "Bash",
                "tool_input_keys": ["command", "new_bash_field"]
            }) + "\n")
            # NewTool 은 모형에 없다
            f.write(json.dumps({
                "kind": "guard",
                "tool_name": "NewTool",
                "tool_input_keys": ["field1"]
            }) + "\n")
            f.flush()

            # 명령을 실행한다
            argv = ["--model", model_path, "--record", f.name]
            output = io.StringIO()
            old_stdout = sys.stdout
            try:
                sys.stdout = output
                ret = main(argv)
            finally:
                sys.stdout = old_stdout

            self.assertEqual(ret, 0)
            result = json.loads(output.getvalue())
            self.assertEqual(result["schema"], "suggest-model/1")
            self.assertIn("new_specs", result)
            self.assertIn("new_fields", result)

            # NewTool 의 초안이 있어야 한다
            self.assertTrue(any(s["name"] == "NewTool" for s in result["new_specs"]))

            # Bash 의 새 칸이 있어야 한다 -- 기록에서 잰 칸만, 선택 · 초안 표시로(CMD-K8 · K9)
            self.assertEqual(result["new_fields"]["Bash"],
                             [{"name": "new_bash_field", "type": "string", "required": False,
                               "note": "[DRAFT] 기록에서 찾은 새 칸"}])

    def test_subprocess_invocation(self):
        """python -m rlo.suggest_model 로 실행할 수 있다."""
        model_path = str(data("cc_tools_model.json"))

        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False, encoding="utf-8") as f:
            f.write(json.dumps({
                "kind": "guard",
                "tool_name": "Bash",
                "tool_input_keys": ["command"]
            }) + "\n")
            f.flush()

            # 서브프로세스로 실행한다
            p = subprocess.run(
                [sys.executable, "-m", "rlo.suggest_model", "--model", model_path, "--record", f.name],
                capture_output=True,
                text=True,
                cwd=tempfile.gettempdir(),  # 소스 트리가 아닌 곳에서, 이 시험이 import 한 그 rlo(설치본 · 소스)를 부른다
                env=dict(os.environ, PYTHONPATH=os.pathsep.join(
                    [str(pathlib.Path(rlo.__file__).resolve().parent.parent), os.environ.get("PYTHONPATH", "")])),
            )

            self.assertEqual(p.returncode, 0, f"stderr: {p.stderr}")
            result = json.loads(p.stdout)
            self.assertEqual(result["schema"], "suggest-model/1")


class EdgeCases(unittest.TestCase):
    """경계 케이스들을 테스트한다."""

    def test_tool_with_no_fields(self):
        """칸이 없는 도구는 어떻게 처리하나."""
        record_tools = {
            "EmptyTool": set()
        }
        model_tools = {}

        new_specs = suggest_new_specs(record_tools, model_tools)
        self.assertEqual(len(new_specs), 1)
        self.assertEqual(new_specs[0]["params"], {})

    def test_field_name_that_looks_like_number(self):
        """duration_ms 는 number 로 추측된다."""
        tools = {"Tool": {"duration_ms"}}
        model_tools = {}
        specs = suggest_new_specs(tools, model_tools)
        self.assertEqual(specs[0]["params"]["duration_ms"]["type"], "number")

    def test_field_name_that_looks_like_bool(self):
        """is_enabled 는 bool 로 추측된다."""
        tools = {"Tool": {"is_enabled"}}
        model_tools = {}
        specs = suggest_new_specs(tools, model_tools)
        self.assertEqual(specs[0]["params"]["is_enabled"]["type"], "bool")


if __name__ == "__main__":
    unittest.main()
