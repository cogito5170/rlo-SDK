"""suggest-model 명령 -- 기록(JSONL)에서 모형(action-model/1)에 없는 도구·칸을 찾아 action-spec 초안을 낸다.

사용:
    python -m rlo.suggest_model --model <action-model/1 JSON> --record <JSONL>

출력: 모형에 없는 도구마다 초안 spec 하나, 모형에 있는 도구의 새 칸 목록을 JSON 으로 낸다.
  - 칸 타입은 추측이다 -- 기록에는 값이 없으므로("tool_input_keys" 는 이름만)
  - 위험 등급(risk)은 자동으로 정하지 않는다 -- 사람이 검토해 채운다
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from typing import Any


@dataclasses.dataclass
class FieldDraft:
    """필드 초안 -- 기록에서 본 이름만 있고 타입은 추측."""
    name: str
    type: str = "string"  # 기본 추측 -- 모든 미알려 필드는 string
    required: bool = True

    def to_dict(self) -> dict:
        d = {"type": self.type}
        if not self.required:
            d["required"] = False
        return d


def infer_field_type(name: str) -> str:
    """필드 이름에서 타입을 추측한다.

    휴리스틱:
    - *_ms, *_timeout, duration*, *timeout -> number
    - *_flag, *_enabled, is_* -> bool
    - 그 외 -> string
    """
    name_lower = name.lower()
    # number 패턴: _ms, timeout, duration
    if any(x in name_lower for x in ("_ms", "timeout", "duration")):
        return "number"
    # bool 패턴: _flag, _enabled, is_
    if any(x in name_lower for x in ("_flag", "_enabled")) or name_lower.startswith("is_"):
        return "bool"
    return "string"


def collect_tools_and_fields(record_path: str) -> dict[str, set[str]]:
    """JSONL 기록에서 각 도구마다 나타난 칸 이름들을 수집한다.

    Returns: {tool_name: {field_name, ...}, ...}
    """
    tools = {}
    with open(record_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            # 예상하는 필드: kind="guard", tool_name, tool_input_keys
            if record.get("kind") != "guard":
                continue
            tool_name = record.get("tool_name")
            if not tool_name:
                continue
            fields = set(record.get("tool_input_keys") or [])
            if tool_name not in tools:
                tools[tool_name] = set()
            tools[tool_name].update(fields)
    return tools


def load_model(model_path: str) -> dict:
    """action-model/1 JSON 을 읽는다."""
    with open(model_path, encoding="utf-8") as f:
        return json.load(f)


def get_model_tools(model: dict) -> dict[str, dict[str, Any]]:
    """모형에 있는 도구들을 {tool_name: {field_name: field_spec, ...}, ...} 로 낸다."""
    tools = {}
    for spec in model.get("specs") or []:
        name = spec.get("name")
        if name:
            params = spec.get("params") or {}
            tools[name] = params
    return tools


def suggest_new_specs(record_tools: dict[str, set[str]], model_tools: dict[str, dict]) -> list[dict]:
    """모형에 없는 도구의 초안 spec 들을 낸다."""
    new_specs = []
    for tool_name in sorted(record_tools.keys()):
        if tool_name not in model_tools:
            fields = record_tools[tool_name]
            params = {}
            for field_name in sorted(fields):
                field_type = infer_field_type(field_name)
                params[field_name] = {"type": field_type}

            spec = {
                "schema": "action-spec/1",
                "name": tool_name,
                "version": "1",
                "target_model": None,
                "params": params,
                "preconditions": [],
                "risk": None,  # 사람이 채운다
                "postcondition": [],
                "window_ms": None,
                "description": f"[DRAFT] {tool_name} -- 기록에서 찾은 도구(칸 타입은 추측)"
            }
            new_specs.append(spec)
    return new_specs


def suggest_new_fields(record_tools: dict[str, set[str]], model_tools: dict[str, dict]) -> dict[str, list[dict]]:
    """모형의 각 도구마다 새로 나타난 칸들을 낸다.

    Returns: {tool_name: [{name, type, required}, ...], ...}
    """
    new_fields_by_tool = {}
    for tool_name in sorted(record_tools.keys()):
        if tool_name in model_tools:
            record_fields = record_tools[tool_name]
            model_fields = set(model_tools[tool_name].keys())
            new_fields = record_fields - model_fields
            if new_fields:
                new_fields_by_tool[tool_name] = [
                    {
                        "name": field_name,
                        "type": infer_field_type(field_name),
                        "required": True,  # 기본값 -- 사람이 검토
                        "note": "[DRAFT] 기록에서 찾은 새 칸"
                    }
                    for field_name in sorted(new_fields)
                ]
    return new_fields_by_tool


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    ap = argparse.ArgumentParser(
        prog="python -m rlo.suggest_model",
        description="기록(JSONL)에서 모형(action-model/1)에 없는 도구·칸을 찾아 action-spec 초안을 낸다"
    )
    ap.add_argument("--model", required=True, help="에이전트 도구의 ActionModel(action-model/1) JSON 경로")
    ap.add_argument("--record", required=True, help="기록 JSONL 경로(훅 기록)")
    args = ap.parse_args(argv)

    # 기록과 모형을 읽는다
    record_tools = collect_tools_and_fields(args.record)
    model = load_model(args.model)
    model_tools = get_model_tools(model)

    # 초안을 낸다
    new_specs = suggest_new_specs(record_tools, model_tools)
    new_fields = suggest_new_fields(record_tools, model_tools)

    # JSON 으로 출력한다
    output = {
        "schema": "suggest-model/1",
        "timestamp": "",
        "new_specs": new_specs,
        "new_fields": new_fields
    }

    json.dump(output, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
