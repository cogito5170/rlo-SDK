"""판본 목록(manifest) -- SDK 가 고정하는 일곱 저장소의 커밋 sha. **이 파일이 원본이다**(BD-120 (3) · (4)).

`pyproject.toml` 의 dependencies · optional-dependencies 는 이 목록과 글자까지 같아야 한다(tests/test_versions.py 가 붙든다).
판은 CMD-PIN1 의 다시 고정이다(NET 머리): Telemetry `f6c7ae2` · Sensor `ff17bdd`(0.2.1, Telemetry 를 f6c7ae2 로 고정) · MS `1f1018e` · DC `7e0ac14` ·
action `9d6729f` · guard · health 는 그대로. 모두 옛 고정의 자손이다. 하나는 stage-3 머리와 다르다:

    action  stage-3 머리는 2f4791e 이지만 **9d6729f** 로 고정한다. MS · guard · health 가 모두 action 을 같은 sha 로 고정했고,
            pip 는 같은 배포 이름을 다른 URL 둘로 받지 못한다(ResolutionImpossible). 두 커밋의 `action/` 패키지 코드와
            pyproject 는 같다(`git diff 3995fdb 2f4791e -- action/ pyproject.toml` 이 빈다 -- 다른 것은 시험 · 문서뿐).

고정끼리 맞물려야 한다 -- 깔린 배포가 다른 고정 배포를 요구하면 그 글자가 이 목록과 같아야 한다(tests/test_versions.py
`PinGraph`). URL 은 의존하는 쪽이 쓴 글자 그대로다(예: Sensor 는 `.../Telemetry@…` 를 쓴다). 글자가 다르면 pip 가 다른 URL 로 본다.
"""
from __future__ import annotations

_GH = "https://github.com/cogito5170/"

# 이름 -> (배포 이름, 저장소 URL, 커밋 sha)
REQUIRED = {
    "telemetry": ("l0-telemetry", _GH + "Telemetry", "f6c7ae26d336965d3558092517e97d37d222c4ea"),
    "dc": ("dc", _GH + "DC", "7e0ac1494192e49ab03c687c79f7156cb38abcec"),
    "ms": ("ms", _GH + "MS", "1f1018e0348428c9b83af4f6679659f0057e3f4b"),
    "action": ("action-contract", _GH + "action", "9d6729fc6809d68d2b5d55b4ad2fd37e5281d998"),
    "guard": ("guard", _GH + "guard", "be871b9d89fe77badeef901caaa75edc1848f13c"),
    "health": ("health", _GH + "health", "afcff3960694f58978afec2cdde62cac9e27830f"),
}

# extras 이름 -> {이름 -> (배포 이름, 저장소 URL, 커밋 sha)}
EXTRAS = {
    "sensor": {"sensor": ("llmsensor", _GH + "Sensor", "ff17bddfd24f6d8cf9c14c0c45107b53818dd26a")},
}


def requirement(pin: tuple) -> str:
    dist, url, sha = pin
    return f"{dist} @ git+{url}@{sha}"
