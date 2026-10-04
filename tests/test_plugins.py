"""rlo 플러그인 · 모형 중립(CMD-K18) -- 진입점 등록, 런타임 읽개, usage 꼴, 토크나이저, 공급자 SDK 없는 속, 꾸러미."""
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest

from rlo import ctxbudget as CB
from rlo import plugins, pspec

ROOT = pathlib.Path(__file__).resolve().parent.parent
RT = pathlib.Path(__file__).parent / "fixtures" / "runtimes"

try:
    import telemetry.usage  # noqa: F401
    TELEMETRY = True
except ImportError:
    TELEMETRY = False
NEEDS_TELEMETRY = unittest.skipUnless(TELEMETRY, "Telemetry 가 없다")


class Builtins(unittest.TestCase):
    def test_builtins_load_through_the_registry(self):
        r = plugins.registry(reload=True)
        self.assertEqual(r.errors, [])
        self.assertEqual({g: sorted(p) for g, p in r.plugins.items()},
                         {"rlo.transcripts": ["claude_code", "codex_cli", "gemini_cli"],
                          "rlo.usage": ["anthropic", "gemini", "openai", "otel"], "rlo.tokenizers": ["bytes4"]})
        self.assertTrue(plugins.get("rlo.transcripts", "codex_cli").experimental)
        self.assertFalse(plugins.get("rlo.transcripts", "claude_code").experimental)
        with self.assertRaises(KeyError):
            plugins.get("rlo.transcripts", "nope")

    def test_list_command(self):
        p = subprocess.run([sys.executable, "-m", "rlo.plugins", "list"], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        out = json.loads(p.stdout)
        self.assertEqual((out["api"], out["errors"]), (1, []))
        self.assertIn("gemini_cli", out["plugins"]["rlo.transcripts"])

    def test_pyproject_declares_the_same_builtins(self):
        try:
            import tomllib
        except ImportError:
            self.skipTest("Python < 3.11")
        p = ROOT / "pyproject.toml"
        if not p.exists():
            self.skipTest("pyproject.toml 이 옆에 없다")
        eps = tomllib.loads(p.read_text(encoding="utf-8"))["project"]["entry-points"]
        self.assertEqual({g: eps[g] for g in plugins.GROUPS}, plugins.BUILTINS)


@NEEDS_TELEMETRY
class Readers(unittest.TestCase):
    def test_fixture_transcripts(self):
        for runtime, path, want in [("claude_code", "claude_code.jsonl", 3 + 152000 + 400),
                                    ("codex_cli", "codex_cli.jsonl", 162000),
                                    ("gemini_cli", "gemini_cli.jsonl", 181000 + 500),     # prompt(캐시 포함) + 도구 프롬프트
                                    ("gemini_cli", "gemini_cli_legacy.json", 50000)]:
            with self.subTest(runtime=runtime, path=path):
                self.assertEqual(CB.context_of(str(RT / path), runtime), want)
        self.assertEqual(CB.context_of(str(RT / "claude_code.jsonl")), 152403)         # 이름 없음 = claude_code
        self.assertEqual(CB.context_tokens(str(RT / "claude_code.jsonl")), 152403)     # K17 입구 그대로

    def test_unreadable_is_none_never_zero(self):
        d = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d)
        (d / "garbage").write_bytes(b"\xff\xfe not json\n{")
        (d / "empty").write_text("")
        (d / "nousage.jsonl").write_text(json.dumps({"type": "event_msg", "payload": {"type": "token_count", "info": None}}) + "\n")
        (d / "notokens.jsonl").write_text(json.dumps({"id": "g", "type": "gemini", "content": "x"}) + "\n")
        (d / "partial.jsonl").write_text(json.dumps({"type": "assistant", "message": {"usage": {"input_tokens": 4}}}) + "\n")
        for runtime in ("claude_code", "codex_cli", "gemini_cli"):
            for name in ("missing", "garbage", "empty", "nousage.jsonl", "notokens.jsonl", "partial.jsonl"):
                with self.subTest(runtime=runtime, file=name):
                    self.assertIsNone(CB.context_of(str(d / name), runtime))

    def test_rewind_removes_the_message_and_what_follows(self):
        g = plugins.get("rlo.transcripts", "gemini_cli").last_usage(str(RT / "gemini_cli.jsonl"))
        self.assertEqual(g["prompt_token_count"], 181000)                              # g2(999000) 는 되감겼다

    def test_budget_picks_the_reader_from_config(self):
        b = CB.Budget(100000, 170000, runtime="codex_cli")
        self.assertEqual(b.context(str(RT / "codex_cli.jsonl")), 162000)
        self.assertEqual(CB.decide(b.context(str(RT / "codex_cli.jsonl")), "Bash", {"command": "ls"}, b)[0], "warn")
        self.assertIsNone(CB.Budget(100000, 170000).context(str(RT / "codex_cli.jsonl")))   # claude_code 로는 못 읽는다
        with self.assertRaises(KeyError):
            CB.Budget(1, 2, runtime="nope")                                            # 없는 읽개는 설정 오류
        with self.assertRaises(KeyError):
            CB.Budget(1, 2, usage_format="nope")
        self.assertEqual(CB.Budget.of({"soft": 1, "hard": 2, "runtime": "gemini_cli"}).runtime, "gemini_cli")


@NEEDS_TELEMETRY
class UsageFormats(unittest.TestCase):
    def n(self, fmt, raw):
        return plugins.get("rlo.usage", fmt).normalize(raw)

    def test_normalized_context(self):
        self.assertEqual(self.n("anthropic", {"input_tokens": 3, "cache_read_input_tokens": 100, "cache_creation_input_tokens": 7,
                                              "output_tokens": 2})["context"], 110)
        self.assertIsNone(self.n("anthropic", {"input_tokens": 3, "output_tokens": 2})["context"])     # 모름은 None
        self.assertIsNone(self.n("anthropic", {"input_tokens": True, "cache_read_input_tokens": 1,
                                               "cache_creation_input_tokens": 1})["context"])
        o = self.n("openai", {"prompt_tokens": 500, "prompt_tokens_details": {"cached_tokens": 400}, "completion_tokens": 9})
        self.assertEqual((o["input"], o["cache_read"], o["cache_creation"], o["context"]), (100, 400, 0, 500))
        o = self.n("openai", {"input_tokens": 500, "output_tokens": 9})                              # 캐시 칸이 없다
        self.assertEqual((o["input"], o["cache_read"], o["context"]), (500, 0, 500))
        self.assertIsNone(self.n("openai", {"output_tokens": 9})["context"])
        g = self.n("gemini", {"prompt_token_count": 800, "candidates_token_count": 5})                # cached 를 빼고 보냄
        self.assertEqual((g["input"], g["cache_read"], g["context"]), (800, 0, 800))
        self.assertIsNone(self.n("gemini", {"candidates_token_count": 5})["context"])
        t = self.n("otel", {"input_tokens": 900, "cached_input_tokens": 600, "output_tokens": 3})
        self.assertEqual((t["context"], t["cache_read"], t["input"]), (900, 600, 300))
        for fmt in ("anthropic", "openai", "gemini", "otel"):
            self.assertIsNone(self.n(fmt, None))

    def test_token_report_by_name(self):
        texts = ["hello world", "é" * 9]
        r = pspec.token_report(texts, [{"prompt_tokens": 50, "prompt_tokens_details": {"cached_tokens": 10}},
                                       {"prompt_tokens": 70}], "openai", tokenizer="bytes4")
        self.assertEqual([t["provider"]["context"] for t in r["turns"]], [50, 70])
        self.assertEqual([t["provider"]["prompt_tokens"] for t in r["turns"]], [50, 70])          # 앞 판의 이름도 그대로
        self.assertEqual(r["provider_prompt_tokens"], 120)
        self.assertEqual([t["estimate"] for t in r["turns"]], [3, 5])
        self.assertEqual(r["tokenizer"], {"name": "bytes4", "tokens": 8})
        with self.assertRaises(KeyError):
            pspec.token_report(texts, tokenizer="nope")

    def test_default_tokenizer_is_bytes_over_four(self):
        b4 = plugins.get("rlo.tokenizers", "bytes4")
        for s in ("", "a", "abcd", "abcde", "é", "한국어 글", "x" * 1001):
            self.assertEqual((b4.count(s), pspec.tokens(s)), (-(-len(s.encode()) // 4),) * 2)
        self.assertNotIn("tokenizer", pspec.token_report(["abc"]))                                # 이름 없으면 추정만


TOY = '''
import json
class _R:
    name, version, api, usage_format, experimental = "toyrt", "0.1", 1, "toyfmt", False
    def last_usage(self, path):
        try:
            return json.load(open(path))["usage"]
        except Exception:
            return None
class _F:
    name, version, api = "toyfmt", "0.1", 1
    def normalize(self, raw):
        c = raw.get("ctx") if isinstance(raw, dict) else None
        return {"input": c, "cache_read": 0, "cache_creation": 0, "output": None, "context": c}
class _T:
    name, version, api = "words", "0.1", 1
    def count(self, text):
        return len(text.split())
class _Old:
    name, version, api = "oldapi", "0.0", 0
    def normalize(self, raw):
        return None
class _Dup:
    name, version, api = "bytes4", "9", 1
    def count(self, text):
        return 0
READER, FMT, WORDS, OLDAPI, DUP = _R(), _F(), _T(), _Old(), _Dup()
NOTAPLUGIN = object()
'''
TOY_PYPROJECT = '''
[build-system]
requires = ["setuptools>=61"]
build-backend = "setuptools.build_meta"
[project]
name = "rlo-toy-plugins"
version = "0.1"
[tool.setuptools]
py-modules = ["toyplug"]
[project.entry-points."rlo.transcripts"]
toyrt = "toyplug:READER"
broken_import = "toyplug_missing:READER"
[project.entry-points."rlo.usage"]
toyfmt = "toyplug:FMT"
oldapi = "toyplug:OLDAPI"
badshape = "toyplug:NOTAPLUGIN"
[project.entry-points."rlo.tokenizers"]
words = "toyplug:WORDS"
bytes4 = "toyplug:DUP"
'''
USE = '''
import json, sys
from rlo import plugins, ctxbudget, pspec
r = plugins.registry(reload=True)
b = ctxbudget.Budget(10, 20, runtime="toyrt")
out = {"errors": sorted((e["group"], e["name"], e["error"].split(":")[0]) for e in r.errors),
       "transcripts": sorted(r.plugins["rlo.transcripts"]), "usage": sorted(r.plugins["rlo.usage"]),
       "tokenizers": sorted(r.plugins["rlo.tokenizers"]), "bytes4_from": r.origin["rlo.tokenizers"]["bytes4"],
       "context": b.context(sys.argv[1]),
       "report": pspec.token_report(["a b c", "d"], [{"ctx": 5}, {"ctx": 7}], "toyfmt", tokenizer="words")}
print(json.dumps(out))
'''


class ThirdParty(unittest.TestCase):
    """설치한 패키지가 진입점으로 셋을 등록하고 이름으로 쓰인다. 고장 난 플러그인은 기록되고 나머지는 실린다."""

    def test_installed_package_plugs_in(self):
        d = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d)
        pkg, site = d / "pkg", d / "site"
        pkg.mkdir()
        (pkg / "toyplug.py").write_text(TOY, encoding="utf-8")
        (pkg / "pyproject.toml").write_text(TOY_PYPROJECT, encoding="utf-8")
        p = subprocess.run([sys.executable, "-m", "pip", "install", "-q", "--no-deps", "--no-build-isolation", "--no-index",
                            "--target", str(site), str(pkg)], capture_output=True, text=True)
        if p.returncode != 0:
            self.skipTest(f"pip 로 깔 수 없다: {p.stderr.strip().splitlines()[-1:]}")
        self.assertTrue(list(site.glob("rlo_toy_plugins-0.1.dist-info/entry_points.txt")))
        transcript = d / "t.json"
        transcript.write_text(json.dumps({"usage": {"ctx": 15}}), encoding="utf-8")
        env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(site), str(ROOT), os.environ.get("PYTHONPATH", "")]))
        q = subprocess.run([sys.executable, "-c", USE, str(transcript)], capture_output=True, text=True, env=env, cwd=str(d))
        self.assertEqual(q.returncode, 0, q.stderr)
        out = json.loads(q.stdout)
        self.assertEqual(out["errors"], [["rlo.tokenizers", "bytes4", "ValueError"],          # 겹친 이름
                                         ["rlo.transcripts", "broken_import", "ModuleNotFoundError"],
                                         ["rlo.usage", "badshape", "TypeError"],             # 꼴이 아니다
                                         ["rlo.usage", "oldapi", "ValueError"]])             # API 판이 다르다
        self.assertEqual(out["transcripts"], ["claude_code", "codex_cli", "gemini_cli", "toyrt"])
        self.assertEqual(out["usage"], ["anthropic", "gemini", "openai", "otel", "toyfmt"])
        self.assertEqual(out["tokenizers"], ["bytes4", "words"])
        self.assertEqual(out["bytes4_from"], "rlo.usage_formats:BYTES4")                     # 기본 제공이 이긴다
        self.assertEqual(out["context"], 15)
        self.assertEqual([t["provider"]["context"] for t in out["report"]["turns"]], [5, 7])
        self.assertEqual(out["report"]["tokenizer"], {"name": "words", "tokens": 4})


BLOCK = '''
import importlib, pkgutil, sys
BLOCKED = ("openai", "anthropic", "google.generativeai", "google.genai", "vertexai", "mistralai", "cohere", "ollama",
           "litellm", "boto3", "groq", "together")
hits = []
class Block:
    def find_spec(self, name, path=None, target=None):
        if any(name == b or name.startswith(b + ".") for b in BLOCKED):
            hits.append(name)
            raise ImportError(f"blocked provider SDK {name}")
        return None
sys.meta_path.insert(0, Block())
import rlo
mods = sorted(m.name for m in pkgutil.walk_packages(rlo.__path__, "rlo."))
for m in mods:
    importlib.import_module(m)
from rlo import plugins
plugins.registry(reload=True)
print(len(mods), hits)
'''


class ProviderFree(unittest.TestCase):
    def test_core_imports_no_provider_sdk(self):
        p = subprocess.run([sys.executable, "-c", BLOCK], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        n, hits = p.stdout.split(" ", 1)
        self.assertGreater(int(n), 10)
        self.assertEqual(hits.strip(), "[]")


class Packaging(unittest.TestCase):
    MODEL = str(ROOT / "rlo" / "data" / "cc_tools_model.json")

    def setUp(self):
        self.d = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.d)
        if not pathlib.Path(self.MODEL).exists():
            from rlo.example_hooks import data
            self.MODEL = str(data("cc_tools_model.json"))

    def test_install_hook_takes_the_budget_flags_and_runtime(self):
        s = self.d / "settings.json"
        p = subprocess.run([sys.executable, "-m", "rlo.hooks", "install-hook", "--settings", str(s), "--model", self.MODEL,
                            "--budget-soft", "150000", "--budget-hard", "200000", "--budget-state", "NOTES.md",
                            "--budget-mode", "enforce", "--runtime", "codex_cli"], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        cmd = json.loads(s.read_text())["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
        for part in ("--budget-soft 150000", "--budget-hard 200000", "--budget-mode enforce", "--budget-state NOTES.md",
                     "--budget-runtime codex_cli"):
            self.assertIn(part, cmd)
        bad = subprocess.run([sys.executable, "-m", "rlo.hooks", "install-hook", "--settings", str(self.d / "b.json"),
                              "--model", self.MODEL, "--budget-soft", "5", "--budget-hard", "4"], capture_output=True, text=True)
        self.assertEqual(bad.returncode, 1)
        self.assertFalse((self.d / "b.json").exists())                                       # 틀린 예산은 쓰지 않는다
        half = subprocess.run([sys.executable, "-m", "rlo.hooks", "install-hook", "--settings", str(self.d / "h.json"),
                               "--model", self.MODEL, "--runtime", "codex_cli"], capture_output=True, text=True)
        self.assertEqual(half.returncode, 1)
        none = self.d / "n.json"
        subprocess.run([sys.executable, "-m", "rlo.hooks", "install-hook", "--settings", str(none), "--model", self.MODEL],
                       capture_output=True, text=True, check=True)
        self.assertNotIn("--budget", json.loads(none.read_text())["hooks"]["PreToolUse"][0]["hooks"][0]["command"])

    def test_claude_code_plugin_layout(self):
        out = self.d / "plug"
        p = subprocess.run([sys.executable, "-m", "rlo.hooks", "claude-plugin", "--out", str(out), "--model", self.MODEL,
                            "--grant", "Bash", "--budget-soft", "150000", "--budget-hard", "200000"],
                           capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        manifest = json.loads((out / ".claude-plugin" / "plugin.json").read_text())
        self.assertEqual(manifest["name"], "rlo-guard")
        hooks = json.loads((out / "hooks" / "hooks.json").read_text())["hooks"]
        self.assertEqual(sorted(hooks), ["PreToolUse", "SessionEnd", "Stop"])
        cmd = hooks["PreToolUse"][0]["hooks"][0]["command"]
        self.assertIn('--model "${CLAUDE_PLUGIN_ROOT}/rlo/model.json"', cmd)
        self.assertIn("--budget-soft 150000", cmd)
        self.assertEqual(json.loads((out / "rlo" / "model.json").read_text()), json.loads(pathlib.Path(self.MODEL).read_text()))
        # 지은 명령이 실제로 돈다(셸이 ${CLAUDE_PLUGIN_ROOT} 를 펼친다)
        env = dict(os.environ, CLAUDE_PLUGIN_ROOT=str(out))
        q = subprocess.run(["bash", "-c", cmd], input=json.dumps({"hook_event_name": "Stop", "session_id": "s",
                                                                 "transcript_path": str(RT / "claude_code.jsonl")}),
                           capture_output=True, text=True, env=env)
        self.assertEqual(q.returncode, 0, q.stderr)
        again = subprocess.run([sys.executable, "-m", "rlo.hooks", "claude-plugin", "--out", str(out), "--model", self.MODEL],
                               capture_output=True, text=True)
        self.assertEqual(again.returncode, 1)                                                # 비어 있지 않은 곳에 쓰지 않는다
        claude = shutil.which("claude")
        if claude:
            home = self.d / "home"
            home.mkdir()
            v = subprocess.run([claude, "plugin", "validate", "--json", "--strict", str(out)], capture_output=True, text=True,
                               env={"PATH": os.environ.get("PATH", ""), "HOME": str(home), "CLAUDE_CONFIG_DIR": str(home / "c")},
                               timeout=120)
            self.assertEqual(v.returncode, 0, v.stdout[-2000:])


if __name__ == "__main__":
    unittest.main()
