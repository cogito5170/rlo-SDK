"""Agent SDK(Python) 에 rlo 훅을 붙이는 예 -- 공식 문서(code.claude.com/docs/en/agent-sdk/hooks · agent-sdk/python)로 확인한 꼴만 쓴다.

    pip install "rlo-sdk[sensor] @ git+https://github.com/cogito5170/rlo-SDK@<sha>" claude-agent-sdk
    python examples/agent_sdk.py /path/to/cc_tools_model.json "할 일"

- 콜백 꼴: `async (input_data, tool_use_id, context) -> dict`. `{}` 는 바꾸지 않고 허락이다. 막기는 hookSpecificOutput 의 deny.
- Python SDK 의 `HookEvent` 에는 SessionEnd 가 없다 -- SessionEnd 에서도 거두려면 `.claude/settings.json` 의 명령 훅
  (examples/claude_code_settings.json)을 쓰고 `setting_sources=["project"]` 로 읽힌다.
- PreToolUse 콜백이 시간 초과면 그 도구는 실행되지 않는다(기본 600 초).
- 기본 모드는 shadow(기록만). enforce 로 바꾸기 전에 README 의 "알려진 한계" 를 읽는다.
"""
from __future__ import annotations

import asyncio
import json
import sys


def build_options(model_path: str, mode: str = "shadow", grants=("Bash",), record=None):
    """rlo 훅을 단 ClaudeAgentOptions. 어댑터 하나를 네 사건에 같이 건다."""
    from claude_agent_sdk import ClaudeAgentOptions, HookMatcher

    from action.spec import ActionModel
    from rlo.hooks import guard_hooks

    with open(model_path, encoding="utf-8") as f:
        model = ActionModel.from_dict(json.load(f))
    adapter = guard_hooks(model, mode=mode, grants=grants, record=record)
    hooks = {event: [HookMatcher(hooks=[adapter.callback])]
             for event in ("PreToolUse", "PostToolUse", "PostToolUseFailure", "Stop")}
    return ClaudeAgentOptions(hooks=hooks), adapter


async def main(model_path: str, prompt: str):
    from claude_agent_sdk import ClaudeSDKClient

    def record(kind, d):
        print(f"[rlo] {kind}: {json.dumps(d, ensure_ascii=False)[:300]}", file=sys.stderr)

    options, _ = build_options(model_path, record=record)
    async with ClaudeSDKClient(options=options) as client:
        await client.query(prompt)
        async for message in client.receive_response():
            print(message)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2]))
