"""기본 제공 usage 꼴 · 토크나이저(CMD-K18 S3) -- 진입점 rlo.usage · rlo.tokenizers 로 실린다(rlo.plugins).

usage 꼴은 Telemetry `l0_usage`(anthropic · openai · gemini · otel)를 감싸 {input, cache_read, cache_creation, output, context}
로 맞춘다. context = 보낸 쪽 토큰 전체(캐시 포함). 모르는 칸은 None 이고, context 를 셀 수 없으면 None 이다(0 으로 메우지 않는다).
정의로 0 인 것만 0 으로 둔다: OpenAI · Gemini 에는 '캐시 쓰기' 토큰 갈래가 없고(cache_creation 0), Gemini 는 0 인 칸을 빼고
보낸다(proto3 -- cachedContentTokenCount 가 없으면 0).
"""
from __future__ import annotations

import math


def _int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _l0(fmt: str, raw: dict) -> dict:
    from telemetry.usage import l0_usage
    vals, _ = l0_usage(fmt, raw)
    return vals


class _Format:
    version = "1"
    api = 1

    def __init__(self, name):
        self.name = name

    def _out(self, vals: dict, context=None) -> dict:
        out = {"input": vals.get("input_tokens"), "cache_read": vals.get("cache_read_input_tokens"),
               "cache_creation": vals.get("cache_creation_input_tokens"), "output": vals.get("output_tokens")}
        out = {k: (v if _int(v) else None) for k, v in out.items()}
        parts = (out["input"], out["cache_read"], out["cache_creation"])
        out["context"] = context if _int(context) else (sum(parts) if all(p is not None for p in parts) else None)
        return out


class _Anthropic(_Format):
    def normalize(self, raw):
        if not isinstance(raw, dict):
            return None
        return self._out(_l0("anthropic", raw))


class _OpenAI(_Format):
    def normalize(self, raw):
        if not isinstance(raw, dict):
            return None
        vals = _l0("openai", raw)
        pt = raw.get("prompt_tokens", raw.get("input_tokens"))
        det = raw.get("prompt_tokens_details") or raw.get("input_tokens_details") or {}
        if _int(pt) and det.get("cached_tokens") is None:      # 캐시 안 씀: 캐시 읽기 0, 입력 전체
            vals.setdefault("input_tokens", pt)
            vals.setdefault("cache_read_input_tokens", 0)
        vals.setdefault("cache_creation_input_tokens", 0)       # OpenAI 에는 캐시 쓰기 갈래가 없다
        return self._out(vals)


class _Gemini(_Format):
    def normalize(self, raw):
        if not isinstance(raw, dict):
            return None
        g = dict(raw)
        if _int(g.get("prompt_token_count")) and g.get("cached_content_token_count") is None:
            g["cached_content_token_count"] = 0                  # Gemini 는 0 인 칸을 빼고 보낸다
        vals = _l0("gemini", g)
        if "input_tokens" in vals:
            vals.setdefault("cache_creation_input_tokens", 0)   # Gemini 에는 캐시 쓰기 갈래가 없다
        return self._out(vals)


class _OTel(_Format):
    def normalize(self, raw):
        if not isinstance(raw, dict):
            return None
        vals = _l0("otel", raw)
        total = vals.get("total_input_tokens")
        out = self._out(vals, context=total)
        if _int(total) and out["cache_read"] is not None:
            out["input"] = total - out["cache_read"]
        return out


ANTHROPIC, OPENAI, GEMINI, OTEL = _Anthropic("anthropic"), _OpenAI("openai"), _Gemini("gemini"), _OTel("otel")


class _Bytes4:
    """기본 토크나이저: ceil(utf-8 바이트 / 4) -- PROMPT_SPEC §4 의 바닥을 재는 수(바꾸지 않는다)."""
    name, version, api = "bytes4", "1", 1

    def count(self, text: str) -> int:
        return math.ceil(len(text.encode("utf-8")) / 4)


BYTES4 = _Bytes4()
