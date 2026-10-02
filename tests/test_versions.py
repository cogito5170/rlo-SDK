"""판본 목록(BD-120 (3) · (4)) -- versions() 가 pyproject 의 고정 목록 · 깔린 메타데이터 · pip 가 받은 커밋과 같다."""
import os
import pathlib
import unittest
from importlib import metadata
from unittest import mock

import rlo
from rlo import _pins

FROZEN = {
    "action-contract": "action-contract/1",
    "action-spec": "action-spec/1",
    "action-model": "action-model/1",
    "guard-result": "guard-result/1",
    "validation-result": "validation-result/1",
    "verification-record": "verification-record/1",
    "state-export": "llmsensor.state-export/2",
    "l0-telemetry": "l0-telemetry/1",
}


def _pyproject():
    root = pathlib.Path(os.environ.get("RLO_REPO") or pathlib.Path(__file__).resolve().parent.parent)
    p = root / "pyproject.toml"
    try:
        import tomllib
    except ImportError:                      # Python 3.10
        return None
    return tomllib.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _required():
    return [_pins.requirement(p) for p in _pins.REQUIRED.values()]


def _extras():
    return {extra: [_pins.requirement(p) for p in group.values()] for extra, group in _pins.EXTRAS.items()}


class Manifest(unittest.TestCase):
    def test_versions_gives_the_pins(self):
        v = rlo.versions()
        self.assertEqual(v["sdk"], f"rlo-sdk/{rlo.__version__}")
        self.assertEqual(v["pins"], {k: p[2] for k, p in _pins.REQUIRED.items()})
        self.assertEqual(v["extras"], {"sensor": {"sensor": _pins.EXTRAS["sensor"]["sensor"][2]}})
        self.assertEqual(rlo.Autonomy.versions(), v)                      # 입구에서도 같은 것을 본다

    def test_seven_repositories_guard_and_health_required_sensor_extra(self):
        self.assertEqual(set(_pins.REQUIRED), {"telemetry", "dc", "ms", "action", "guard", "health"})
        self.assertEqual(_pins.EXTRAS, {"sensor": {"sensor": _pins.EXTRAS["sensor"]["sensor"]}})
        for pin in [*_pins.REQUIRED.values(), _pins.EXTRAS["sensor"]["sensor"]]:
            self.assertRegex(pin[2], r"^[0-9a-f]{40}$")                    # 바뀌지 않는 커밋 sha 로만

    def test_pyproject_is_the_same_list(self):
        pp = _pyproject()
        if pp is None:
            self.skipTest("pyproject.toml 이 옆에 없다(소스가 아닌 곳에서 돎) 또는 Python < 3.11 -- RLO_REPO")
        self.assertEqual(pp["project"]["name"], "rlo-sdk")
        self.assertEqual(pp["project"]["version"], rlo.__version__)
        self.assertEqual(pp["project"]["dependencies"], _required())
        self.assertEqual(pp["project"].get("optional-dependencies", {}), _extras())

    def test_installed_metadata_is_the_same_list(self):
        try:
            reqs = metadata.requires("rlo-sdk")
        except metadata.PackageNotFoundError:
            self.skipTest("rlo-sdk 가 깔려 있지 않다(소스에서 돎)")
        plain = [r for r in reqs if "extra ==" not in r]
        sensor = [r.split(";")[0].strip() for r in reqs if "extra ==" in r]
        self.assertEqual(plain, _required())
        self.assertEqual(sensor, _extras()["sensor"])

    def test_pip_got_the_pinned_commits(self):
        """깔린 것이 git 에서 왔으면, 그 커밋이 고정 sha 와 같다(direct_url.json). 하나의 배포가 두 판으로 들어오지 않았다."""
        v = rlo.versions()
        every = {**v["pins"], **v["extras"]["sensor"]}
        got = {k: c for k, c in v["installed"].items() if c is not None}
        if not got:
            self.skipTest("git 에서 깐 의존이 없다")
        for name, commit in got.items():
            self.assertEqual(commit, every[name], name)


    def test_installed_is_read_not_assumed(self):
        """깔리지 않은 배포는 None 이다 -- installed 는 고정 목록을 베끼지 않고 실제로 읽는다."""
        fake = {"nowhere": ("rlo-no-such-dist", "https://example.invalid/x", "0" * 40)}
        with mock.patch.dict(_pins.EXTRAS, {"probe": fake}):
            self.assertIsNone(rlo.versions()["installed"]["nowhere"])


class FrozenContracts(unittest.TestCase):
    def test_contracts_are_the_frozen_ones(self):
        c = rlo.versions()["contracts"]
        try:
            import llmsensor  # noqa: F401
        except ImportError:
            self.assertIsNone(c["state-export"])                                # [sensor] 없이 깔면 None
            c = dict(c, **{"state-export": FROZEN["state-export"]})
        self.assertEqual(c, FROZEN)


if __name__ == "__main__":
    unittest.main()
