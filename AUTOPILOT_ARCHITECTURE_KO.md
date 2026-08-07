# be-lazy Prompt-Only Autopilot 아키텍처 v4

## 1. 목표

v3는 변경의 신뢰성을 높였지만, 사람에게 다음 제어 작업을 요구했다.

- 오프라인 self-test 실행
- dry-run 실행
- T00 단독 실행과 검토
- tranche별 반복 trigger
- receipt와 diff 확인
- finding을 Repair 입력으로 전달
- blocker decision 재주입

v4는 이 제어를 Agent 내부로 이동한다. 사람은 최초에 전체 로컬 프로그램 범위를 위임하고, Supervisor Agent가 상태와 검증을 소유한다.

```text
PROGRAM MANDATE — 최초 프롬프트
        ↓
PREFLIGHT
        ↓
W00 → W01 → W02 → W03 → W04 → W05 → W06 → W07
        │      각 Wave 내부
        │
        ├─ parallel read-only discovery
        ├─ Supervisor-only implementation
        ├─ deterministic tests/checks
        ├─ parallel read-only reviews
        ├─ automatic repair/re-review
        └─ local checkpoint + state update
```

## 2. 한 명의 writer

Main Supervisor만 target의 최종 branch를 수정한다. 서브에이전트는 기본적으로 read-only 조사와 검토에 사용한다.

이 선택은 다음 문제를 줄인다.

- 병렬 writer의 merge conflict
- 서로 다른 agent가 같은 contract를 상충되게 변경
- 어느 agent가 어떤 byte를 만들었는지 불명확
- review agent가 자신의 변경을 승인하는 구조

필요한 구현 작업은 Supervisor가 직접 수행한다. 별도 worker subagent가 patch를 제안할 수는 있지만 target에 직접 쓰지 않고 구체적인 파일·symbol·테스트 제안만 반환해야 한다.

## 3. Subagent 구성

### Wave 시작 전

최소 2개의 fresh read-only agent를 병렬 실행한다.

- **source/flow explorer**: 실제 호출 경로, public contract, test oracle 탐색
- **acceptance/risk planner**: Wave 명세를 코드·Schema·테스트 증거로 변환하고 실패 모드 제시

### Wave 구현 후

최소 3개의 fresh read-only agent를 병렬 실행한다.

- **architecture/contract reviewer**: 책임 분리, 순환 의존성, versioning, migration, plan-only core
- **security/authority reviewer**: fail-closed, digest binding, path/TOCTOU, human approval 위조, side effect 경계
- **test/compatibility reviewer**: legacy parity, negative tests, packaging, isolated install, platform gap

Reviewer는 과거 Builder의 reasoning을 정답으로 가정하지 않고 실제 diff, 코드와 테스트 결과만 검토한다.

## 4. 자동 Review/Repair loop

```text
BUILD
  → deterministic checks
  → REVIEW A/B/C
  → findings merge
       ├─ Critical/High 없음 → PASS
       └─ 존재 → Supervisor repair
                    → tests
                    → fresh REVIEW A/B/C
```

- 정상 repair 최대 3회
- 계속 실패하면 Wave 시작 checkpoint로 rollback
- 새로운 discovery agents로 원인 재분석
- Wave를 한 번 전면 재구현
- 재구현도 실패한 경우에만 hard blocker

사람에게 finding을 옮기거나 수정 방향을 선택하게 하지 않는다.

## 5. 8개 Wave

| Wave | 통합한 v3 tranche | 결과 |
|---|---|---|
| W00 | T00–T01 | 신규 저장소 bootstrap, source import, parity baseline |
| W01 | T02–T04 | Schema packaging, strict JSON, approval context binding |
| W02 | T05–T06 | Managed Mutation contracts와 guards |
| W03 | T07–T08 | Director Mesh, ProductionBlueprint, shadow projections |
| W04 | T09–T11 | Declarative workflow, authority control, dual-run parity |
| W05 | T12–T14 | 자동 후보 선택, QualityBundle, Release control |
| W06 | T15–T17 | Durable journal, managed executor, migration runbooks |
| W07 | 신규 | 전체 회귀·패키징·경계 감사와 최종 보고 |

T90 public identifier migration은 자동 프로그램에서 제외한다. 초기 호환 식별자를 유지하는 것이 기본 결정이다.

## 6. 프로그램 상태

Agent는 target에 다음 상태를 유지한다.

```text
.be-lazy/autopilot/state.json
.agent/execplans/be-lazy-autopilot.md
reports/autopilot/waves/<wave-id>/
reports/autopilot/final-report.md
reports/autopilot/final-result.json
```

각 Wave state는 다음을 포함한다.

- 상태: pending / in_progress / reviewing / repairing / passed / blocked
- 시작 checkpoint
- 완료 commit
- task ID
- 변경 경로
- 실제 테스트 명령과 결과
- reviewer finding과 해결 여부
- source/handoff pre/post digest
- rollback 정보

## 7. Local Git checkpoint

외부 오케스트레이터가 없으므로 v4는 Supervisor Agent에 local Git checkpoint를 허용한다.

- branch: `agent/autopilot-v4`
- 권장 commit: `autopilot(W03): director mesh and blueprint shadow`
- remote 생성·변경·push는 금지
- Wave 실패 시 시작 commit으로 rollback
- final audit 통과 후 local tag `be-lazy-autopilot-complete-v4` 허용

Local commit은 운영 승인이나 배포 권한이 아니라 복구 지점이다.

## 8. 최초 프롬프트의 권한 의미

최초 메시지는 target repository engineering에 대한 bounded mandate다. 다음을 승인한다.

- target 내부 파일 변경
- 로컬 명령과 테스트
- local Git checkpoint
- subagent 사용

그러나 다음을 승인하지 않는다.

- 애플리케이션의 인간 승인 record
- 실제 유료 generation
- publish 또는 deploy
- remote push/merge
- credential 사용

따라서 Agent는 초기 메시지를 `approved_by_human=true` 같은 runtime artifact로 변환할 수 없다.

## 9. 질문을 없애는 결정 정책

모호성은 `AUTONOMOUS_DECISION_DEFAULTS.yaml`로 해결한다.

- 호환성 유지
- additive contract/version 우선
- 외부 integration은 port/fake
- production side effect는 계획·receipt까지만
- 현재 host에서 불가능한 플랫폼 검증은 CI 정의와 local unit evidence로 대체
- optional public identifier migration은 defer
- 안전과 권한이 불명확하면 runtime은 fail closed하되 구현 프로그램은 계속

결정은 ADR 또는 ExecPlan에 남긴다.

## 10. Hard blocker와 non-blocking gap

### Hard blocker

- 입력 digest 불일치
- source/target 경계 위반
- target에 기존 보호 대상 소스·uncommitted 작업 또는 충돌하는 Autopilot state 존재
- target 쓰기/Git checkpoint 불가
- rollback 이후 Git/control state 불신

### 질문하지 않고 기록만 하는 gap

- Windows/Python 3.12 runner 부재
- 실제 provider credential 부재
- 외부 approval ledger 부재
- remote repository 부재
- public identifier migration 미결정

이 gap은 local contract, fake adapter, CI configuration, runbook으로 표현하고 다음 Wave를 막지 않는다.

## 11. 컨텍스트 관리

장기 작업의 main thread는 요구사항, 결정, state와 통합 결과에 집중한다. 대량 탐색 로그와 개별 테스트 분석은 subagent에 위임한다. 각 Wave 종료 시 state와 ExecPlan을 갱신하므로 자동 compact 또는 새 세션에서도 이어갈 수 있다.

## 12. 완료

완료는 Agent의 자연어 선언이 아니라 다음 교집합이다.

```text
W00..W07 passed
AND required task IDs implemented
AND legacy and new tests pass in available supported environment
AND wheel/schema isolated loading passes
AND core purity/isolation boundary passes
AND source/handoff digest unchanged
AND no Critical/High review finding
AND target Git working tree clean
AND final report/result generated
AND no network/remote-change-or-contact/publish/human-approval synthesis
```
