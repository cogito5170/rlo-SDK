# rlo-SDK

일곱 층(Telemetry · Sensor · DC · MS · action · guard · health)을 **한 입구**로 묶는 SDK. 배포 이름 `rlo-sdk`, import 이름 `rlo`.
설계는 action `720b7d9` [`docs/SDK.md`](https://github.com/cogito5170/action/blob/720b7d9/docs/SDK.md), 결정은 baseline BD-119 · BD-120.

```
pip install "git+https://github.com/cogito5170/rlo-SDK@<sha>"            # 입구 + 훅 어댑터
pip install "rlo-sdk[sensor] @ git+https://github.com/cogito5170/rlo-SDK@<sha>"   # + Sensor
python -m rlo.example                                                   # 예시 세계에서 한 바퀴(shadow · enforce)
```

Python 3.10 이상. 의존은 일곱 저장소의 **커밋 sha 고정**뿐이다(PyPI 에는 올리지 않는다).

## 한 바퀴

```python
from rlo import Autonomy

a = Autonomy.from_spec(spec, observations, actions=tools, llm=provider)   # 세계 · 행동 · LLM 은 반드시 받는다
a.open_session("s", {"token_budget": 1000})
r = a.handle("srv07 을 throttle", queries=spec["queries"])
r.outcome, r.decision_id, r.guards, r.executions, r.verifications      # 결정 → Guard → 실행기 → VERIFY
a.close_windows()                                                      # 창이 닫힌 VERIFY 마감
```

전체 예는 [`rlo/example.py`](rlo/example.py) 다. MS 패키지가 싣는 예시 세계(`ms/examples/datacenter.json`)를 쓰고, 처리기를 주지 않았으므로 throttle 은 모의로 돈다(부작용 없음).

## 꽂는 자리

| 자리 | `Autonomy` 인자 | 기본 | 꼴 · 이음매 |
|---|---|---|---|
| 세계 | 첫 인자 · `from_spec(spec, observations)` | — (반드시) | MS `StateManager`, 또는 MS 세계 명세 dict + 관측 dict 목록 |
| 행동 = 도구 처리기 + 행동 명세 | `actions=` | `from_spec` 은 명세의 `tools` | 도구 dict: `name · target_model · risk · params · preconditions · postcondition · window_ms · description` (`action-spec/1`) + 처리기 `(target, args) -> [{signal, value[, entity, ts]}]`. 처리기가 없으면 MS effect 틀(모의) |
| LLM 공급자 | `llm=` | — (반드시) | MS `LLMProvider`(`make_provider(name)` · `CallableProvider`) 또는 글 → 글 함수 |
| 목적 | `purpose=` | `"context_runtime"` | DC 내장 목적 이름. 새 목적을 꽂는 자리는 아직 없다 |
| 문턱(Model) | 세계 명세 안 | 명세 그대로 | MS 세계 모형 `models[].derived`. 사용량 문턱은 MS 안에 고정돼 있어 지금은 꽂을 수 없다 |
| `guard_mode` | `guard_mode=` | `"shadow"`(BD-118) | `"shadow"` 기록만 · `"enforce"` Arbiter ALLOW ∧ Guard ALLOW 일 때만 실행 |
| 허가 | `grants=` | `()` | 도구 이름들 |
| L0 기록 위치 | `l0=` | 끔 | 경로(JSONL) 또는 `.write(ev)` 하는 sink(Telemetry `MemorySink` 등) |
| 결정 원장 | `ledger=` | 끔 | JSONL 경로 |
| `$run.*` 읽기 | `run_state=` | 없음 | `.read(entity, state)` · `.subjects(run)`. Sensor 어댑터는 아직 없다 |
| Guard 위험 등급 | — | — | **아직 꽂을 수 없다**(MS 가 `risky` 없이 짓는다 → MS 의 일) |

## 지키는 것

- **snapshot 길은 내지 않는다.** 의도 · Guard · 실행기 · VERIFY 는 결정 문맥(DC) id 가 있을 때만 돈다. 그래서 `Autonomy` 는 언제나 DC 길(`MSStateReader`)로 돌고, 그것을 끄는 인자가 없다. 누가 런타임에서 끄면 `handle` 이 거절한다.
- **guard · health 는 필수다.** MS 런타임은 guard · 실행기 · VERIFY 를 불러오지 못하면 조용히 빼고 돈다. `Autonomy` 는 그때 서지 않는다(shadow 에서도).
- **훅은 허가를 넓히지 않는다.** `rlo.hooks` 는 Guard ALLOW 에도 `"allow"` 를 내지 않고 `{}` 를 낸다(`"allow"` 는 사용자의 권한 확인을 건너뛴다). enforce 에서 ALLOW 가 아니면 deny, shadow 는 늘 `{}`.

## 공개 경계와 판본

공개는 `Autonomy` · `Result` · `versions()` · `rlo.hooks` 와 위 꽂는 자리의 꼴, 그리고 동결 계약이다. 나머지(MS Runtime 속 · DC builder · Sensor 엔진 …)는 내부다.

```python
import rlo
rlo.versions()
# {"sdk": "0.1.0", "pins": {저장소: sha}, "extras": {"sensor": {...}}, "installed": {저장소: pip 가 받은 sha},
#  "contracts": {"action-contract": "action-contract/1", "action-spec": "action-spec/1", "action-model": "action-model/1",
#                "guard-result": "guard-result/1", "validation-result": "validation-result/1",
#                "verification-record": "verification-record/1", "state-export": "llmsensor.state-export/2",
#                "l0-telemetry": "l0-telemetry/1"}}
```

SDK 판본은 semver 이고, 지금은 `0.x` 다. 계약이 호환되지 않게 바뀌면 major, 꽂는 자리 · 공개 이름을 더하면 minor, 계약을 바꾸지 않는 sha 갱신이면 patch 다.

### 고정 목록

원본은 [`rlo/_pins.py`](rlo/_pins.py) 이고, `pyproject.toml` 이 글자까지 같아야 한다(시험).

| 저장소 | 배포 | sha | |
|---|---|---|---|
| Telemetry | `l0-telemetry` | `89d2887` | 필수 |
| DC | `dc` | `b55ff04` | 필수 |
| MS | `ms` | `74a8585` | 필수 |
| action | `action-contract` | `3995fdb` | 필수. stage-3 머리 `2f4791e` 와 패키지 코드가 같다. MS · guard · health 가 `3995fdb` 로 고정해 같은 sha 를 쓴다(다르면 pip 가 설치하지 못한다) |
| guard | `guard` | `be871b9` | 필수 |
| health | `health` | `afcff39` | 필수 |
| Sensor | `llmsensor` | `a073e77` | `[sensor]` |

## 시험

```
pip install -r <(python -c "import rlo._pins as p; print('\n'.join(p.requirement(x) for x in [*p.REQUIRED.values(), *p.EXTRAS['sensor'].values()]))")
python -B -m unittest discover -s tests -t .      # 시험
python -B eval/mutation.py                         # 변이: 모두 RED 여야 한다
```

## 아직 하지 않은 것

- 훅 판정 `judge` 를 실제 상태(transcript → Telemetry → Sensor → DC → Guard)로 잇기 — S2-7b.
- API(서비스) — OQ-19 · OQ-23 이 먼저다.
