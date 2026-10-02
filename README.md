# rlo-SDK

일곱 층(Telemetry · Sensor · DC · MS · action · guard · health)을 **한 입구**로 묶는 SDK. 배포 이름 `rlo-sdk`, import 이름 `rlo`.
설계는 action `9642bb3` [`docs/SDK.md`](https://github.com/cogito5170/action/blob/9642bb3/docs/SDK.md), 결정은 baseline BD-119 · BD-120.

```
pip install "git+https://github.com/cogito5170/rlo-SDK@<sha>"            # 입구 + 훅 어댑터
pip install "rlo-sdk[sensor] @ git+https://github.com/cogito5170/rlo-SDK@<sha>"   # + Sensor (훅에 필요)
python -m rlo.example                                                   # 예시 세계에서 한 바퀴(shadow · enforce)
python -m rlo.example_hooks                                             # 기록된 transcript 로 훅 판정(명령 훅 길)
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
| Guard 위험 등급 | `risky=` | guard 기본(external · irreversible) | D 가 막는 위험 등급들(MS `Runtime(risky=)`, CMD-M26) |

## 지키는 것

- **snapshot 길은 내지 않는다.** 의도 · Guard · 실행기 · VERIFY 는 결정 문맥(DC) id 가 있을 때만 돈다. 그래서 `Autonomy` 는 언제나 DC 길(`MSStateReader`)로 돌고, 그것을 끄는 인자가 없다. 누가 런타임에서 끄면 `handle` 이 거절한다.
- **guard · health 는 필수다.** MS 런타임은 guard · 실행기 · VERIFY 를 불러오지 못하면 조용히 빼고 돈다. `Autonomy` 는 그때 서지 않는다(shadow 에서도).
- **훅은 허가를 넓히지 않는다.** `rlo.hooks` 는 Guard ALLOW 에도 `"allow"` 를 내지 않고 `{}` 를 낸다(`"allow"` 는 사용자의 권한 확인을 건너뛴다). enforce 에서 ALLOW 가 아니면 deny, shadow 는 늘 `{}`.

## 공개 경계와 판본

공개는 `Autonomy` · `Result` · `versions()` · `rlo.hooks` 와 위 꽂는 자리의 꼴, 그리고 동결 계약이다. 나머지(MS Runtime 속 · DC builder · Sensor 엔진 …)는 내부다.

```python
import rlo
rlo.versions()
# {"sdk": "rlo-sdk/0.4.1", "pins": {저장소: sha}, "extras": {"sensor": {...}}, "installed": {저장소: pip 가 받은 sha},
#  "contracts": {"action-contract": "action-contract/1", "action-spec": "action-spec/1", "action-model": "action-model/1",
#                "guard-result": "guard-result/1", "validation-result": "validation-result/1",
#                "verification-record": "verification-record/1", "state-export": "llmsensor.state-export/2",
#                "l0-telemetry": "l0-telemetry/1"}}
```

SDK 판본은 semver 이고, 지금은 `0.x` 다. 계약이 호환되지 않게 바뀌면 major, 꽂는 자리 · 공개 이름을 더하면 minor, 계약을 바꾸지 않는 sha 갱신이면 patch 다.

### 고정 목록

원본은 [`rlo/_pins.py`](rlo/_pins.py) 이고, `pyproject.toml` 이 글자까지 같아야 한다(시험). 깔린 고정 배포가 서로를 요구하는 글자도 이 목록과 같아야 한다(`PinGraph` 시험 — 다르면 pip 가 `ResolutionImpossible`).

MS 의 sha 없는 `ms[sensor]` extras 는 `rlo-sdk[sensor]` 와 함께 깔면 설치가 실패했다(잰 것, K2) → MS 가 뺐다(M27).

| 저장소 | 배포 | sha | |
|---|---|---|---|
| Telemetry | `l0-telemetry` | `35e8119` | 필수 (T18 `tool.start.tool_use_id`) |
| DC | `dc` | `526f2fb` | 필수 (D18 목적 `agent_tool_call`) |
| MS | `ms` | `19d850e` | 필수 (M26 `risky=` · M27 sha 없는 `ms[sensor]` 를 뺐다) |
| action | `action-contract` | `3995fdb` | 필수. stage-3 머리 `2f4791e` 와 패키지 코드가 같다. MS · guard · health 가 `3995fdb` 로 고정해 같은 sha 를 쓴다(다르면 pip 가 설치하지 못한다) |
| guard | `guard` | `be871b9` | 필수 |
| health | `health` | `afcff39` | 필수 |
| Sensor | `llmsensor` | `97961e9` | `[sensor]`. S26 — Telemetry 고정을 `35e8119` 로 올린 커밋 |

## 시험

```
pip install -r <(python -c "import rlo._pins as p; print('\n'.join(p.requirement(x) for x in [*p.REQUIRED.values(), *p.EXTRAS['sensor'].values()]))")
python -B -m unittest discover -s tests -t .      # 시험
python -B eval/mutation.py                         # 변이: 모두 RED 여야 한다
```

## 훅 — 남의 에이전트에 붙기

`rlo.hooks` 는 Claude Code · Agent SDK 의 도구 호출을 Guard 로 판정한다(`[sensor]` 가 필요하다).

```
PreToolUse ─► transcript 다시 거둠(Telemetry cc_jsonl) ─► 지금 호출을 뺌 ─► Sensor from_l0 ─► DC agent_tool_call ─► guard ─► {} | deny
Stop · SessionEnd ─► 거둠(판정 없음)        PostToolUse(Failure) ─► 관측만(거두지 않음, tool_response 는 L0 로 들이지 않는다)
```

- **기본 목적은 DC `agent_tool_call`**(BD-123): 필수는 `agent.execution_health` 하나, 나머지는 선택. `purpose=` · `--purpose` 로 바꿀 수 있다.
- **지금 호출을 빼고 평가한다**(BD-124): 훅 입력 `tool_use_id` 와 같은 `tool.start` 와 그 `tool.end` 를 뺀다. 그 줄이 훅 순간
  transcript 에 있을지는 결정적이지 않다(T19). 나란히 부른 **다른** 호출은 남긴다(결과를 아직 못 본 앞 호출 → 닫는 쪽).
- **깔기 · 떼기**(`~/.claude/settings.json`, `--settings` 로 바꾼다):

  ```
  python -m rlo.hooks install-hook --model <action-model/1 JSON> [--mode shadow|enforce] [--grant Bash] [--record <JSONL>]
  python -m rlo.hooks uninstall-hook
  ```

  PreToolUse(matcher `*`) · Stop · SessionEnd 에 **사건마다 하나만** 둔다(다시 깔면 그 자리에서 바꾼다). 남의 훅 · 다른 칸은
  건드리지 않는다. 바뀔 때만 쓰고, 쓰기 전 설정을 `<설정>.bak-rlo` 로 남긴다. 설정이 JSON 이 아니거나 모형이 읽히지 않으면
  아무것도 쓰지 않고 실패한다(종료 1).
- **Claude Code 명령 훅을 손으로**: [`examples/claude_code_settings.json`](examples/claude_code_settings.json). 명령은
  `python -m rlo.hooks --model <action-model/1 JSON> --mode shadow|enforce --grant <도구> [--record <JSONL>]`.
  모형의 예는 `rlo/data/cc_tools_model.json`(`cc-tools-example-2`): Bash · Read · Write 와, 실제 Claude Code 에서 잰 칸으로
  Edit(local: `file_path` · `old_string` · `new_string` · `replace_all`) · Grep(read: `path` · `pattern`).
- **Agent SDK(Python)**: [`examples/agent_sdk.py`](examples/agent_sdk.py). `guard_hooks(model, mode=…).callback` 을
  PreToolUse · PostToolUse · PostToolUseFailure · Stop 에 건다. Python SDK 에는 SessionEnd 가 없다(설정 파일 명령 훅으로만).
- 응답: shadow 는 늘 `{}`. enforce 는 Guard ALLOW 가 아니면 deny. **`"allow"` 는 내지 않는다.** 판정 오류 · 설정 오류는
  enforce 에서 deny(까닭은 예외 종류만), shadow 에서 `{}`. Stop · SessionEnd 는 막지 않는다.
- 모형에 없는 도구는 A1, 모르는 인자는 A4, 허가 없는 external · irreversible 은 A7 로 막힌다(enforce).
- **모형 밖 도구는 enforce 에서 막힌다.** 실제 Claude Code 6 실행(K7)에서 도구 호출 16 가운데 4 가 그랬다(Grep · Edit, 그때는
  예시 모형 밖). Glob · TodoWrite · Task · WebFetch 등은 지금도 예시 모형 밖이다. 그러니 **모형을 넓히기 전에는 shadow 로 쓴다.**
  넓히는 법: shadow 로 돌리며 `--record` 를 켜고, 기록의 `tool_name` · `tool_input_keys`(칸 이름만, 값은 싣지 않는다)를 보고
  실제로 나온 도구와 칸만 모형에 더한다. 재지 않고 추측한 칸은 A4 오판을 숨긴다.

### 판정 (잰 것 — `tests/test_hooks.py`, enforce, `--grant Bash`)

| 그 순간 | 응답 | 까닭 |
|---|---|---|
| 앞 호출 성공 · 실패 · 세션 첫 호출(지금 줄이 있든 없든) | `{}` | `execution_health` 를 안다(실패 값만으로는 막지 않는다, BD-123) |
| 나란히 부른 · 결과를 못 본 앞 호출이 있음 | deny D | `execution_health` 모름 → 문맥 불완전 |
| Read · Grep(read) · Edit(local), 잰 칸만 | `{}` | D 는 external · irreversible 만 본다 |
| 잰 적 없는 칸이 든 Edit · Grep | deny A4 | 모형에 없는 인자 |
| 모형 밖 도구(Glob · WebFetch …) | deny A1 | 모형에 없는 행동 |

실패 뒤에 막고 싶으면 운영자가 행동 명세의 사전조건(A6)으로 둔다(BD-123). `execution_control` 을 꽂으면 Claude Code
transcript 로는 완전해질 수 없어 위험 도구가 늘 D 다.

## MBA-frontend 와 함께 쓰기

[`cogito5170/MBA`](https://github.com/cogito5170/MBA) 의 `mba-frontend`(토큰 절약 앞단)와 나란히 쓸 수 있다. SDK 에 넣지 않고 **각자 깐다**(BD-125).
확인한 판: MBA `0879c2b` · rlo-sdk `0.3.0`. 재현: `python eval/with_mba.py --mba-frontend <mba-frontend>`(임시 HOME 에서만 돈다, `claude -p` 를 부르지 않는다).

| 확인한 것 | 결과 |
|---|---|
| 한 가상환경에 함께 설치(`rlo-sdk[sensor]` + `mba`) | 된다. import 이름이 겹치지 않는다(`rlo` · `mba`) |
| 붙는 사건 | MBA: `UserPromptSubmit` · `Stop`. rlo: `PreToolUse` · `Stop` · `SessionEnd`. **겹치는 것은 `Stop` 하나** |
| 깔기 순서 MBA → rlo, rlo → MBA | 어느 순서든 두 훅과 원래 사용자 훅 · 다른 칸이 모두 남는다. MBA 를 다시 깔아도 하나만 남는다 |
| 하나 떼기 | 하나를 떼면 다른 하나를 깐 뒤의 설정과 같다. 둘 다 떼면 원래 설정과 같다 |
| `Stop` 을 함께(나란히) | 둘 다 출력 없음 · 종료 0 — 혼자일 때와 같다. MBA 원장의 stop 기록도 같다. 서로 막지 않는다 |
| `.bak-mba` · `.bak-rlo` | rlo 는 `.bak-mba` 를 만들거나 고치지 않고 자기 백업은 `.bak-rlo` 에 둔다. 다만 **rlo 를 먼저 깔고 MBA 를 깔면 `.bak-mba` 에 rlo 항목이 든다**(MBA 가 바꾸기 전 설정을 백업한다). rlo 를 뗀 뒤 `.bak-mba` 로 되돌리면 rlo 훅이 다시 생긴다 |

rlo 는 `python -m rlo.hooks install-hook | uninstall-hook` 으로 깔고 뗐다(위 확인, 27/27). rlo 의 백업은 `.bak-rlo` 라 MBA 의 `.bak-mba` 와 겹치지 않는다.
실제 Claude Code 실행으로는 재보지 않았다.

## 아직 하지 않은 것

- API(서비스) — OQ-19 · OQ-23 이 먼저다.
