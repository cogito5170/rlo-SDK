"""나란히 부르기 세기 도구(K7-Proposal-3) -- Claude Code transcript(JSONL)에서 한 응답 안의 tool_use 수를 센다.

사용:
    python -m rlo.parallel_calls <transcript.jsonl> [<transcript.jsonl> ...]

출력: JSON 형식
    - 각 응답별: message_id, tool_count, tools (도구 이름 목록)
    - 요약: total_responses, parallel_responses (2개 이상의 tool_use), max_tool_count
"""
from __future__ import annotations

import argparse
import json
import sys


def count_parallel_calls(transcript_path: str) -> dict:
    """JSONL transcript 에서 나란히 부른 호출을 센다.

    Returns:
        {
            "responses": [
                {"message_id": "m1", "tool_count": 2, "tools": ["Read", "Bash"]},
                ...
            ],
            "summary": {
                "total_responses": N,
                "parallel_responses": M,  # 2개 이상의 tool_use
                "max_tool_count": K
            }
        }
    """
    responses_by_id = {}

    try:
        with open(transcript_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                # assistant 타입의 메시지만 처리
                if entry.get("type") != "assistant":
                    continue

                message = entry.get("message", {})
                message_id = message.get("id")
                if not message_id:
                    continue

                content = message.get("content", [])
                if not isinstance(content, list):
                    continue

                # 이 message_id가 처음 나타나면 초기화
                if message_id not in responses_by_id:
                    responses_by_id[message_id] = {"tools": []}

                # tool_use 블록 수세기
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        tool_name = block.get("name")
                        if tool_name:
                            responses_by_id[message_id]["tools"].append(tool_name)

        # tool_count 계산
        for tool_list in responses_by_id.values():
            tool_list["tool_count"] = len(tool_list["tools"])

        # 응답을 message_id 순서대로 정렬
        responses = [
            {
                "message_id": msg_id,
                "tool_count": data["tool_count"],
                "tools": data["tools"]
            }
            for msg_id, data in sorted(responses_by_id.items())
        ]

        # 요약 통계
        total = len(responses)
        parallel = sum(1 for r in responses if r["tool_count"] >= 2)
        max_count = max((r["tool_count"] for r in responses), default=0)

        return {
            "responses": responses,
            "summary": {
                "total_responses": total,
                "parallel_responses": parallel,
                "max_tool_count": max_count
            }
        }

    except FileNotFoundError:
        print(f"Error: {transcript_path} not found", file=sys.stderr)
        raise


def main(argv=None) -> int:
    """Command line entry point."""
    argv = argv or sys.argv[1:]

    ap = argparse.ArgumentParser(
        description="Count parallel tool calls in Claude Code transcripts"
    )
    ap.add_argument(
        "transcripts",
        nargs="+",
        help="JSONL transcript file path(s)"
    )

    args = ap.parse_args(argv)

    all_results = {}
    for transcript in args.transcripts:
        try:
            result = count_parallel_calls(transcript)
            all_results[transcript] = result
        except Exception as e:
            print(f"Error processing {transcript}: {e}", file=sys.stderr)
            return 1

    # JSON 출력
    json.dump(all_results, sys.stdout, ensure_ascii=False, indent=2)
    print()  # 줄바꿈

    return 0


if __name__ == "__main__":
    sys.exit(main())
