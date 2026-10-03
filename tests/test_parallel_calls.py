"""나란히 부르기 세기 도구 시험(K7-Proposal-3) -- transcript 에서 한 응답의 tool_use 수를 센다."""
import json
import tempfile
import unittest

from rlo import parallel_calls
from rlo.example_hooks import data


class CountParallelCalls(unittest.TestCase):
    """parallel_calls 함수 시험."""

    def test_single_tool_use(self):
        """한 응답에 한 도구만 쓰이는 경우."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False) as f:
            # 한 개의 tool_use
            entry = {
                "type": "assistant",
                "message": {
                    "id": "m1",
                    "content": [
                        {"type": "tool_use", "name": "Bash"}
                    ]
                }
            }
            f.write(json.dumps(entry) + "\n")
            f.flush()

            result = parallel_calls.count_parallel_calls(f.name)

            self.assertEqual(len(result["responses"]), 1)
            self.assertEqual(result["responses"][0]["message_id"], "m1")
            self.assertEqual(result["responses"][0]["tool_count"], 1)
            self.assertEqual(result["responses"][0]["tools"], ["Bash"])
            self.assertEqual(result["summary"]["total_responses"], 1)
            self.assertEqual(result["summary"]["parallel_responses"], 0)
            self.assertEqual(result["summary"]["max_tool_count"], 1)

    def test_parallel_tool_use(self):
        """한 응답에 두 개의 도구가 나란히 쓰이는 경우."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False) as f:
            # 두 개의 tool_use
            entry = {
                "type": "assistant",
                "message": {
                    "id": "m1",
                    "content": [
                        {"type": "tool_use", "name": "Read"},
                        {"type": "tool_use", "name": "Bash"}
                    ]
                }
            }
            f.write(json.dumps(entry) + "\n")
            f.flush()

            result = parallel_calls.count_parallel_calls(f.name)

            self.assertEqual(len(result["responses"]), 1)
            self.assertEqual(result["responses"][0]["message_id"], "m1")
            self.assertEqual(result["responses"][0]["tool_count"], 2)
            self.assertEqual(result["responses"][0]["tools"], ["Read", "Bash"])
            self.assertEqual(result["summary"]["total_responses"], 1)
            self.assertEqual(result["summary"]["parallel_responses"], 1)
            self.assertEqual(result["summary"]["max_tool_count"], 2)

    def test_multiple_responses(self):
        """여러 응답이 있는 경우."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False) as f:
            # 첫 응답: 1개 tool_use
            f.write(json.dumps({
                "type": "assistant",
                "message": {
                    "id": "m1",
                    "content": [
                        {"type": "tool_use", "name": "Bash"}
                    ]
                }
            }) + "\n")

            # 두 번째 응답: 2개 tool_use
            f.write(json.dumps({
                "type": "assistant",
                "message": {
                    "id": "m2",
                    "content": [
                        {"type": "tool_use", "name": "Read"},
                        {"type": "tool_use", "name": "Edit"}
                    ]
                }
            }) + "\n")

            # 세 번째 응답: 3개 tool_use
            f.write(json.dumps({
                "type": "assistant",
                "message": {
                    "id": "m3",
                    "content": [
                        {"type": "tool_use", "name": "Bash"},
                        {"type": "tool_use", "name": "Read"},
                        {"type": "tool_use", "name": "Write"}
                    ]
                }
            }) + "\n")
            f.flush()

            result = parallel_calls.count_parallel_calls(f.name)

            self.assertEqual(len(result["responses"]), 3)
            self.assertEqual(result["responses"][0]["tool_count"], 1)
            self.assertEqual(result["responses"][1]["tool_count"], 2)
            self.assertEqual(result["responses"][2]["tool_count"], 3)
            self.assertEqual(result["summary"]["total_responses"], 3)
            self.assertEqual(result["summary"]["parallel_responses"], 2)  # m2, m3
            self.assertEqual(result["summary"]["max_tool_count"], 3)

    def test_ignores_non_assistant_messages(self):
        """assistant 가 아닌 메시지는 무시한다."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False) as f:
            # user 메시지 (무시됨)
            f.write(json.dumps({
                "type": "user",
                "message": {"role": "user", "content": "go"}
            }) + "\n")

            # assistant 메시지
            f.write(json.dumps({
                "type": "assistant",
                "message": {
                    "id": "m1",
                    "content": [
                        {"type": "tool_use", "name": "Bash"}
                    ]
                }
            }) + "\n")
            f.flush()

            result = parallel_calls.count_parallel_calls(f.name)

            self.assertEqual(len(result["responses"]), 1)

    def test_ignores_non_tool_use_blocks(self):
        """tool_use 가 아닌 블록은 무시한다."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False) as f:
            f.write(json.dumps({
                "type": "assistant",
                "message": {
                    "id": "m1",
                    "content": [
                        {"type": "text", "text": "Hello"},
                        {"type": "tool_use", "name": "Bash"},
                        {"type": "text", "text": "Done"}
                    ]
                }
            }) + "\n")
            f.flush()

            result = parallel_calls.count_parallel_calls(f.name)

            self.assertEqual(len(result["responses"]), 1)
            self.assertEqual(result["responses"][0]["tool_count"], 1)
            self.assertEqual(result["responses"][0]["tools"], ["Bash"])

    def test_empty_content(self):
        """content 가 비어있는 경우."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False) as f:
            f.write(json.dumps({
                "type": "assistant",
                "message": {
                    "id": "m1",
                    "content": []
                }
            }) + "\n")
            f.flush()

            result = parallel_calls.count_parallel_calls(f.name)

            self.assertEqual(len(result["responses"]), 1)
            self.assertEqual(result["responses"][0]["tool_count"], 0)

    def test_missing_message_id(self):
        """message.id 가 없는 경우는 무시한다."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False) as f:
            f.write(json.dumps({
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "tool_use", "name": "Bash"}
                    ]
                }
            }) + "\n")
            f.flush()

            result = parallel_calls.count_parallel_calls(f.name)

            self.assertEqual(len(result["responses"]), 0)

    def test_empty_file(self):
        """빈 파일."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False) as f:
            f.flush()

            result = parallel_calls.count_parallel_calls(f.name)

            self.assertEqual(len(result["responses"]), 0)
            self.assertEqual(result["summary"]["total_responses"], 0)
            self.assertEqual(result["summary"]["parallel_responses"], 0)
            self.assertEqual(result["summary"]["max_tool_count"], 0)

    def test_file_not_found(self):
        """파일이 없는 경우."""
        with self.assertRaises(FileNotFoundError):
            parallel_calls.count_parallel_calls("/nonexistent/path.jsonl")


class MainCommand(unittest.TestCase):
    """main() 함수 시험."""

    def test_main_with_single_file(self):
        """단일 파일로 main 호출."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False) as f:
            f.write(json.dumps({
                "type": "assistant",
                "message": {
                    "id": "m1",
                    "content": [
                        {"type": "tool_use", "name": "Bash"},
                        {"type": "tool_use", "name": "Read"}
                    ]
                }
            }) + "\n")
            f.flush()

            import io
            import sys
            old_stdout = sys.stdout
            try:
                sys.stdout = captured_output = io.StringIO()
                result_code = parallel_calls.main([f.name])
                output = captured_output.getvalue()
            finally:
                sys.stdout = old_stdout

            self.assertEqual(result_code, 0)
            output_json = json.loads(output)
            self.assertIn(f.name, output_json)
            self.assertEqual(output_json[f.name]["summary"]["parallel_responses"], 1)


class IntegrationWithTestData(unittest.TestCase):
    """실제 테스트 데이터로 시험."""

    def test_parallel_jsonl(self):
        """parallel.jsonl 은 한 응답에 2개 tool_use 를 가져야 한다."""
        test_data_path = data("transcripts/parallel.jsonl")          # 패키지 자원 -- 설치본에서도 건너뛰지 않는다(CMD-K9)

        result = parallel_calls.count_parallel_calls(str(test_data_path))

        # parallel.jsonl 은 한 응답에 2개의 tool_use 를 가진다
        self.assertEqual(result["summary"]["total_responses"], 1)
        self.assertEqual(result["summary"]["parallel_responses"], 1)
        self.assertEqual(result["summary"]["max_tool_count"], 2)
        self.assertEqual(result["responses"][0]["tool_count"], 2)

    def test_normal_jsonl(self):
        """normal.jsonl 은 응답별로 각각 1개 tool_use 를 가져야 한다."""
        test_data_path = data("transcripts/normal.jsonl")          # 패키지 자원 -- 설치본에서도 건너뛰지 않는다(CMD-K9)

        result = parallel_calls.count_parallel_calls(str(test_data_path))

        # normal.jsonl 은 여러 응답이 있지만 각각 1개씩만 가진다
        self.assertGreater(result["summary"]["total_responses"], 0)
        self.assertEqual(result["summary"]["parallel_responses"], 0)
        self.assertEqual(result["summary"]["max_tool_count"], 1)


if __name__ == "__main__":
    unittest.main()
