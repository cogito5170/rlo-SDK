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
# {"sdk": "rlo-sdk/0.11.1", "pins": {저장소: sha}, "extras": {"sensor": {...}}, "installed": {저장소: pip 가 받은 sha},
#  "contracts": {"action-contract": "action-contract/1", "action-spec": "action-spec/1", "action-model": "action-model/1",
#                "guard-result": "guard-result/1", "validation-result": "validation-result/1",
#                "verification-record": "verification-record/1", "state-export": "llmsensor.state-export/2",
#                "l0-telemetry": "l0-telemetry/1"}}
```

SDK 판본은 semver 이고, 지금은 `0.x` 다. 계약이 호환되지 않게 바뀌면 major, 꽂는 자리 · 공개 이름을 더하면 minor, 계약을 바꾸지 않는 sha 갱신이면 patch 다.

### 고정 목록

원본은 [`rlo/_pins.py`](rlo/_pins.py) 이고, `pyproject.toml` 이 글자까지 같아야 한다(시험). 깔린 메타데이터와 깔린 고정 배포가 서로를 요구하는 것도 이 목록과 같아야 한다(`Manifest` · `PinGraph` 시험 — 다르면 pip 가 `ResolutionImpossible`). 메타데이터는 빌드 도구마다 글자가 달라(`action-contract @ git+…` · `action-contract@ git+…`) **뜻으로** 비교한다: 이름(PEP 503) · extras · url(sha 포함) · 표지.

MS 의 sha 없는 `ms[sensor]` extras 는 `rlo-sdk[sensor]` 와 함께 깔면 설치가 실패했다(잰 것, K2) → MS 가 뺐다(M27).

| 저장소 | 배포 | sha | |
|---|---|---|---|
| Telemetry | `l0-telemetry` | `f6c7ae2` | 필수 (T18 `tool.start.tool_use_id`) |
| DC | `dc` | `7e0ac14` | 필수 (D18 목적 `agent_tool_call`) |
| MS | `ms` | `1f1018e` | 필수 (M26 `risky=` · M27 sha 없는 `ms[sensor]` 를 뺐다) |
| action | `action-contract` | `9d6729f` | 필수. stage-3 머리 `2f4791e` 와 패키지 코드가 같다. MS · guard · health 가 `9d6729f` 로 고정해 같은 sha 를 쓴다(다르면 pip 가 설치하지 못한다) |
| guard | `guard` | `3f7b235` | 필수 |
| health | `health` | `798e7ad` | 필수 |
| Sensor | `llmsensor` | `ff17bdd` | `[sensor]`. SEN1 · SEN2 — 이어 받기(`RunState.extend`) · `evaluate="once"`, Telemetry 고정은 `f6c7ae2`(0.2.1) |

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
- **꼴이 틀린 입력도 막는다**(CMD-K10). 빈 표준입력 · JSON 아님 · 꼴 위반(객체 아님, `hook_event_name` 없음, PreToolUse 의
  `tool_name` · `tool_input` · `transcript_path` 없음이나 틀린 타입)은 enforce 에서 PreToolUse deny
  `rlo hook input error: <문제>`, shadow 에서 출력 없음. **둘 다 종료 0** 이고 `--record` 에 `input_error` 한 줄
  (문제만, 값은 없음)을 남긴다. 0 아닌 종료를 "막지 않음" 으로 보는 호스트가 있어서, 명령 훅은 그 밖의 예상 못 한 예외
  (예: 기록 파일을 쓰지 못함)도 종료 0 · enforce deny(`rlo hook error: <종류>`)로 닫는다. 전에는 빈 · JSON 아닌 입력이
  종료 1, `hook_event_name` 없는 입력이 출력 없음(허락)이었다.
- **낡음만으로 막혔으면 되살리는 법을 말한다**(CMD-K10). 쉼(Sensor 기본 TTL 10 분) 뒤에는 `agent.execution_health` 가 STALE
  이라 첫 위험 도구가 D 로 막힌다. D 의 쓸 수 없는 필수 키가 **모두 STALE**(UNKNOWN · 없음이 없음)이고 걸린 규칙이 D 뿐이면,
  까닭 끝에 정해진 안내가 붙는다: `-- hint: the decision state is stale, not unavailable; make one read-only tool call to
  refresh it, then retry`. 읽기 도구(Read · Grep, 위험 등급 read)는 D 밖이라 지나고, 그 결과가 상태를 새로 관측한다 —
  그 뒤 같은 호출은 지난다(시험 `StaleHint`). 판정은 deny 그대로이고, 상태를 지어내거나 시계를 바꾸지 않는다.
  나란히 부른 호출처럼 상태를 **모르는**(UNKNOWN) D 에는 붙지 않는다.

### D 는 세션을 통로에서 끊지 않는다 (CMD-K13)

막힌 세션이 올릴 길(통로)까지 D 가 막으면 아무도 모른다. 그래서 운영자가 모형 파일에 **인자가 고정된 통로 도구**를 선언한다
(`substitutes` 와 같은 자리 — action-model/1 밖, rlo 가 읽고 떼어 낸다):

```json
"channels": [
  {"tool": "mcp__github__add_issue_comment", "args": {"owner": "cogito5170", "repo": "amp", "issue_number": 1}},
  {"tool": "mcp__github__issue_read", "args": {"owner": "cogito5170", "repo": "amp", "issue_number": 1}, "use": "read"},
  {"tool": "Bash", "argv_prefix": ["ga", "mail"]}
]
```

- **D 의 까닭이 낡음뿐이면**(쓸 수 없는 필수 키가 모두 STALE, 걸린 규칙이 D 뿐) 고정된 통로 호출은 지나간다. 기록 줄은 판정(D)을
  그대로 싣고 `allowed_while_stale: "channel:<도구>"` 를 붙인다. 다른 external 호출은 D 그대로다.
- 고정: `args` 는 값과 타입이 같아야 한다(다른 이슈 · 저장소 · `"1"` 대 `1` 은 통로가 아니다). Bash 는 명령이 `argv_prefix`
  낱말로 시작하고 셸 특수 문자(`; & | < > $ \` ( ) { } * ? ! ~`, 줄바꿈)가 없어야 한다. 고정이 없는 선언은 설정 오류다(enforce 에서 막는다).
- 상태를 **모르면**(나란히 부른 앞 호출의 결과가 없음 등 UNKNOWN) 통로도 막힌다 — 낡음이 아니다.
- `report` 대안은 지금 부를 수 있는 통로 도구를 이름 짓는다(`use: report`, 모형에 있고, 허가가 필요하면 허가됐고, D 가 막지 않는 것).
  그런 것이 없으면 이름 짓지 않는다 — 막힌 도구를 가리키지 않는다.
- **낡음이 풀리는 때 · 풀리지 않는 때**: 판정마다 transcript 전체를 다시 거둔다. `execution_health` 가 NO_FAILURE_OBSERVED 처럼
  "없음" 을 말하면(BD-74) 더 새 도구 결과(읽기 하나, 막힌 호출의 `tool_result` 도)가 그것을 새로 한다. 그러나 **풀리지 않은 옛 실패가
  있으면**(UNRESOLVED_FAILURES) 그 시각은 그 실패의 시각이다(BD-57 · BD-63) — 읽기를 몇 번 해도, 실패한 그 겨냥이 다시 성공하기
  전에는 낡음이 풀리지 않는다. 실제 세션(W1)에서 읽기가 듣지 않은 까닭이 이것이다(CMD-K13 S0). 사용자 · 모형의 글만 새로 있는 것은
  어느 경우든 새로 하지 않는다.

- **훅에서는 두 건강 상태에 TTL 이 없다**(S6, BD-246 선택 B). `execution_health` · `tool_execution_health` 의 Sensor TTL 을 훅의
  설정에서만 끈다(`TranscriptJudge(health_ttl=False)` 기본, Sensor 의 기본 설정 · BD-57/63 은 그대로). 훅은 판정마다 transcript
  전체에서 다시 세우므로 알려진 값의 나이는 모르는 것이 아니다. 그래서 풀리지 않은 옛 실패(`UNRESOLVED_FAILURES`)는 **값**으로
  DC · guard 에 가고(값만으로는 막지 않는다, BD-123 B2), D 는 상태를 **모를 때**(UNKNOWN · 없음)만 막는다. 받아들인 값: 실패 없이
  TTL 보다 오래 쉰 세션도 D 로 막히지 않는다. 예전 동작이 필요하면 `--health-ttl`(재생 · 비교용).
- **판정 기한**(S7): 호스트는 시간이 다 된 PreToolUse 훅을 막지 않고 지나가게 한다(문서: 명령 훅 기본 600 초). 그래서 판정이
  `--deadline-s`(기본 120 초, `guard_hooks(deadline_s=)`) 안에 끝나지 않으면 enforce 에서 `rlo hook deadline exceeded` 로 막고
  react 는 `report`(rule hook, cause deadline). **선언된 통로 호출만은 지나간다**(기록 `guard_deadline` · `allowed: channel:<도구>`).
  0 이면 기한 없음. 판정은 따로 된 스레드에서 돌고 부수 효과가 없어, 기한을 넘으면 버린다.
- **판정 비용 · 이어 받기**(CMD-K14 S2, Sensor `f1e45b5` 의 `RunState.extend` · `evaluate="once"`): 훅은 transcript 마다
  RunState 를 두고(`TranscriptJudge` 안, 최대 `CACHE_MAX` 개) 새 사건만 `extend` 로 넣는다. 처음 · 다른 파일(장치 · inode ·
  줄어듦 · 머리 4 KB 가 다름) · 지금 호출의 사건이 이미 들어 있을 때(나란히 부른 앞 호출)는 `evaluate="once"`(선형)로 새로 짓는다.
  값 · 유효성 · 근거 시각은 전부 넣은 것과 같다(Sensor 의 보장, 위 조건에서). **명령 훅은 부를 때마다 새 프로세스**라 캐시가 없어
  늘 once 로 짓는다 — 10 MB 실제 transcript(L0 사건 1,726)에서 한 번에 약 0.5 초(예전 약 62 초), 사건을 버리지 않는다.
  같은 프로세스에서 부르는 어댑터(Agent SDK)는 둘째 부름부터 extend 다. 남은 비용은 선형이다: Telemetry 가 transcript 전체를
  다시 읽는 것(10 MB 에 약 0.23 초)과 Sensor compat 한 번 훑기. 기록 줄의 `window.mode` 가 길을 말한다
  (`incremental` · `once:first` · `once:other_file` · `once:current_seen` · `window`). `--no-incremental` 이면 아래 사건 창.
- **사건 창**(CMD-K14 S1, 이제 **대체 길**): 이어 받기를 못 쓸 때 — 설정이 이력에 기대거나(`min_consecutive` ·
  `resource_bands` · `latency_slo`, Sensor 가 전부 다시 짓는다) `--no-incremental` — 훅은 Sensor 에 **최근 `WINDOW` 개(기본 400) L0 사건**만 넣는다(`--window N`, `TranscriptJudge(window=)`;
  0 · None 이면 전부). 창 밖이라도 결과가 없는 `tool.start`(대기 중), 창 안 `tool.end` 의 짝 `tool.start`, 남는 호출을 낸
  `llm.response`(같은 `call_index`)는 둔다 — 그래서 대기 중인 호출로 생기는 UNKNOWN → D 는 창이 있어도 같다. 기록 줄에
  `window: {mode, size, fed, dropped}`. 같은 10 MB transcript 에서 한 번에 약 2.7 초(전부 넣으면 약 62 초), K13 재생의 판정은 전부 넣은
  것과 같다. **다른 곳**: 창 밖의 일은 보이지 않는다 — 풀리지 않은 옛 실패는 값(`UNRESOLVED_FAILURES`)에서 빠지고(선택 B 에서는 판정이
  같다), 창 안에서 쓰지 않은 도구의 `tool_execution_health` 는 없다(필수가 아니다 — 처음 쓰는 도구와 같다). 창 400 에서 Sensor 시간은
  약 3 초, 800 에서 약 12 초(제곱으로 자란다).
- **실제 확인**(S4, `eval/k13_live.py` → `eval/results/k13_live_2026-10-03.json`): 실제 `claude -p` 두 번, 한 세션, enforce.
  첫 실행에서 Bash 하나가 실패(풀리지 않은 실패), 663 초 쉼(낡음 창 600 초), 이어 받은 둘째 실행의 Read · Bash · 고정 통로
  (`ga mail …`)가 모두 지나갔다. 같은 순간의 사본을 `--health-ttl` 로 다시 돌리면 Bash 는 DENY(D) 낡음 · `refresh_read`, 통로는
  `allowed_while_stale: channel:Bash` — 선택 B 가 바꾼 것이 이것이다. 결과에는 이름 · 수 · 라벨만 남는다.

### 거부마다 대안 하나 — 턴 안 ReAct (CMD-K11, `rlo/react.py`)

막기만 하면 에이전트가 멈춘다. 그래서 거부마다 **닫힌 대안표**에서 대안 하나를 골라 까닭의 **마지막 줄**에 싣는다.
판정은 deny 그대로이고, 대안을 고르는 데 자유 글 · LLM 을 쓰지 않는다.

```
guard DENY(A1): [A1] 행동 ReadNotifications 는 이 문맥에서 제안되지 않았다
-- react: {"attempt":1,"cause":"has_substitute","escalate":false,"kind":"use_tool","of":2,"rule":"A1","tool":"mcp__github__issue_read"}
```

| 막힘 (rule, cause) | 대안 `kind` | 뜻 |
|---|---|---|
| A1 `has_substitute` | `use_tool` (+ `tool`) | 같은 목적의 대체 도구로 |
| A1 `no_substitute` | `report` | 하지 않고 통로에 올린다 |
| A4 `unknown_args` | `drop_unknown_args` | 모형에 없는 인자를 빼고 한 번 |
| A7 `not_granted` | `report` | 허가는 대안으로 풀지 않는다(D 와 함께 걸려도) |
| D `stale` | `refresh_read` | 읽기 호출 하나 뒤 다시(위 K10 안내와 같은 경우) |
| D `unavailable` | `wait_previous` | 앞 호출의 결과를 기다린 뒤 다시 |
| `input` · `config` · `hook` · E | `report` | 꼴이 틀린 입력 · 설정 오류 · 훅 · 판정 오류 |
| 그 밖(A4 `bad_args`, A5 · A6 · A8) | `none` | 대안 없음 |

- `report` 이면 `escalate: true` 다. **같은 (도구, 규칙, 원인) 거부의 세 번째**(`attempt` 3 of 2)도 `report` · `escalate` 가 된다.
  되풀이는 transcript 에서 그 도구의 tool_result 에 실린 `-- react:` 줄로 센다. 그 도구가 거부 아닌 결과를 받으면 1 부터.
- `--record` 의 guard · input_error · guard_error 줄에 같은 객체가 `react` 로 들어간다. **shadow 는 기록만** 하고 아무것도 내지 않는다.
- **대체표(`substitutes`)는 운영자 몫**이다. 모형 파일에 action-model/1 칸 옆으로 둔다. rlo 가 읽고 떼어 낸 뒤 나머지를 action-model/1 로 읽는다.
  action-model/1 계약은 바꾸지 않았다(`ActionModel.from_dict` 는 모르는 칸을 받지 않는다).

  ```json
  {"schema": "action-model/1", "version": "…", "specs": [ … ],
   "substitutes": {"ReadNotifications": ["mcp__github__issue_read"]}}
  ```

- **대안은 허가를 넓히지 않는다.** `use_tool` 은 모형에 있고, external · irreversible 이면 `--grant` 로 허가된 도구만 이름 짓는다.
  그런 대체가 없으면 `report` 다. grants 는 읽기만 한다. 대체표의 꼴이 틀리면 설정 오류다(enforce 에서 막는다).
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

## 분당 한도 — 지킴이와 걸음 차례 (CMD-K12)

LLM 없는 제어기가 걸음마다 모형이 필요한지 정하고, 분당 한도(RPM · TPM)가 찼으면 도구 걸음을 계속 돌리며 모형 걸음을 세워 두었다가
창이 열리면 차례대로 다시 보낸다. 공급자를 가리지 않는다(Gemini 의 429 꼴을 안다). 429 는 실패가 아니라 미룸이다.

```python
from rlo import Governor, Scheduler, Step

gov = Governor({"gemini-3.1-flash-lite": {"rpm": 15, "tpm": 250_000}})          # 모형마다 예산(설정), 미끄러지는 60 초 창
kinds = {"schema": "rlo-step-kinds/1", "steps": {"read": "tool", "plan": "model", "render": "tool"}}
steps = [Step("s1", "read", fn=read_files),
         Step("s2", "plan", payload=lambda r: make_prompt(r["s1"]), after=("s1",), est_tokens=2_000),
         Step("s3", "render", fn=lambda r: render(r["s2"]), after=("s2",))]
report = Scheduler(steps, gov, call_gemini, kinds=kinds, ledger="ledger.jsonl").run()
# report.done · report.failed(429 로는 생기지 않는다) · report.parked(하루 할당 소진 등) · report.ledger
```

- **지킴이(`rlo.governor.Governor`)**: `try_acquire(est_tokens)` 가 ok 면 창에 적고, 아니면 `wait_s` 를 준다. 부른 뒤
  `observe(ticket, usage)` 로 추정을 실제 사용량으로 바꾼다. 429 면 `on_rate_limit(e)`: 공급자가 **선언한** 대기
  (Gemini `RetryInfo.retryDelay` · `retry-after`, Telemetry `errors.translate` 로 읽음)가 이기고, 없으면 할당 힌트
  (`quotaId` 의 PerMinute → 60 초, PerDay → 오늘은 더 부르지 않음), 그것도 없으면 60 초. 시계는 바꿔 끼울 수 있다.
- **걸음 표(`rlo-step-kinds/1`)**: 걸음 이름마다 `model`(LLM 공급자를 부른다) 또는 `tool`. **닫힌 표**라 표에 없는 이름은
  싣는 순간 `StepKindError` 다. 표는 rlo 의 것이다(action-model/1 밖, `substitutes` 와 같은 자리).
- **걸음 차례(`rlo.scheduler.Scheduler`)**: 도구 걸음은 준비되면(`after` 끝남 · `not_before` 지남) 언제나 돈다 — 세워 둔 모형
  걸음 뒤에 막히지 않는다. 모형 걸음은 큐 차례대로 하나씩, 지킴이가 허락할 때만 보낸다. 할 수 있는 것이 없을 때만 다음 도구
  걸음 · 창이 열리는 때 가운데 이른 쪽까지 **한 번** 잔다(바쁘게 돌지 않는다). 하루 할당처럼 기다려도 풀리지 않으면 세운 채 끝낸다.
- **기록**: 세움 · 보냄 · 429 · 끝남마다 원장 줄(걸음 id) 하나와 L0 사건(닫힌 목록 안: `runtime.status` · `llm.request`
  (`attempt` = 다시 보낸 횟수) · `llm.response` · `llm.error`(`retry_after_ms`, `error_code` RATE_LIMITED) · `tool.start/end`).
  L0 사건 목록에 걸음 칸이 없어서 모형 걸음은 `call_index` 로 잇는다.
- **VERIFY(health)**: 세운 걸음마다 "창(대기 + 여유 5 초) 안에 보냈다" 를 health `verify` 로 판정한다(원장 `verification`).
- **저장 · 이어 가기 · 상태(S6)**: `Scheduler(..., state="sched.json")` 는 바뀔 때마다 상태(`rlo-scheduler-state/1`: 걸음마다 상태 ·
  시도 수, 끝난 걸음의 결과(JSON 이 되는 것만), 지킴이 창, 세운 걸음의 VERIFY 창)를 저장하고, 파일이 있으면 시작할 때 읽어 이어 간다
  — 프로세스를 다시 띄워도 끝난 모형 걸음을 다시 부르지 않고, 지킴이 창도 되살아난다. 다시 띄울 때 같은 걸음(id · 이름)을 다시
  싣는다(함수는 저장되지 않는다). `run(wait=False)` 는 자야 할 때 저장하고 돌아온다. `status()` =
  `{resumes_in_s, done, running, parked, next: [{id, kind}]}` 를 감독(ga `ga gemini`)이 그대로 찍는다. `on_event(row)` 는 원장 줄마다 불린다.
- **함께 도는 도구 걸음(S7)**: 준비된 도구 걸음은 크기가 정해진 스레드 풀(`max_parallel`, 기본 4 · 1 이면 예전처럼 하나씩)에서
  함께 돈다. 걸음마다 시간 한도(`Step.timeout_s` · `tool_timeout_s`, 실제 초) — 넘으면 그 걸음만 `timeout` 으로 실패하고 다른
  걸음은 끊지 않는다(파이썬 스레드는 죽일 수 없어 그 스레드는 끝날 때까지 풀 한 칸을 쓴다). 모형 걸음은 도는 도구 걸음을 기다리지
  않고, 도구 걸음은 세운 모형 걸음을 기다리지 않는다. 결과 · 원장 줄(`start` · `done` · `failed`) · L0 사건은 걸음마다 끝난 차례로
  적는다. 저장 상태는 도는 걸음을 `running` 으로 남기고 다시 띄우면 그것만 다시 돈다. `status()` 의 `running` 은 차례로 첫째 도는 걸음,
  `running_all` 은 모두. 도구 함수는 다른 스레드에서 불리므로 공유 상태를 고친다면 스스로 지킨다.
- **`Autonomy(..., governor=)`**: LLM 을 감싼다. 한 `handle` 의 첫 부름 앞에서 예산이 없거나 429 면 실패하지 않고
  **미룬 결과**(`outcome == "deferred"`, `wait_s` · `step_id`)를 낸다. `tick()` 또는 `close_windows()` 가 창이 열리면 차례대로
  다시 보낸다. 새 요청은 세운 걸음 뒤에 선다. 한 실행 안의 둘째 부름부터는 걸음을 다시 보내지 않고(앞 판을 되풀이하게 된다)
  창이 열릴 때까지 한 번 잔다(`governor_sleep`, 90 초 넘는 대기 · 429 세 번 넘으면 미룸).

## 어느 모형 · 런타임에나 — 플러그인 (CMD-K18, BD-300)

rlo 의 속은 공급자 SDK 를 들이지 않는다(네트워크 · 키 · 내장 할당 없음, BD-289). 런타임 · usage 꼴 · 토크나이저는 **설치한
패키지가 진입점으로 꽂는다** — rlo 를 고치지 않는다. `python -m rlo.plugins list` 가 실린 것과 실리지 못한 것을 보인다.

```toml
[project.entry-points."rlo.transcripts"]   # 런타임 transcript 읽개
my_runtime = "my_pkg.reader:READER"        # name · version · api=1 · usage_format · last_usage(path) -> dict | None
[project.entry-points."rlo.usage"]         # usage 꼴
my_format = "my_pkg.usage:FORMAT"          # normalize(raw) -> {input, cache_read, cache_creation, output, context} | None
[project.entry-points."rlo.tokenizers"]    # 토크나이저
my_tok = "my_pkg.tok:TOKENIZER"            # count(text) -> int
```

- **플러그인 API 판 1**: 객체(클래스면 인자 없이 만든다)는 `name`(진입점 이름과 같다) · `version` · `api`(1)와 무리의 메서드를
  갖는다. 판이 다르거나 · 이름이 겹치거나 · 들이다 실패하면 그것만 `plugins.load_errors()` 에 적고 나머지는 싣는다. 기본 제공이
  먼저 실리므로 같은 이름의 바깥 플러그인은 겹침으로 기록된다.
- **기본 제공**(같은 진입점 길): 읽개 `claude_code`(K17 그대로) · `codex_cli` · `gemini_cli`(둘은 `experimental` — 꼴은 openai/codex
  `afb436d` 와 @google/gemini-cli-core 0.62.0 의 소스로 확인했고 fixture 는 그 꼴, 실제 세션 기록으로는 재지 않았다), usage 꼴
  `anthropic` · `openai` · `gemini` · `otel`(Telemetry `l0_usage` 를 감싼다; OpenAI · Gemini 에는 캐시 쓰기 갈래가 없어 0, Gemini 가
  빼고 보낸 cached 는 0, 그 밖에 모르는 것은 None), 토크나이저 `bytes4`(= `pspec.tokens`, §4 의 바닥을 재는 수 — 바뀌지 않는다).
- **컨텍스트 예산**: `context_budget={..., "runtime": "codex_cli", "usage_format": None}` · 명령 훅 `--budget-runtime` ·
  `--budget-usage-format`. 이름이 없으면 `claude_code`(K17 그대로), 없는 이름은 설정 오류. 기록 줄에 `runtime`.
- **토큰 보고**: `pspec.token_report(texts, usages, usage_format, tokenizer=None)` — 추정은 늘 bytes4, 토크나이저를 이름 지으면
  그 수를 옆에(`tokenizer`), 공급자 사용량은 usage 꼴로 맞춘 칸(`provider.context` 등, 앞 판의 이름도 그대로).
- **꾸러미**: `install-hook` 이 `--budget-soft/--budget-hard/--budget-state/--budget-mode/--runtime/--budget-usage-format` 을 받는다.
  `python -m rlo.hooks claude-plugin --out DIR --model M [같은 플래그]` 가 Claude Code 플러그인 꼴(`.claude-plugin/plugin.json` ·
  `hooks/hooks.json` · `rlo/model.json`, 명령은 `"${CLAUDE_PLUGIN_ROOT}/rlo/model.json"` 을 읽는다)을 짓는다 —
  `claude plugin validate --strict` 를 지난다. 짓기만 한다: 어느 세션에 까는지는 사용자가 정한다.

## 컨텍스트 예산 — context-budget/1 (CMD-K17, BD-296)

긴 자율 세션은 모형을 부를 때마다 쌓인 대화 전체를 다시 읽는다(캐시 읽기) — 토큰은 '컨텍스트 크기 × 호출 수' 로 는다.
rlo 훅의 PreToolUse 가 가드 판정 **앞에서** 컨텍스트를 재고(transcript 꼬리의 마지막 주 사슬 assistant `usage`:
input + cache_read + cache_creation), 예산을 넘으면 세션이 상태를 남기고 차례를 끝내게 한다(baseline CONTEXT_BUDGET.md · 원형
`ops/ctxbudget/`).

| 단계 | 조건 | enforce 의 출력 |
|---|---|---|
| ok | ctx < soft | 없음 |
| warn | soft ≤ ctx < hard | `additionalContext`: 지금 단계만 끝내고 상태 파일을 쓰고 commit · push 한 뒤 차례를 끝내라 |
| checkpoint | ctx ≥ hard | checkpoint 도구(상태 파일 Write/Edit · `cd` · `git add/commit/push/status` 만 있는 Bash)가 아니면 `deny` + 까닭; checkpoint 도구면 알림만 |
| unknown | usage 를 못 읽음 | 없음 — 모름은 0 도, 예산 넘음도 아니다(기록만) |

- **기본 예산은 없다**(BD-289): `guard_hooks(..., context_budget={"soft": 150_000, "hard": 200_000, "state_paths": ["STATE.md"],
  "mode": "shadow"})` 또는 명령 훅 `--budget-soft N --budget-hard N [--budget-state PATH] [--budget-mode shadow|enforce]`.
  설정이 없으면 이 단계는 없다. 모드 기본은 **shadow**(기록만, 아무것도 바꾸지 않음) — 가드 모드(`--mode`)와 따로다.
- **가드의 deny 가 늘 이긴다**: 예산은 막기만 한다. hard 를 넘은 non-checkpoint 호출은 가드를 묻지 않고 막고, 나머지는 가드
  판정 뒤 가드가 막으면 그대로, 아니면 알림(`additionalContext`)을 붙인다. 예산은 `permissionDecision: "allow"` 를 내지 않는다
  (allow 는 Claude Code 의 권한 확인을 건너뛴다 — 허락은 가드 · 사용자 설정의 몫).
- **checkpoint Bash** 는 `&&` · `;` · 줄바꿈으로 나눈 조각이 모두 `cd <dir>` 또는 `git [-C dir] add|commit|push|status …` 이고
  `` ` `` · `$(` · `${` · `|` · `>` · `<` · 홑 `&` 가 없어야 한다. 상태 파일은 경로가 같거나 `/<이름>` 으로 끝나야 한다.
- **기록**: 결정마다 `--record` 에 `context_budget {stage, ctx, soft, hard, tool_name, mode, enforced, denied}` 한 줄.
- **비용**: transcript 는 꼬리(256 KiB, 못 찾으면 두 배씩)만 읽는다 — 100 MB 에서도 수 ms.
- **고르기(오프라인)**: `python -m rlo.ctxbudget simulate (--l0 L0.jsonl | --cc transcript.jsonl) --soft N --hard N
  [--reset-to N]` 이 기록된 세션의 호출별 컨텍스트로 '예산이 있었다면' 을 추정한다(원형의 `simulate`, L1).

## 프롬프트 언어 — prompt-spec/1 (CMD-K15, baseline PROMPT_SPEC.md b9e7669)

손으로 쓴 `str.format` 프롬프트 대신 뜻을 선언한 파일 하나(`*.pspec`)에서 세 가지를 낸다: 프롬프트 글(`verbatim` = 지금 글과
바이트 같음 · `compact` = 토큰을 줄인 글), 같은 `out` 선언에서 나온 출력 검사기, 토큰 보고. **문법은 baseline 의 것**이다
(PROMPT_SPEC.md §1-§3, 원형 `ops/pspec/pspec.py`). rlo 는 제품판을 짓는다(§5): 시험 · 변이 · 오류 위치 · 거르개 · 타입 확장 규칙 ·
Telemetry · 판본 · 성능. 모형을 부르지 않는다(공급자 SDK · 네트워크 · 키 없음).

```python
from rlo import pspec

spec = pspec.load_file("gemini-plan.pspec")                 # 꼴 오류는 SpecError("gemini-plan.pspec:12:7: ...")
first = pspec.compile(spec, "first", {"tools": tools, "task": task, "results": [], "ask": ""}, "compact")
problems = pspec.check(spec, pspec.parse_answer(answer), {"tools": tools})   # [] 이면 받는다; 또는 check_text(spec, answer, …)
texts = pspec.conversation(spec, first_values, turn_values, "compact", resumes=True)   # 첫 턴 + 뒤 턴들
report = pspec.token_report(texts, usages, "gemini")         # 턴마다 추정(ceil(바이트/4)) 과 공급자 사용량(Telemetry) 나란히
```

- **§4 의 바닥**은 `tests/test_pspec.py` 가 fixture(`tests/fixtures/pspec/ga_438a34a.json`, ga-sdk 438a34a 의 `protocol()` ·
  `Supervisor._prompt` · `check_plan` 에서 뜸 — `eval/capture_pspec_fixtures.py`)로 지킨다. rlo 는 ga 를 들이지 않는다:
  verbatim 이 `protocol()` · 첫 턴 · `--resume` 턴 · agy 턴과 도구 표 셋에서 바이트 같다, `check` 가 `check_plan` 과 17/17 같다(사례 17 은 BD-292),
  모르는 낱말 · 근거 · 빠진 입력은 오류, dict 순서는 글을 바꾸지 않는다, 8 턴 토큰 Gemini 646 → 447 · agy 2263 → 1574.
- **대화**: `conversation(...)` 이 첫 턴은 `first`, 뒤 턴은 `turn`(--resume 으로 앞을 기억하는 호스트) 또는 `turn_noresume` 으로
  짓는다 — 기억하는 호스트에 once 구역을 다시 보내지 않는 판단은 여기 하나다.
- **오류 위치**: 적재 · 템플릿 오류는 `SpecError(.line, .col, .source)`, 글은 `파일:줄:열: 까닭`. 구역 템플릿은 적재 때 한 번
  파싱해 둔다 — 모르는 태그 · 거르개 · 짝 없는 `end` · `v` 밖의 `c` · 없는 구역의 `use` · `use` 순환은 적재 오류다.
- **거르개**: `json` · `cap:N` · `rstrip:"chars"` · `sort`. 인자 꼴은 적재 때 본다.
- **BD-292(CMD-K16)**: `id seen` 은 앞 객체들의 id 만(자기를 가리키는 `after` 거부 — `check_plan` 과 17/17), `id new` 는
  `let id` 에 통째로(fullmatch) 맞아야 한다(`"a\n"` 거부), 템플릿 이름은 입력 · `let` · `out` · `out_schema` · 반복 변수(와 그
  속성)뿐 — 오타(`{{ tsk }}`)는 적재 오류(줄 · 열).
- **타입 확장 규칙**: 타입은 문법이다 — baseline 이 PROMPT_SPEC §3 을 고친 뒤에만 더한다. rlo 에서는 `KINDS` 한 줄과 세 곳
  (`_P.atom` 파서 · `_sig` 서명 · `_check` 검사), 시험(읽기 · 서명 · 받음 · 거부)과 변이 하나. 이미 있는 명세의 글은 바이트 그대로.
- **Telemetry**: `token_report(texts, usages, usage_format)` 는 부른 쪽이 넘긴 공급자 사용량을 Telemetry `l0_usage` 로 읽어
  턴마다 `provider`(input · cache · output 토큰, `prompt_tokens` = 보낸 쪽 합)를 추정 옆에 둔다.
- **판본**: 언어 `pspec.LANGUAGE = "prompt-spec/1"`, 명세의 `spec <name>/<n>` → `Spec.version`, `Spec.digest`(파일 sha256).
- **성능**: 템플릿은 적재 때 파싱해 두고, 한 턴 컴파일 + 검사는 0.1 ms 안팎(시험 한도 10 ms).

## 기록을 읽는 도구 둘

둘 다 읽기만 한다. 결과는 JSON 으로 표준 출력에 낸다.

**`python -m rlo.suggest_model --model <action-model/1 JSON> --record <훅 기록 JSONL>`** — 훅의 `--record` 기록(guard 줄의 `tool_input_keys`, 칸 이름만)에서 모형에 없는 것을 찾아 **초안**을 낸다. 모형은 바꾸지 않는다.

- `new_specs`: 모형에 없는 도구마다 action-spec 초안 하나. `risk` 는 `null`(사람이 정한다), 설명은 `[DRAFT] …`.
- `new_fields`: 모형에 있는 도구에 기록에서 처음 본 칸. `required: false`(이전 호출에 없던 칸이라 선택일 가능성이 크다), `note: "[DRAFT] 기록에서 찾은 새 칸"`.
- 기록에서 잰 칸만 넣는다. 칸 타입은 이름으로 추측한다(`*_ms` · `timeout` · `duration` → number, `is_*` · `*_flag` · `*_enabled` → bool, 나머지 string) — 기록에 값이 없기 때문이다.

```
python -m rlo.hooks --model m.json --mode shadow --record rec.jsonl …     # 훅이 기록을 남기고
python -m rlo.suggest_model --model m.json --record rec.jsonl > draft.json  # 사람이 draft.json 을 보고 모형에 옮긴다
```

**`python -m rlo.parallel_calls <transcript.jsonl> [<transcript.jsonl> …]`** — Claude Code transcript 에서 한 응답(`message.id`)이 부른 `tool_use` 수를 센다. 파일마다 `responses`(`message_id` · `tool_count` · `tools`) 와 `summary`(`total_responses` · `parallel_responses`(2 개 이상) · `max_tool_count`). 나란히 부른 호출은 훅이 앞 호출을 기다리는 중으로 보아 `agent.execution_health` 를 모름 → D 로 막을 수 있으니(위 판정 표의 parallel), 실제 작업에서 얼마나 잦은지 잴 때 쓴다.

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
