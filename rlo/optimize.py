"""rlo.optimize -- 프롬프트 변형을 결정론 지표로 찾는다(CMD-K15 S3 · S4, BD-288). 고르기만 하고 쓰거나 내보내지 않는다.

    r = optimize(spec, [v1, v2], cases, model=call, metrics=[Metric("exact", exact)],
                 split={"train": [...], "held_out": [...]}, governor=Governor({"m": {"rpm": 5, "calls": 10}}),
                 run_dir="runs/plan-1", margin=0.1)
    r.adopted · r.chosen · r.held_out · r.status            # 쓰는 것은 부른 쪽(코드 diff · baseline 판정)

- **모형은 넣어 받는 함수다**(`model(prompt_text) -> str` 또는 {"text", "usage"}). rlo 는 공급자 SDK 를 들이지 않고 키를 읽지 않는다.
- **지표는 결정론**이어야 한다. `Metric(kind="llm_judge")` 만 있으면 받지 않는다(LLM 의 판정은 Opinion -- 그것만으로 채점하지 않는다).
  점수는 지표들의 평균, 지표는 [0, 1].
- **차례**: 모든 변형(base 먼저)을 train 에서 잰다 -> train 이 base 보다 높은 가장 좋은 변형 하나만 held-out 에서 base 와 함께 잰다 ->
  held-out 에서 `base + margin` 이상이면 채택, 아니면 base 를 둔다. train 과 held-out 은 겹치면 안 된다.
- **상한**은 지킴이(Governor)의 `calls` 예산으로 지킨다 -- 상한에서 멈추고(status "capped") base 를 둔다. 분당 한도 대기는 잔다
  (`max_wait_s` 보다 길면 status "paused").
- **캐시**: 열쇠 = sha256(프롬프트 글 + NUL + 사례 id). 맞으면 부르지 않는다. 답은 받자마자 run_dir/cache.jsonl 에 적는다.
- **이어 하기**: run_dir/run.json(설정 지문 · 지킴이 상태 · 결과)과 캐시로 다시 돌면 같은 결과, 되풀이 부름 없음. 설정이 다르면 RunMismatch.
- **Telemetry**: 부름마다 L0 llm.request · llm.response(또는 llm.error), 실행(토막)마다 run.start · run.end(run_dir/l0.jsonl).
  토막마다 run_id 가 다르다(`optimize:<spec id>:<토막>`). usage 는 `usage_format`(Telemetry 의 꼴 이름)을 줄 때만 싣는다.
  rlo 원장(run_dir/ledger.jsonl)은 사례 평가마다 한 줄 · 실행마다 한 줄 -- 프롬프트 글 · 답 글은 싣지 않는다.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import os
import pathlib
import time
from typing import Callable

from .governor import Governor, usage_tokens
from .prompt_spec import LABEL, Check, PromptSpec, canonical, digest

RUN_SCHEMA = "rlo-optimize-run/1"
KINDS = ("deterministic", "llm_judge")


class RunMismatch(ValueError):
    """run_dir 의 실행 기록이 지금 설정(명세 · 변형 · 나눔 · 여유폭 · 지표)과 다르다."""


@dataclasses.dataclass(frozen=True)
class Metric:
    name: str
    fn: Callable                     # (Check, case) -> [0, 1]
    kind: str = "deterministic"      # deterministic | llm_judge


@dataclasses.dataclass
class OptimizeResult:
    status: str                      # done | capped | paused
    adopted: bool
    chosen: str                      # 채택한 변형 id(아니면 base 의 id)
    base: str
    candidate: "str | None"          # held-out 에서 잰 변형(없으면 None)
    train: dict
    held_out: dict
    margin: float
    calls: int                       # 이 실행 기록에서 모형을 부른 총수(이어 한 토막 모두)
    cache_hits: int                  # 이 토막에서 캐시로 넘긴 수
    reason: str

    def summary(self) -> dict:
        """판정에 드는 칸(토막마다 다른 cache_hits 는 뺀다)."""
        return {k: v for k, v in dataclasses.asdict(self).items() if k != "cache_hits"}


class _Stop(Exception):
    def __init__(self, status, reason):
        super().__init__(reason)
        self.status, self.reason = status, reason


def _text_of(resp) -> "tuple[str, object]":
    if isinstance(resp, str):
        return resp, None
    if isinstance(resp, dict) and isinstance(resp.get("text"), str):
        return resp["text"], resp.get("usage")
    raise TypeError("모형 함수는 글 또는 {text, usage} 를 돌려줘야 한다")


def cache_key(text: str, case_id: str) -> str:
    return hashlib.sha256(text.encode("utf-8") + b"\x00" + case_id.encode("utf-8")).hexdigest()


class _Run:
    def __init__(self, spec, base, candidates, cases, split, metrics, margin, governor, model, model_name, run_dir,
                 sleep, clock, provider_name, est_tokens, max_wait_s, usage_format):
        self.spec, self.model, self.gov = spec, model, governor
        self.model_name = model_name or governor.default_model
        self.sleep, self.clock, self.provider_name = sleep, clock, provider_name
        self.usage_format = usage_format             # Telemetry usage 꼴(anthropic · openai · gemini · otel). None 이면 싣지 않는다
        self.est_tokens, self.max_wait_s, self.margin = est_tokens, max_wait_s, margin
        self.metrics = metrics
        self.base, self.candidates = base, candidates
        self.cases = {c["id"]: c for c in cases}
        self.split = split
        self.dir = pathlib.Path(run_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.fp = {"spec": spec.ref, "variants": [digest(v) for v in [base, *candidates]],
                   "cases": digest([[c["id"], c["inputs"], c.get("label")] for c in cases]),
                   "split": split, "margin": margin, "metrics": [[m.name, m.kind] for m in metrics],
                   "model": self.model_name}
        self.rec_path, self.cache_path = self.dir / "run.json", self.dir / "cache.jsonl"
        self.record = self._load_record()
        self.cache = self._load_cache()
        self.hits = 0
        self.calls = self.record["calls"]
        from telemetry.ledger import JsonlSink
        from telemetry.recorder import Recorder
        seg = self.record["segments"]
        self.rec = Recorder(f"optimize:{spec.raw['id']}:{seg}", JsonlSink(self.dir / "l0.jsonl"),
                            source="inproc:rlo.optimize", wall=lambda: self.clock() * 1000)
        self.call_index = 0

    # ── 기록 ──
    def _load_record(self) -> dict:
        if self.rec_path.exists():
            r = json.loads(self.rec_path.read_text(encoding="utf-8"))
            if r.get("schema") != RUN_SCHEMA or r.get("fingerprint") != json.loads(canonical(self.fp)):
                raise RunMismatch("run_dir 의 실행 기록이 지금 설정과 다르다 -- 새 run_dir 을 쓴다")
            self.gov.load(r["governor"])
            r["segments"] += 1
            return r
        return {"schema": RUN_SCHEMA, "fingerprint": json.loads(canonical(self.fp)), "segments": 0, "calls": 0,
                "governor": self.gov.to_dict(), "status": "running", "result": None}

    def save(self) -> None:
        self.record.update(calls=self.calls, governor=self.gov.to_dict())
        tmp = self.rec_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.record, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp, self.rec_path)

    def _load_cache(self) -> dict:
        out = {}
        if self.cache_path.exists():
            for line in self.cache_path.read_text(encoding="utf-8").splitlines():
                try:
                    x = json.loads(line)
                except ValueError:
                    continue                                  # 끊긴 마지막 줄(죽음) -- 그 부름은 없던 것이다
                out[x["key"]] = x["answer"]
        return out

    def _remember(self, key, answer) -> None:
        with open(self.cache_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"key": key, "answer": answer}, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
        self.cache[key] = answer

    def ledger(self, **row) -> None:
        with open(self.dir / "ledger.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    # ── 부름 ──
    def answer(self, text: str, case_id: str) -> "tuple[str, bool]":
        key = cache_key(text, case_id)
        if key in self.cache:
            self.hits += 1
            return self.cache[key], True
        while True:
            g = self.gov.try_acquire(self.est_tokens, self.model_name)
            if g.ok:
                break
            if math.isinf(g.wait_s):
                raise _Stop("capped", "call cap reached" if self.gov.remaining_calls(self.model_name) == 0
                            else "quota exhausted for the day")
            if g.wait_s > self.max_wait_s:
                raise _Stop("paused", f"rate window wait {g.wait_s:.0f}s > max_wait_s")
            self.sleep(g.wait_s)
        idx, self.call_index = self.call_index, self.call_index + 1
        self.rec.llm_request(idx, self.provider_name, self.model_name, prompt_chars=len(text))
        try:
            resp = self.model(text)
        except Exception as e:
            status, body, headers = Governor.rate_limit_parts(e)
            self.rec.llm_error(idx, self.provider_name, http_status=status, body=body, headers=headers,
                               exception=type(e).__name__, table=self.gov.provider)
            self.calls += 1
            self.save()
            if Governor.is_rate_limit(e):
                wait = self.gov.on_rate_limit(e, model=self.model_name)
                if wait > self.max_wait_s:
                    raise _Stop("paused", "rate limited") from None
                self.sleep(wait)
                return self.answer(text, case_id)
            raise
        out, usage = _text_of(resp)
        self.calls += 1
        self.gov.observe(g.ticket, usage, model=self.model_name)
        self._remember(key, out)                               # 받자마자 -- 죽어도 되풀이하지 않게
        self.save()
        self.rec.llm_response(idx, self.provider_name, usage=usage if self.usage_format else None,
                              usage_format=self.usage_format or "otel", output_text_chars=len(out))
        return out, False

    def score(self, variant: dict, part: str) -> float:
        total = 0.0
        ids = self.split[part]
        for cid in ids:
            case = self.cases[cid]
            c = self.spec.compile(variant, case["inputs"])
            text, cached = self.answer(c.text, cid)
            chk: Check = c.check(text)
            vals = []
            for m in self.metrics:
                v = m.fn(chk, case)
                if not isinstance(v, (int, float)) or isinstance(v, bool) or not 0 <= v <= 1:
                    raise ValueError(f"지표 {m.name}: [0, 1] 의 수를 돌려줘야 한다")
                vals.append(float(v))
            s = sum(vals) / len(vals)
            total += s
            self.ledger(kind="case", variant=variant["id"], case=cid, split=part, key=cache_key(c.text, cid),
                        prompt_sha256=c.sha256, cached=cached, ok=chk.ok, problems=list(chk.problems), score=s)
        return total / len(ids)


def _check_inputs(spec, base, candidates, cases, split, metrics, margin, governor, model_name):
    if not metrics:
        raise ValueError("지표가 없다")
    for m in metrics:
        if not isinstance(m, Metric) or m.kind not in KINDS or not callable(m.fn) or not LABEL.match(m.name):
            raise ValueError(f"지표는 Metric(name, fn, kind ∈ {KINDS}) 이어야 한다")
    if all(m.kind == "llm_judge" for m in metrics):
        raise ValueError("LLM 판정만으로는 채점하지 않는다 -- 결정론 지표가 하나는 있어야 한다(LLM 의 답은 Opinion)")
    if not (isinstance(margin, (int, float)) and not isinstance(margin, bool) and margin >= 0):
        raise ValueError("margin: 0 이상의 수")
    ids = [v["id"] for v in [base, *candidates]]
    if len(set(ids)) != len(ids):
        raise ValueError("변형 id 가 겹친다")
    for v in [base, *candidates]:
        spec.variant(v)                                         # VariantError
    case_ids = [c["id"] for c in cases]
    if len(set(case_ids)) != len(case_ids) or not all(isinstance(i, str) and LABEL.match(i) for i in case_ids):
        raise ValueError("사례 id 는 겹치지 않는 라벨이어야 한다")
    if not isinstance(split, dict) or set(split) != {"train", "held_out"}:
        raise ValueError("split: {train, held_out}")
    tr, ho = split["train"], split["held_out"]
    if not tr or not ho or set(tr) & set(ho):
        raise ValueError("split: train 과 held_out 은 비지 않고 겹치지 않아야 한다")
    unknown = (set(tr) | set(ho)) - set(case_ids)
    if unknown or len(set(tr)) != len(tr) or len(set(ho)) != len(ho):
        raise ValueError(f"split: 모르는 사례 또는 겹친 id {sorted(unknown)}")
    m = model_name or governor.default_model
    if math.isinf(governor.remaining_calls(m)):
        raise ValueError(f"지킴이 예산 {m!r} 에 calls 상한이 없다 -- 상한 없이는 돌지 않는다")


def optimize(spec, candidates, cases, *, model, metrics, split, governor: Governor, run_dir, margin: float = 0.05,
             base: "dict | None" = None, model_name: "str | None" = None, sleep=time.sleep, clock=time.time,
             provider_name: str = "injected", usage_format: "str | None" = None, est_tokens: int = 0,
             max_wait_s: float = 300.0) -> OptimizeResult:
    """변형 탐색. 결과를 돌려줄 뿐 프롬프트를 쓰거나 내보내지 않는다."""
    spec = spec if isinstance(spec, PromptSpec) else PromptSpec.load(spec)
    base = base or {"id": "base"}
    candidates, cases, metrics = list(candidates), list(cases), list(metrics)
    if isinstance(split, dict) and set(split) == {"train", "held_out"}:
        split = {"train": list(split["train"]), "held_out": list(split["held_out"])}
    _check_inputs(spec, base, candidates, cases, split, metrics, margin, governor, model_name)
    run = _Run(spec, base, candidates, cases, split, metrics, margin, governor, model, model_name, run_dir, sleep, clock,
               provider_name, est_tokens, max_wait_s, usage_format)
    run.save()                                                  # 기록은 첫 부름 전에 -- 첫 부름에서 죽어도 토막을 센다
    run.rec.run_start(model=run.model_name, provider=provider_name)
    train, held, cand = {}, {}, None
    status, reason, adopted, chosen = "done", "", False, base["id"]
    try:
        for v in [base, *candidates]:
            train[v["id"]] = run.score(v, "train")
        better = [v for v in candidates if train[v["id"]] > train[base["id"]]]
        if not better:
            reason = "no variant beats the base on train"
        else:
            best = max(better, key=lambda v: train[v["id"]])     # 같으면 앞의 것(max 는 처음 것을 둔다)
            cand = best["id"]
            held[base["id"]] = run.score(base, "held_out")
            held[cand] = run.score(best, "held_out")
            gain = held[cand] - held[base["id"]]
            adopted = gain >= margin
            chosen = cand if adopted else base["id"]
            reason = (f"held-out gain {gain:.4f} >= margin {margin}" if adopted
                      else f"held-out gain {gain:.4f} < margin {margin}")
    except _Stop as s:
        status, reason, adopted, chosen = s.status, s.reason, False, base["id"]
    result = OptimizeResult(status, adopted, chosen, base["id"], cand, train, held, margin, run.calls, run.hits, reason)
    run.record.update(status=status, result=result.summary())
    run.save()
    run.rec.run_end(api_calls=run.call_index, is_error=status != "done", terminal_reason=status)
    run.ledger(kind="run", segment=run.record["segments"], status=status, adopted=adopted, chosen=chosen, calls=run.calls,
               cache_hits=run.hits, train=train, held_out=held, margin=margin)
    return result
