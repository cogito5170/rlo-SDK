"""판본 목록(BD-120 (3) · (4)) -- versions() 가 pyproject 의 고정 목록 · 깔린 메타데이터 · pip 가 받은 커밋과 같다."""
import os
import pathlib
import shutil
import tempfile
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


# 요구 문자열은 뜻으로 비교한다(CMD-K9). 빌드 도구마다 글자가 다르다: `action-contract @ git+…` · `action-contract@ git+…`
# (packaging < 22 의 str(Requirement) 꼴), 표지의 따옴표 · 빈칸, 이름의 대소문자 · `_`.
def _requirement_class():
    for mod in ("packaging.requirements", "pip._vendor.packaging.requirements"):
        try:
            return __import__(mod, fromlist=["Requirement"]).Requirement
        except ImportError:
            pass
    return None


def _canon(name: str) -> str:
    import re
    return re.sub(r"[-_.]+", "-", name.strip()).lower()                   # PEP 503


def _req_plain(s: str) -> tuple:
    """packaging 없이 -- `이름[extras] @ url ; 표지` 꼴만. 비교 꼴은 _req 와 같다."""
    head, _, marker = s.partition(";")
    name, at, url = head.partition("@")
    extras = ()
    if "[" in name:
        name, _, ex = name.partition("[")
        extras = tuple(sorted(_canon(x) for x in ex.rstrip().rstrip("]").split(",") if x.strip()))
    marker = "".join(marker.split()).replace("'", '"') or None
    return (_canon(name), extras, "", url.strip() if at else None, marker)


def _req(s: str) -> tuple:
    """요구 문자열 -> (이름, extras, 판 조건, url, 표지). 글자 꼴은 버리고 sha 를 포함한 뜻은 남긴다."""
    R = _requirement_class()
    if R is None:
        return _req_plain(s)
    r = R(s)
    marker = "".join(str(r.marker).split()).replace("'", '"') if r.marker else None
    return (_canon(r.name), tuple(sorted(_canon(x) for x in r.extras)), str(r.specifier), r.url, marker)


def _manifest_mismatch(reqs: list) -> "list[str]":
    """깔린 메타데이터의 요구(Requires-Dist) 가 고정 목록과 뜻으로 같은가. 다른 것들을 낸다(같으면 빈 목록)."""
    got = [_req(r) for r in reqs]
    plain = sorted(g for g in got if g[4] is None)
    sensor = sorted(g[:4] for g in got if g[4] == _req('x ; extra == "sensor"')[4])
    other = [g for g in got if g[4] is not None and g[4] != _req('x ; extra == "sensor"')[4]]
    bad = []
    if plain != sorted(_req(r) for r in _required()):
        bad.append(f"필수: {plain}")
    if sensor != sorted(_req(r)[:4] for r in _extras()["sensor"]):
        bad.append(f"[sensor]: {sensor}")
    if other:
        bad.append(f"모르는 표지: {other}")
    return bad


def _extras():
    return {extra: [_pins.requirement(p) for p in group.values()] for extra, group in _pins.EXTRAS.items()}


class Manifest(unittest.TestCase):
    def test_versions_gives_the_pins(self):
        v = rlo.versions()
        self.assertEqual(v["sdk"], f"rlo-sdk/{rlo.__version__}")
        self.assertEqual(rlo.__version__, "0.8.1")
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
        self.assertEqual(_manifest_mismatch(reqs), [])
        self.assertEqual(metadata.version("rlo-sdk"), rlo.__version__)       # 깔린 판 = versions() 의 판(pyproject 가 옆에 없어도)

    def test_builder_spellings_compare_by_meaning(self):
        """ga 쪽 빌드의 꼴(`이름@ git+…`)도 같은 목록이다. sha · 이름 · extras 가 다르면 여전히 다르다(CMD-K9)."""
        same = [_pins.requirement(p) for p in _pins.REQUIRED.values()] + \
               [_pins.requirement(_pins.EXTRAS["sensor"]["sensor"]) + ' ; extra == "sensor"']
        ga = [r.replace(" @ ", "@ ", 1).replace(' ; extra == "sensor"', " ; extra=='sensor'") for r in same]
        self.assertNotEqual(ga, same)                                       # 글자는 다르다
        self.assertEqual(_manifest_mismatch(same), [])
        self.assertEqual(_manifest_mismatch(ga), [])                        # 뜻은 같다
        self.assertEqual(_manifest_mismatch(list(reversed(ga))), [])        # 줄 순서는 빌드가 정한다
        wrong_sha = [same[0][:-1] + ("0" if same[0][-1] != "0" else "1"), *same[1:]]
        self.assertNotEqual(_manifest_mismatch(wrong_sha), [])
        self.assertNotEqual(_manifest_mismatch(same[1:]), [])               # 하나 빠짐
        self.assertNotEqual(_manifest_mismatch([*same[:-1], same[-1].replace("sensor\"", "other\"")]), [])

    def test_both_normalizers_agree(self):
        """packaging 이 있든 없든 같은 비교 꼴 -- 빈 venv 에 packaging 이 없을 수 있다."""
        cases = ["action-contract @ git+https://github.com/cogito5170/action@3995fdb3ba487f31d841d3e11b710e64f0d523db",
                 "Action_Contract@ git+https://github.com/cogito5170/action@3995fdb3ba487f31d841d3e11b710e64f0d523db",
                 'llmsensor @ git+https://github.com/cogito5170/Sensor@97961e9 ; extra == "sensor"',
                 "llmsensor@ git+https://github.com/cogito5170/Sensor@97961e9 ; extra=='sensor'"]   # url 뒤 ';' 앞 빈칸은 PEP 508
        plain = [_req_plain(c) for c in cases]
        self.assertEqual(plain[0], plain[1])
        self.assertEqual(plain[2], plain[3])
        self.assertNotEqual(plain[0][3], _req_plain(cases[0][:-1] + "x")[3])    # sha 한 글자도 다르면 다르다
        if _requirement_class() is not None:
            self.assertEqual([_req(c) for c in cases], plain)

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


# 배포 이름 -> import 이름. 옆 저장소를 PYTHONPATH 로만 붙였을 때 그 소스의 pyproject 를 찾는 데 쓴다
MODULES = {"l0-telemetry": "telemetry", "dc": "dc", "ms": "ms", "action-contract": "action", "guard": "guard",
           "health": "health", "llmsensor": "llmsensor"}


def _norm(name: str) -> str:
    return _canon(name)


def _deps_from_pyproject(path: pathlib.Path, dist: str):
    """옆 저장소 pyproject 의 [project] dependencies(extras 아님). 그 pyproject 의 이름이 dist 가 아니면 None."""
    text = path.read_text(encoding="utf-8")
    try:
        import tomllib
        proj = tomllib.loads(text).get("project", {})
        name, deps = proj.get("name"), proj.get("dependencies", [])
    except ImportError:                                      # Python 3.10: [project] 의 name · dependencies 만 글로 읽는다
        import re
        head = text.split("[project]", 1)[-1].split("\n[", 1)[0]
        m = re.search(r'^name\s*=\s*"([^"]+)"', head, re.M)
        name = m.group(1) if m else None
        d = re.search(r"^dependencies\s*=\s*\[(.*?)\]", head, re.M | re.S)
        deps = re.findall(r'"([^"]+)"', d.group(1)) if d else []
    return list(deps) if name and _norm(name) == _norm(dist) else None


def requirements_of(dist: str) -> "tuple[list, list]":
    """(요구들, 출처들). 출처: 그 배포의 소스 옆 pyproject(PYTHONPATH · 로컬 경로) 와 깔린 메타데이터 -- 있는 것 모두."""
    import importlib.util
    reqs, where = [], []
    spec = importlib.util.find_spec(MODULES[dist]) if dist in MODULES else None
    if spec is not None and spec.origin:
        pp = pathlib.Path(spec.origin).resolve().parent.parent / "pyproject.toml"
        if pp.exists():
            got = _deps_from_pyproject(pp, dist)
            if got is not None:
                reqs += got
                where.append("pyproject")
    try:
        md = [r for r in (metadata.requires(dist) or []) if ";" not in r]   # extras 의 요구는 SDK 가 쓰지 않는다
        reqs += md
        where.append("metadata")
    except metadata.PackageNotFoundError:
        pass
    return reqs, where


def _present(dist: str) -> bool:
    import importlib.util
    return importlib.util.find_spec(MODULES[dist]) is not None


class PinGraph(unittest.TestCase):
    """고정끼리 맞물린다: 고정 배포가 다른 고정 배포를 (extras 아닌 의존으로) 요구하면, 그 요구는 이 목록의 글자와 같다.
    pip 가 `ResolutionImpossible` 을 낼 짝(예: action 2f4791e 대 guard 의 3995fdb)을 설치 없이 잡는다.
    요구는 **옆 저장소 pyproject 를 직접** 읽고(PYTHONPATH 로만 붙였을 때), 깔린 메타데이터도 읽는다(BD-123 · CMD-K3)."""

    def test_every_pin_agrees_with_this_list(self):
        mine = {_norm(p[0]): _pins.requirement(p)
                for p in [*_pins.REQUIRED.values(), *[x for g in _pins.EXTRAS.values() for x in g.values()]]}
        checked = set()
        for dist in mine:
            reqs, where = requirements_of(dist)
            for r in reqs:
                name = _req(r)[0]
                if name in mine:
                    checked.add((dist, name))
                    self.assertEqual(_req(r), _req(mine[name]), f"{dist} 가 요구하는 {r!r} ({where})")
        want = {("guard", "action-contract"), ("health", "action-contract"), ("ms", "action-contract")}
        if _present("llmsensor"):
            want.add(("llmsensor", "l0-telemetry"))
        self.assertEqual(checked, want)                       # 건너뛰지 않는다: 일곱이 어떤 꼴로든 보여야 한다

    def test_reads_a_sibling_pyproject(self):
        d = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d)
        (d / "pyproject.toml").write_text('[project]\nname = "guard"\nversion = "0"\n'
                                          'dependencies = ["action-contract @ git+https://x/action@abc"]\n'
                                          '\n[project.optional-dependencies]\nz = ["q"]\n', encoding="utf-8")
        self.assertEqual(_deps_from_pyproject(d / "pyproject.toml", "guard"), ["action-contract @ git+https://x/action@abc"])
        self.assertIsNone(_deps_from_pyproject(d / "pyproject.toml", "health"))     # 다른 배포의 pyproject 는 읽지 않는다


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
