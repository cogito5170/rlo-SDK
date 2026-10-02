"""깔기 · 떼기(CMD-K6) -- `python -m rlo.hooks install-hook | uninstall-hook`. 모든 시험은 임시 디렉터리 · 임시 HOME 안에서만 돈다."""
import json
import os
import pathlib
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest

from rlo import install as I
from rlo.example_hooks import data, hook_input, now_after

try:
    import llmsensor  # noqa: F401
    SENSOR = True
except ImportError:
    SENSOR = False

MODEL = str(data("cc_tools_model.json"))
USER = {"permissions": {"allow": ["Bash(ls:*)"]},
        "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "echo rlo-is-not-us"}]}],
                  "Stop": [{"hooks": [{"type": "command", "command": "mba-frontend stop"}]}]}}


class Base(unittest.TestCase):
    def setUp(self):
        self.d = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.d)
        self.home = self.d / "home"
        self.home.mkdir()
        self.p = self.d / "settings.json"

    def cli(self, *args, home=None):
        env = {**os.environ, "HOME": str(home or self.home)}
        return subprocess.run([sys.executable, "-m", "rlo.hooks", *args], capture_output=True, text=True, env=env)

    def put(self, d):
        self.p.write_text(json.dumps(d, indent=2) + "\n", encoding="utf-8")

    def get(self):
        return json.loads(self.p.read_text(encoding="utf-8"))

    def ours(self, d, ev):
        return [h["command"] for g in d.get("hooks", {}).get(ev, []) for h in g["hooks"] if I.is_ours(h["command"])]

    def install(self, *extra):
        r = self.cli("install-hook", "--settings", str(self.p), "--model", MODEL, *extra)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def uninstall(self):
        r = self.cli("uninstall-hook", "--settings", str(self.p))
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)


class InstallUninstall(Base):
    def test_one_per_event_and_others_kept(self):
        self.put(USER)
        out = self.install("--grant", "Bash")
        self.assertTrue(out["changed"])
        d = self.get()
        for ev in ("PreToolUse", "Stop", "SessionEnd"):
            self.assertEqual(len(self.ours(d, ev)), 1, ev)
        self.assertEqual(d["permissions"], USER["permissions"])
        self.assertEqual(d["hooks"]["PreToolUse"][0], USER["hooks"]["PreToolUse"][0])      # 남의 훅은 제자리 · 앞
        self.assertEqual(d["hooks"]["Stop"][0], USER["hooks"]["Stop"][0])
        self.assertEqual(d["hooks"]["PreToolUse"][-1]["matcher"], "*")
        self.assertNotIn("matcher", d["hooks"]["Stop"][-1])                                 # Stop 은 matcher 를 받지 않는다

    def test_reinstall_keeps_one_and_replaces_in_place(self):
        self.put(USER)
        self.install()
        first = self.get()
        self.assertFalse(self.install()["changed"])                                        # 같은 설정이면 쓰지 않는다
        self.assertEqual(self.get(), first)
        self.install("--mode", "enforce")
        d = self.get()
        for ev in ("PreToolUse", "Stop", "SessionEnd"):
            (c,) = self.ours(d, ev)
            self.assertIn("--mode enforce", c)
        self.assertEqual(json.dumps(d["hooks"]["PreToolUse"][0]), json.dumps(first["hooks"]["PreToolUse"][0]))

    def test_reinstall_keeps_its_place_before_later_hooks(self):
        """사용자가 rlo 뒤에 훅을 더했어도, 다시 깔면 rlo 는 그 자리에 남는다(끝으로 옮기지 않는다)."""
        self.put(USER)
        self.install()
        d = self.get()
        d["hooks"]["Stop"].append({"hooks": [{"type": "command", "command": "echo later"}]})
        self.put(d)
        self.install("--mode", "enforce")
        stop = [h["command"] for g in self.get()["hooks"]["Stop"] for h in g["hooks"]]
        self.assertEqual(stop[0], "mba-frontend stop")
        self.assertTrue(I.is_ours(stop[1]) and "--mode enforce" in stop[1])
        self.assertEqual(stop[2:], ["echo later"])

    def test_uninstall_restores_the_original_json(self):
        self.put(USER)
        self.install("--grant", "Bash")
        self.uninstall()
        self.assertEqual(self.get(), USER)
        self.assertFalse(self.uninstall()["changed"])

    def test_uninstall_leaves_hooks_that_only_mention_rlo(self):
        self.put(USER)
        self.install()
        self.uninstall()
        self.assertIn("echo rlo-is-not-us", json.dumps(self.get()))

    def test_backup_is_bak_rlo_and_never_bak_mba(self):
        self.put(USER)
        before = self.p.read_text(encoding="utf-8")
        out = self.install()
        self.assertEqual(out["backup"], str(self.p) + ".bak-rlo")
        self.assertEqual(pathlib.Path(out["backup"]).read_text(encoding="utf-8"), before)
        self.assertFalse((self.d / "settings.json.bak-mba").exists())
        self.assertEqual(sorted(x.name for x in self.d.iterdir()), ["home", "settings.json", "settings.json.bak-rlo"])

    def test_missing_file_is_created_without_backup(self):
        out = self.install()
        self.assertIsNone(out["backup"])
        self.assertEqual(set(self.get()["hooks"]), {"PreToolUse", "Stop", "SessionEnd"})
        self.uninstall()
        self.assertEqual(self.get(), {})

    def test_default_path_is_home_claude_settings(self):
        r = self.cli("install-hook", "--model", MODEL)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["settings"], str(self.home / ".claude" / "settings.json"))
        self.assertTrue((self.home / ".claude" / "settings.json").is_file())


class Closing(Base):
    def test_broken_json_is_not_touched(self):
        self.p.write_text("{not json", encoding="utf-8")
        for cmd in (("install-hook", "--model", MODEL), ("uninstall-hook",)):
            r = self.cli(cmd[0], "--settings", str(self.p), *cmd[1:])
            self.assertEqual(r.returncode, 1)
            self.assertEqual(self.p.read_text(encoding="utf-8"), "{not json")
        self.assertEqual(sorted(x.name for x in self.d.iterdir()), ["home", "settings.json"])

    def test_wrong_shape_is_not_touched(self):
        self.put({"hooks": {"Stop": "oops"}})
        r = self.cli("install-hook", "--settings", str(self.p), "--model", MODEL)
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.get(), {"hooks": {"Stop": "oops"}})

    def test_bad_model_is_not_installed(self):
        self.put(USER)
        bad = self.d / "bad.json"
        bad.write_text('{"nope": 1}', encoding="utf-8")
        for model in (str(bad), str(self.d / "missing.json")):
            r = self.cli("install-hook", "--settings", str(self.p), "--model", model)
            self.assertEqual(r.returncode, 1)
            self.assertEqual(self.get(), USER)


@unittest.skipUnless(SENSOR, "훅 판정은 Sensor 가 필요하다 -- rlo-sdk[sensor]")
class InstalledCommandRuns(Base):
    def test_installed_command_is_the_hook(self):
        self.install("--mode", "enforce", "--grant", "Bash")
        (cmd,) = self.ours(self.get(), "PreToolUse")
        argv = shlex.split(cmd) + ["--now-ms", str(now_after("parallel"))]
        r = subprocess.run(argv, input=json.dumps(hook_input("parallel")), capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"], "deny")
        argv = shlex.split(cmd) + ["--now-ms", str(now_after("normal"))]
        r = subprocess.run(argv, input=json.dumps(hook_input("normal")), capture_output=True, text=True)
        self.assertEqual((r.returncode, r.stdout), (0, ""))


if __name__ == "__main__":
    unittest.main()
