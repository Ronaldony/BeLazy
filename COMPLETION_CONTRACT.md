# be-lazy Autopilot 완료 계약

## 1. 완료 상태

### COMPLETE

다음 조건을 모두 만족한다.

1. W00부터 W07까지 `passed`다.
2. `AUTOPILOT_PROGRAM.yaml`의 모든 non-optional task ID가 코드·Schema·테스트·문서 증거로 구현됐다.
3. source archive와 handoff package가 작업 전후 exact-byte 수준에서 불변이다.
4. 기존 characterization test와 신규 회귀 test가 현재 지원 환경에서 통과한다.
5. wheel build와 isolated installation에서 모든 등록 Schema를 source checkout 없이 로드할 수 있다.
6. strict JSON negative cases가 duplicate key, non-finite number, malformed encoding, invalid date-time, missing path를 안정적으로 거부한다.
7. approval/readiness가 current effective config와 모든 material context digest에 결합된다.
8. Managed Mutation Plane이 direct human mutation을 정상 경로로 허용하지 않으며 exact-before, path containment, drift, break-glass, receipt 규칙을 표현한다.
9. ProductionBlueprint와 Director Mesh가 versioned contract 및 shadow projection으로 구현된다.
10. Declarative workflow와 authority evaluator가 legacy와 dual-run parity evidence를 갖는다.
11. CandidateDecision, QualityBundle, ReleaseCandidate/Assessment가 자동화와 escalation을 안정적인 reason code로 표현한다.
12. Durable journal과 managed executor가 pure core 밖에 있으며 idempotency, restart, uncertain state, path/TOCTOU 방어를 테스트한다.
13. 모든 fresh reviewer의 Critical/High finding이 0개다.
14. target working tree가 final checkpoint 뒤 clean하다.
15. remote, network, publish, deploy, credential, actual provider side effect가 없었다.
16. 인간 승인 identity, signature 또는 approval artifact를 AI가 생성하지 않았다.
17. public identifier migration은 수행하지 않고 deferred decision으로 기록했다.

### PARTIAL

hard blocker는 아니지만 host 환경 때문에 일부 integration test를 실행하지 못했다. 이 상태는 다음을 요구한다.

- 모든 구현과 local unit/contract test는 통과
- 실행하지 못한 검사의 정확한 이유와 CI/runbook 존재
- P0/P1 correctness 또는 authority gap 없음
- 미실행 검사를 실행했다고 주장하지 않음

### BLOCKED

`AUTONOMOUS_DECISION_DEFAULTS.yaml`의 hard blocker 중 하나가 실제로 발생했다. Agent는 질문하지 않고 증거, 마지막 trusted checkpoint, 안전한 재시작 조건을 최종 보고서에 남긴다.

## 2. 필수 검증 계층

- source/handoff integrity
- repository inventory와 provenance
- focused unit/contract tests
- full test suite
- branch/negative tests
- core purity/side-effect/repository isolation checks
- wheel build와 isolated install
- Schema registry loading
- legacy/new workflow parity corpus
- mutation executor isolated fixture tests
- final read-only architecture/security/test reviews

## 3. 완료가 아닌 것

- 계획 또는 TODO만 작성
- 일부 Schema만 추가하고 registry/migration 누락
- happy-path test만 통과
- Reviewer finding을 문서에만 기록하고 미수정
- 실제 실행하지 않은 테스트를 PASS로 보고
- source를 수정한 뒤 되돌렸다고 주장
- runtime 인간 승인을 AI consensus로 대체
- remote push 또는 publish까지 수행
