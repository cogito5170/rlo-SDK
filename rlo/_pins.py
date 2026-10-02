"""판본 목록(manifest) -- SDK 가 고정하는 일곱 저장소의 커밋 sha. **이 파일이 원본이다**(BD-120 (3) · (4)).

`pyproject.toml` 의 dependencies · optional-dependencies 는 이 목록과 글자까지 같아야 한다(tests/test_versions.py 가 붙든다).
판은 baseline `STAGES.md` stage-3 이다. 단 하나 다르다:

    action  stage-3 머리는 2f4791e 이지만 **3995fdb** 로 고정한다. MS · guard · health 가 모두 action 을 3995fdb 로 고정했고,
            pip 는 같은 배포 이름을 다른 URL 둘로 받지 못한다(ResolutionImpossible). 두 커밋의 `action/` 패키지 코드와
            pyproject 는 같다(`git diff 3995fdb 2f4791e -- action/ pyproject.toml` 이 빈다 -- 다른 것은 시험 · 문서뿐).

URL 은 의존하는 쪽이 쓴 글자 그대로다(예: Sensor 는 `.../Telemetry@…` 를 쓴다). 글자가 다르면 pip 가 다른 URL 로 본다.
"""
from __future__ import annotations

_GH = "https://github.com/cogito5170/"

# 이름 -> (배포 이름, 저장소 URL, 커밋 sha)
REQUIRED = {
    "telemetry": ("l0-telemetry", _GH + "Telemetry", "89d2887768a1f230860ea6323870a747fd4323ef"),
    "dc": ("dc", _GH + "DC", "b55ff044c992f7ca3fb9ddeb807839be5ae8dd06"),
    "ms": ("ms", _GH + "MS", "74a8585f77cb999f6b4c562b6b90ce3218682ae9"),
    "action": ("action-contract", _GH + "action", "3995fdb3ba487f31d841d3e11b710e64f0d523db"),
    "guard": ("guard", _GH + "guard", "be871b9d89fe77badeef901caaa75edc1848f13c"),
    "health": ("health", _GH + "health", "afcff3960694f58978afec2cdde62cac9e27830f"),
}

# extras 이름 -> {이름 -> (배포 이름, 저장소 URL, 커밋 sha)}
EXTRAS = {
    "sensor": {"sensor": ("llmsensor", _GH + "Sensor", "a073e7741a33dcaaa81ba0227ad6fee71bde0d30")},
}


def requirement(pin: tuple) -> str:
    dist, url, sha = pin
    return f"{dist} @ git+{url}@{sha}"
