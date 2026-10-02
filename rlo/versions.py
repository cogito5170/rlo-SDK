"""`versions()` -- 고정 목록과 동결 계약 판본(BD-120 (4)). 공개 경계의 판본을 한곳에서 본다.

    {"sdk": "rlo-sdk/0.1.0",                             판본 문자열(SDK.md §6). __version__ 은 "0.1.0"
     "pins":      {이름: 커밋 sha}                      rlo/_pins.py 의 필수 고정
     "extras":    {extras: {이름: 커밋 sha}}            선택 설치의 고정
     "installed": {이름: 커밋 sha | None}               pip 가 실제로 받은 커밋(direct_url.json). git 이 아닌 설치 · 없으면 None
     "contracts": {계약: 판본 | None}}                  깔린 패키지가 스스로 밝힌 판본. 패키지가 없으면 None
"""
from __future__ import annotations

import importlib
import json

from . import _pins

__version__ = "0.1.0"

# 동결 계약 -- 계약 이름 -> (모듈, 이름). 판본은 깔린 패키지에서 읽는다(SDK 가 다시 적지 않는다)
CONTRACT_SOURCES = {
    "action-contract": ("action", "SPEC"),                         # BD-96
    "action-spec": ("action", "SPEC_SCHEMA"),                      # BD-109
    "action-model": ("action", "MODEL_SCHEMA"),                    # BD-109
    "guard-result": ("guard.forms", "GUARD_SCHEMA"),               # BD-102
    "validation-result": ("guard.forms", "VALIDATION_SCHEMA"),     # BD-102
    "verification-record": ("health.verification", "SCHEMA"),      # BD-101
    "state-export": ("llmsensor.state.export", "CONTRACT"),        # BD-56, extras [sensor]
    "l0-telemetry": ("telemetry", "SPEC"),
}


def _installed_commit(dist: str) -> "str | None":
    from importlib import metadata
    try:
        raw = metadata.distribution(dist).read_text("direct_url.json")
    except metadata.PackageNotFoundError:
        return None
    if not raw:
        return None
    return json.loads(raw).get("vcs_info", {}).get("commit_id")


def _contract(module: str, attr: str) -> "str | None":
    try:
        return getattr(importlib.import_module(module), attr)
    except (ImportError, AttributeError):
        return None


def versions() -> dict:
    every = {**_pins.REQUIRED, **{k: v for group in _pins.EXTRAS.values() for k, v in group.items()}}
    return {
        "sdk": f"rlo-sdk/{__version__}",
        "pins": {name: pin[2] for name, pin in _pins.REQUIRED.items()},
        "extras": {extra: {name: pin[2] for name, pin in group.items()} for extra, group in _pins.EXTRAS.items()},
        "installed": {name: _installed_commit(pin[0]) for name, pin in every.items()},
        "contracts": {name: _contract(*src) for name, src in CONTRACT_SOURCES.items()},
    }
