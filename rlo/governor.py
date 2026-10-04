"""분당 한도 지킴이(CMD-K12 S1) -- 모형마다 분당 요청 수(RPM) · 분당 토큰 수(TPM)를 미끄러지는 60 초 창으로 센다. LLM 없음.

    g = Governor({"gemini-3.1-flash-lite": {"rpm": 15, "tpm": 250_000}}, clock=time.time)
    grant = g.try_acquire(est_tokens=900)        # ok 면 창에 한 번 적는다(표). 아니면 wait_s 만큼 뒤에 다시
    resp = provider(...)                          # 부른 뒤
    g.observe(grant.ticket, usage)                # 추정 토큰을 실제 사용량으로 바꾼다
    except e: if Governor.is_rate_limit(e): g.on_rate_limit(e)    # 429 -- 서버의 retryDelay 가 이긴다

- 시계는 바꿔 끼울 수 있다(시험은 가짜 시계). 단위는 초.
- 429 의 대기: 공급자가 **선언한** 대기(Gemini `google.rpc.RetryInfo.retryDelay` · `retry-after` 헤더)를 Telemetry
  `errors.translate` 로 읽는다. 없으면 할당 힌트(`QuotaFailure.violations[].quotaId` 의 PerMinute / PerDay): 분이면 창 하나(60 초),
  날이면 오늘은 더 부르지 않는다(무한 대기 -- 기다려도 풀리지 않는 것을 바쁘게 되풀이하지 않는다). 그것도 없으면 창 하나.
- 공급자를 가리지 않는다: 예외의 `status`(MS ProviderError) · `body` · `headers` 를 읽는다. Gemini 의 429 꼴을 안다.
- 부름 상한(CMD-K15): 예산의 `calls` 는 이 지킴이가 그 모형에 허락하는 부름의 **총수**다(창이 아니다). 다 쓰면 wait_s 가
  무한이고 try_acquire 는 ok 가 아니다. 쓴 수는 to_dict · load 로 저장 · 다시 읽힌다(여러 날 이어 하는 실행의 상한).
"""
from __future__ import annotations

import collections
import dataclasses
import math
import time

WINDOW_S = 60.0
EPS = 1e-6                            # 창 끝 비교의 부동소수 여유 -- 창 끝에서 아주 작은 대기로 되풀이하지 않게


@dataclasses.dataclass(frozen=True)
class Budget:
    rpm: "int | None" = None          # 분당 요청 수. None 이면 세지 않는다
    tpm: "int | None" = None          # 분당 토큰 수(입력 + 출력). None 이면 세지 않는다
    calls: "int | None" = None        # 부름 총수 상한(CMD-K15). None 이면 세지 않는다

    @classmethod
    def of(cls, v) -> "Budget":
        if isinstance(v, Budget):
            return v
        if not isinstance(v, dict) or set(v) - {"rpm", "tpm", "calls"}:
            raise ValueError(f"예산은 {{rpm, tpm, calls}} 이어야 한다 ({v!r})")
        for k in ("rpm", "tpm", "calls"):
            x = v.get(k)
            if x is not None and (not isinstance(x, int) or isinstance(x, bool) or x <= 0):
                raise ValueError(f"예산 {k}: 0 보다 큰 정수 또는 None ({x!r})")
        return cls(v.get("rpm"), v.get("tpm"), v.get("calls"))


@dataclasses.dataclass(frozen=True)
class Grant:
    ok: bool
    wait_s: float                     # ok 면 0. 아니면 다시 물어도 되는 가장 이른 때까지(초). 무한일 수 있다
    ticket: "int | None" = None       # observe 에 넘긴다


class RateDeferred(RuntimeError):
    """예산이 비어 부르지 않았다(지킴이가 막음). 실패가 아니라 미룸이다."""

    def __init__(self, wait_s: float):
        super().__init__(f"rate budget empty -- wait {wait_s:.1f}s")
        self.wait_s = wait_s


def usage_tokens(usage) -> "int | None":
    """사용량 -> 입력 + 출력 토큰. MS Usage · OTel/anthropic dict · Gemini usageMetadata 를 안다. 모르면 None."""
    if usage is None:
        return None
    get = usage.get if isinstance(usage, dict) else (lambda k, d=None: getattr(usage, k, d))
    total = get("total_tokens", None) or get("totalTokenCount", None)
    if isinstance(total, int):
        return total
    ins = get("input_tokens", None) or get("promptTokenCount", None)
    outs = get("output_tokens", None) or get("candidatesTokenCount", None)
    if ins is None and outs is None:
        return None
    return (ins or 0) + (outs or 0)


class Governor:
    def __init__(self, budgets: dict, *, clock=time.time, provider: str = "gemini", default_model: "str | None" = None):
        if not budgets:
            raise ValueError("예산이 없다 -- {모형: {rpm, tpm}}")
        self.budgets = {m: Budget.of(b) for m, b in budgets.items()}
        self.clock, self.provider = clock, provider
        self.default_model = default_model or next(iter(self.budgets))
        self._calls = {m: collections.deque() for m in self.budgets}     # [시각, 토큰, ticket]
        self._blocked_until = {m: 0.0 for m in self.budgets}
        self._used = {m: 0 for m in self.budgets}                         # 부른 총수(calls 상한)
        self._tickets = 0

    def _model(self, model):
        m = model or self.default_model
        if m not in self.budgets:
            raise KeyError(f"예산이 없는 모형 {m!r}")
        return m

    def _prune(self, m, now):
        q = self._calls[m]
        while q and q[0][0] <= now - WINDOW_S + EPS:
            q.popleft()

    def wait_s(self, est_tokens: int = 0, model: "str | None" = None, calls: int = 1) -> float:
        """지금 calls 번 부를 수 있으려면 몇 초 기다려야 하나(0 이면 지금). 창에 적지 않는다."""
        m = self._model(model)
        now = self.clock()
        self._prune(m, now)
        b, q = self.budgets[m], self._calls[m]
        wait = max(0.0, self._blocked_until[m] - now)
        wait = 0.0 if wait <= EPS else wait
        if b.calls is not None and self._used[m] + calls > b.calls:
            return math.inf                                 # 총수를 다 썼다 -- 기다려도 늘지 않는다
        if b.rpm is not None and len(q) + calls > b.rpm:
            k = len(q) + calls - b.rpm                     # 창에서 빠져야 할 요청 수
            wait = max(wait, q[k - 1][0] + WINDOW_S - now) if k <= len(q) else math.inf
        if b.tpm is not None and q:
            used = sum(x[1] for x in q)
            need = used + max(0, est_tokens) - b.tpm
            if need > 0:
                freed, at = 0, None
                for t, tok, _ in q:                         # 오래된 것부터 빠진다
                    freed += tok
                    if freed >= need:
                        at = t
                        break
                # 다 빠져도 모자라면(추정 하나가 tpm 보다 크다) 창이 빈 뒤 하나만 보낸다 -- 영원히 막지 않는다
                at = q[-1][0] if at is None else at
                wait = max(wait, at + WINDOW_S - now)
        return wait

    def try_acquire(self, est_tokens: int = 0, model: "str | None" = None) -> Grant:
        """지금 한 번 불러도 되면 창에 적고 ok. 아니면 wait_s(무한일 수 있다)."""
        m = self._model(model)
        w = self.wait_s(est_tokens, m)
        if w > 0:
            return Grant(False, w)
        self._tickets += 1
        self._calls[m].append([self.clock(), max(0, int(est_tokens or 0)), self._tickets])
        self._used[m] += 1
        return Grant(True, 0.0, self._tickets)

    def remaining_calls(self, model: "str | None" = None) -> float:
        """calls 상한까지 남은 부름 수(상한이 없으면 무한)."""
        m = self._model(model)
        c = self.budgets[m].calls
        return math.inf if c is None else max(0, c - self._used[m])

    def observe(self, ticket: "int | None", usage, model: "str | None" = None) -> None:
        """부른 뒤 실제 사용량으로 추정을 바꾼다. 사용량을 모르면 추정을 둔다."""
        tok = usage_tokens(usage)
        if ticket is None or tok is None:
            return
        for x in self._calls[self._model(model)]:
            if x[2] == ticket:
                x[1] = tok
                return

    # ── 저장 · 다시 읽기(CMD-K12 S6) -- 창은 절대 시각(시계의 단위)으로 남는다. 무한 대기는 "inf" ──
    def to_dict(self) -> dict:
        return {"windows": {m: [[t, tok] for t, tok, _ in q] for m, q in self._calls.items()},
                "blocked_until": {m: ("inf" if math.isinf(b) else b) for m, b in self._blocked_until.items()},
                "used": dict(self._used)}

    def load(self, d: dict) -> None:
        """to_dict 의 꼴을 다시 싣는다. 예산 밖 모형은 받지 않는다(설정이 바뀌었으면 알린다)."""
        if not isinstance(d, dict) or not {"windows", "blocked_until"} <= set(d) <= {"windows", "blocked_until", "used"}:
            raise ValueError("지킴이 상태: {windows, blocked_until[, used]} 이어야 한다")
        unknown = (set(d["windows"]) | set(d["blocked_until"]) | set(d.get("used", {}))) - set(self.budgets)
        if unknown:
            raise ValueError(f"지킴이 상태에 예산 밖 모형 {sorted(unknown)}")
        for m, rows in d["windows"].items():
            self._calls[m] = collections.deque()
            for t, tok in rows:
                self._tickets += 1
                self._calls[m].append([float(t), int(tok), self._tickets])
        for m, b in d["blocked_until"].items():
            self._blocked_until[m] = math.inf if b == "inf" else float(b)
        for m, n in d.get("used", {}).items():
            if not isinstance(n, int) or isinstance(n, bool) or n < 0:
                raise ValueError(f"지킴이 상태 used[{m}]: 0 이상의 정수 ({n!r})")
            self._used[m] = n

    @staticmethod
    def rate_limit_parts(exc) -> "tuple[int | None, dict | None, dict | None]":
        """예외 -> (HTTP 상태, 본문, 헤더). MS ProviderError 는 status · body · headers 를 싣는다."""
        status = getattr(exc, "status", None)
        body = getattr(exc, "body", None)
        headers = getattr(exc, "headers", None)
        if status is None and isinstance(body, dict) and isinstance(body.get("error"), dict):
            status = body["error"].get("code")
        return status, body if isinstance(body, dict) else None, headers if isinstance(headers, dict) else None

    @classmethod
    def is_rate_limit(cls, exc) -> bool:
        status, body, _ = cls.rate_limit_parts(exc)
        rpc = (body or {}).get("error", {}).get("status") if isinstance((body or {}).get("error"), dict) else None
        return status == 429 or rpc == "RESOURCE_EXHAUSTED"

    def on_rate_limit(self, exc=None, *, model: "str | None" = None, status=None, body=None, headers=None) -> float:
        """429 를 받았다: 다음에 불러도 되는 가장 이른 때를 정하고, 지금부터의 대기(초)를 돌려준다."""
        if exc is not None:
            status, body, headers = self.rate_limit_parts(exc)
        m = self._model(model)
        from telemetry.errors import translate
        t = translate(self.provider, status, body, headers)
        if t.retry_after_ms is not None:
            wait = t.retry_after_ms / 1000
        else:
            wait = {"day": math.inf, "minute": WINDOW_S}.get(quota_period(body), WINDOW_S)
        self._blocked_until[m] = max(self._blocked_until[m], self.clock() + wait)
        return wait


def quota_period(body) -> "str | None":
    """Gemini QuotaFailure 의 quotaId 에서 할당 주기: 'minute' · 'day' · None."""
    err = body.get("error") if isinstance(body, dict) else None
    for d in (err or {}).get("details") or []:
        if isinstance(d, dict) and str(d.get("@type", "")).endswith("google.rpc.QuotaFailure"):
            for v in d.get("violations") or []:
                qid = str(v.get("quotaId", "")) if isinstance(v, dict) else ""
                if "PerDay" in qid:
                    return "day"
                if "PerMinute" in qid:
                    return "minute"
    return None


def gemini_429(retry_delay: "str | None" = "7s", per: str = "Minute") -> dict:
    """Gemini API 의 429 본문 꼴(시험 · 예시용). retry_delay 가 None 이면 RetryInfo 를 싣지 않는다."""
    details = [{"@type": "type.googleapis.com/google.rpc.QuotaFailure",
                "violations": [{"quotaMetric": "generativelanguage.googleapis.com/generate_content_free_tier_requests",
                                "quotaId": f"GenerateRequestsPer{per}PerProjectPerModel-FreeTier", "quotaValue": "15"}]}]
    if retry_delay is not None:
        details.append({"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": retry_delay})
    return {"error": {"code": 429, "message": "You exceeded your current quota.", "status": "RESOURCE_EXHAUSTED",
                      "details": details}}
