"""기본 제공 transcript 읽개(CMD-K18 S2) -- 진입점 rlo.transcripts 로 실린다(rlo.plugins). 공급자 SDK 를 들이지 않는다.

    claude_code  Claude Code transcript(JSONL). 마지막 주 사슬(사이드체인 · `<synthetic>` 아님) assistant `message.usage`.
                 꼬리만 읽는다(K17). 꼴 anthropic.
    codex_cli    Codex CLI rollout(`~/.codex/sessions/…/rollout-*.jsonl`). 마지막 `event_msg` / `token_count` 의
                 `info.last_token_usage`(info 가 null 인 줄은 건너뛴다). Codex 의 input_tokens 는 캐시를 포함한다 -- OpenAI 꼴로
                 넘긴다. 꼴은 openai/codex 소스(afb436d, codex-rs/protocol TokenUsage · rollout tests)로 확인했다. 압축한 rollout 은 못 읽는다(None).
    gemini_cli   Gemini CLI 대화 기록(`~/.gemini/tmp/<project>/chats/session-*.jsonl`, 옛 `.json`). 메시지 줄 · `$set` ·
                 `$rewindTo` 를 앞에서부터 다시 짓고, 남은 마지막 `type: gemini` 메시지의 `tokens`(input = promptTokenCount,
                 캐시 포함). Gemini 꼴로 넘긴다. 꼴은 @google/gemini-cli-core 0.62.0 의 ChatRecordingService 로 확인했다.
                 되감기 때문에 파일 전체를 읽는다.

codex_cli · gemini_cli 는 실제 세션 기록으로 재지 못했다(이 환경에 그 CLI 가 없다) -- `experimental = True`, fixture 는
위 소스의 꼴로 지었다.
"""
from __future__ import annotations

import json
import os

TAIL_BYTES = 256 * 1024


def _tail_lines(path: str, tail_bytes: int):
    """파일 끝에서부터 줄(bytes)을 거꾸로 낸다. 창을 두 배씩 넓힌다 -- 부른 쪽이 찾으면 멈춘다. 끝까지 보면 None 을 낸다."""
    with open(path, "rb") as f:
        size = f.seek(0, os.SEEK_END)
        n, done = tail_bytes, 0
        while True:
            start = max(0, size - n)
            f.seek(start)
            lines = f.read(size - start).split(b"\n")
            if start > 0:
                lines = lines[1:]                          # 꼬리의 첫 줄은 잘렸을 수 있다
            new = lines[: max(0, len(lines) - done)]       # 앞 창에서 본 줄은 다시 보지 않는다
            for raw in reversed(new):
                if raw.strip():
                    yield raw
            done = len(lines)
            if start == 0:
                return
            n *= 2


class _Reader:
    version = "1"
    api = 1
    experimental = False


class _ClaudeCode(_Reader):
    name, usage_format = "claude_code", "anthropic"

    def last_usage(self, path, tail_bytes: int = TAIL_BYTES):
        try:
            for raw in _tail_lines(path, tail_bytes):
                try:
                    d = json.loads(raw)
                except ValueError:
                    continue
                if not isinstance(d, dict) or d.get("type") != "assistant" or d.get("isSidechain"):
                    continue
                m = d.get("message") or {}
                if not isinstance(m, dict) or m.get("model") == "<synthetic>":
                    continue
                u = m.get("usage")
                if isinstance(u, dict):
                    return u
        except OSError:
            return None
        return None


class _CodexCli(_Reader):
    name, usage_format, experimental = "codex_cli", "openai", True

    def last_usage(self, path, tail_bytes: int = TAIL_BYTES):
        try:
            for raw in _tail_lines(path, tail_bytes):
                try:
                    d = json.loads(raw)
                except ValueError:
                    continue
                p = d.get("payload") if isinstance(d, dict) and d.get("type") == "event_msg" else None
                if not isinstance(p, dict) or p.get("type") != "token_count" or not isinstance(p.get("info"), dict):
                    continue
                u = p["info"].get("last_token_usage")
                if not isinstance(u, dict):
                    continue
                return {"input_tokens": u.get("input_tokens"),
                        "input_tokens_details": {"cached_tokens": u.get("cached_input_tokens")},
                        "output_tokens": u.get("output_tokens"),
                        "output_tokens_details": {"reasoning_tokens": u.get("reasoning_output_tokens")}}
        except OSError:
            return None
        return None


class _GeminiCli(_Reader):
    name, usage_format, experimental = "gemini_cli", "gemini", True

    def last_usage(self, path):
        try:
            with open(path, encoding="utf-8") as f:
                text = f.read()
        except (OSError, UnicodeDecodeError):
            return None
        messages: dict = {}
        records = []
        stripped = text.strip()
        if stripped.startswith("{") and "\n" not in stripped:
            try:
                one = json.loads(stripped)
            except ValueError:
                one = None
            records = [one] if isinstance(one, dict) else []
        else:
            for line in text.splitlines():
                if not line.strip():
                    continue
                try:
                    records.append(json.loads(line))
                except ValueError:
                    continue
        for r in records:
            if not isinstance(r, dict):
                continue
            if isinstance(r.get("$rewindTo"), str):            # 그 메시지와 그 뒤를 지운다
                ids = list(messages)
                if r["$rewindTo"] in messages:
                    for i in ids[ids.index(r["$rewindTo"]):]:
                        del messages[i]
                else:
                    messages.clear()
            elif isinstance(r.get("$set"), dict) and isinstance(r["$set"].get("messages"), list):
                messages = {m["id"]: m for m in r["$set"]["messages"] if isinstance(m, dict) and "id" in m}
            elif isinstance(r.get("messages"), list):           # 메타데이터 줄 · 옛 .json 의 통째 기록
                for m in r["messages"]:
                    if isinstance(m, dict) and "id" in m:
                        messages[m["id"]] = m
            elif "id" in r and "type" in r:
                messages[r["id"]] = r                          # 같은 id 를 다시 쓰면 자리는 그대로, 내용만 바뀐다
        for m in reversed(list(messages.values())):
            t = m.get("tokens") if m.get("type") == "gemini" else None
            if isinstance(t, dict):
                return {"prompt_token_count": t.get("input"), "cached_content_token_count": t.get("cached"),
                        "candidates_token_count": t.get("output"), "thoughts_token_count": t.get("thoughts"),
                        "tool_use_prompt_token_count": t.get("tool")}
        return None


CLAUDE_CODE, CODEX_CLI, GEMINI_CLI = _ClaudeCode(), _CodexCli(), _GeminiCli()
