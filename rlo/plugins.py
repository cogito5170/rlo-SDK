"""rlo 플러그인(CMD-K18, BD-300) -- 런타임 · 공급자 · 토크나이저를 rlo 의 속을 고치지 않고 설치한 패키지로 꽂는다.

    [project.entry-points."rlo.transcripts"]   my_runtime = "my_pkg.reader:READER"
    [project.entry-points."rlo.usage"]         my_format  = "my_pkg.usage:FORMAT"
    [project.entry-points."rlo.tokenizers"]    my_tok     = "my_pkg.tok:TOKENIZER"

    from rlo import plugins
    plugins.get("rlo.transcripts", "codex_cli").last_usage(path)       # 없으면 KeyError(그리고 load_errors 를 본다)
    python -m rlo.plugins list                                          # 실린 것 · 실리지 못한 것

**플러그인 API 판 1**(`API_VERSION`): 진입점이 가리키는 객체(클래스면 인자 없이 만든다)는 `name`(진입점 이름과 같다) ·
`version`(글) · `api`(정수, 1)를 갖고, 무리마다 하나를 더 갖는다.

    rlo.transcripts  last_usage(path) -> dict | None   런타임 transcript 의 마지막 주 사슬 응답의 공급자 usage(날것).
                     usage_format: str                  그 usage 의 꼴(rlo.usage 의 이름). experimental: bool
    rlo.usage        normalize(raw) -> dict | None      {input, cache_read, cache_creation, output, context}. context =
                                                        컨텍스트(보낸 쪽 토큰 전체). 모르는 칸은 None -- 0 으로 메우지 않는다
    rlo.tokenizers   count(text) -> int                 글의 토큰 수

판이 다르거나 · 이름이 겹치거나 · 들이다 실패하면 그 플러그인만 `load_errors()` 에 적고 나머지는 싣는다. 기본 제공(claude_code ·
codex_cli · gemini_cli · anthropic · openai · gemini · otel · bytes4)도 같은 길(진입점)로 싣는다 -- 소스 트리에서 돌 때처럼
rlo-sdk 의 배포 정보가 없으면 같은 진입점을 rlo 가 스스로 만든다. 공급자 SDK 가 필요한 플러그인은 rlo 밖에 둔다(rlo 의 속은
공급자 SDK 를 들이지 않는다, BD-289).
"""
from __future__ import annotations

import json
import sys
from importlib import metadata

API_VERSION = 1
GROUPS = {"rlo.transcripts": ("last_usage", "usage_format"), "rlo.usage": ("normalize",), "rlo.tokenizers": ("count",)}
BUILTINS = {
    "rlo.transcripts": {"claude_code": "rlo.readers:CLAUDE_CODE", "codex_cli": "rlo.readers:CODEX_CLI",
                        "gemini_cli": "rlo.readers:GEMINI_CLI"},
    "rlo.usage": {"anthropic": "rlo.usage_formats:ANTHROPIC", "openai": "rlo.usage_formats:OPENAI",
                  "gemini": "rlo.usage_formats:GEMINI", "otel": "rlo.usage_formats:OTEL"},
    "rlo.tokenizers": {"bytes4": "rlo.usage_formats:BYTES4"},
}
DEFAULT_TOKENIZER = "bytes4"


class Registry:
    def __init__(self):
        self.plugins: dict = {g: {} for g in GROUPS}
        self.origin: dict = {g: {} for g in GROUPS}       # 이름 -> "module:attr (배포)"
        self.errors: list = []

    def _error(self, group, name, value, why):
        self.errors.append({"group": group, "name": name, "value": value, "error": why})

    def add(self, group: str, ep) -> None:
        """진입점 하나를 싣는다. 실패는 기록만 한다 -- 다른 플러그인을 막지 않는다."""
        name, value = ep.name, ep.value
        try:
            obj = ep.load()
            if isinstance(obj, type):
                obj = obj()
            for attr in ("name", "version", "api") + GROUPS[group]:
                if not hasattr(obj, attr):
                    raise TypeError(f"missing {attr!r}")
            if obj.name != name:
                raise ValueError(f"name {obj.name!r} differs from the entry point name {name!r}")
            if obj.api != API_VERSION:
                raise ValueError(f"plugin API {obj.api!r}, rlo speaks {API_VERSION}")
            if name in self.plugins[group]:
                raise ValueError(f"duplicate name (already from {self.origin[group][name]})")
        except Exception as e:                            # 들이기 실패 · 꼴 · 판 · 겹침 -- 그것만 빠진다
            self._error(group, name, value, f"{type(e).__name__}: {e}")
            return
        self.plugins[group][name] = obj
        dist = getattr(getattr(ep, "dist", None), "name", None)
        self.origin[group][name] = value + (f" ({dist})" if dist else "")

    def get(self, group: str, name: str):
        if group not in GROUPS:
            raise KeyError(f"unknown plugin group {group!r}")
        try:
            return self.plugins[group][name]
        except KeyError:
            why = [e["error"] for e in self.errors if e["group"] == group and e["name"] == name]
            raise KeyError(f"no {group} plugin {name!r}" + (f" ({why[0]})" if why else "")) from None

    def listing(self) -> dict:
        return {"api": API_VERSION,
                "plugins": {g: {n: {"version": str(p.version), "from": self.origin[g][n],
                                    **({"experimental": True} if getattr(p, "experimental", False) else {})}
                                for n, p in sorted(ps.items())} for g, ps in self.plugins.items()},
                "errors": list(self.errors)}


def _entry_points(group: str) -> list:
    eps = metadata.entry_points()
    return list(eps.select(group=group) if hasattr(eps, "select") else eps.get(group, []))


def load() -> Registry:
    """기본 제공을 먼저, 그다음 설치된 패키지의 진입점(이름 차례). 기본 제공과 같은 진입점(rlo-sdk 자신의 것)은 한 번만."""
    r = Registry()
    for group in GROUPS:
        builtin = BUILTINS[group]
        for name, value in builtin.items():
            r.add(group, metadata.EntryPoint(name, value, group))
        for ep in sorted(_entry_points(group), key=lambda e: (e.name, e.value)):
            if builtin.get(ep.name) == ep.value:
                continue                                  # rlo-sdk 가 pyproject 로 낸 같은 기본 제공
            r.add(group, ep)
    return r


_REGISTRY: "Registry | None" = None


def registry(reload: bool = False) -> Registry:
    global _REGISTRY
    if _REGISTRY is None or reload:
        _REGISTRY = load()
    return _REGISTRY


def get(group: str, name: str):
    return registry().get(group, name)


def load_errors() -> list:
    return list(registry().errors)


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] != ["list"]:
        print("usage: python -m rlo.plugins list", file=sys.stderr)
        return 2
    print(json.dumps(registry().listing(), ensure_ascii=False, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
