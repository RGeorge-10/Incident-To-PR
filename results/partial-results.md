# Partial results — simulation lab

Generated 2026-11-13 07:11 UTC by `scripts/evaluate.py`.

- **Environment:** simulation lab (synthetic telemetry, in-memory GitHub and Argo CD)
- **Model:** `openai/rule-based-stub` — a deterministic rule-based stub, **not an LLM**
- **Policy ruleset:** `phase1-v2` · human approval required: True · auto-apply: False

> **How to read this.** Every fault here is synthetic and injected by the simulator. Because the decisions come from a rule-based stub written alongside those faults, the decision matrix shows that the pipeline, guards and policy gate behave as designed. It is **not** a measurement of diagnostic accuracy. No result here is evidence of repairing a real system.

## 1. Decision matrix

**14 of 14** cases selected the expected action.

| Expected ↓ / Selected → | INFRA_FIX | CODE_FIX | ROLLBACK | ESCALATE_HUMAN | (no decision) |
|---|---|---|---|---|---|
| **INFRA_FIX** | 4 | 0 | 0 | 0 | 0 |
| **CODE_FIX** | 0 | 4 | 0 | 0 | 0 |
| **ESCALATE_HUMAN** | 0 | 0 | 0 | 6 | 0 |

### Per case

| Service | Injected fault | Expected | Selected | Fault class | Confidence | Outcome | Policy | Patch | Time (s) | Tokens |
|---|---|---|---|---|---|---|---|---|---|---|
| shippingservice | capacity | INFRA_FIX | ✅ INFRA_FIX | INFRA | 0.86 | awaiting_review | allowed | deploy/online-boutique/shippingservice.yaml | 0.48 | 1628 |
| cartservice | capacity | INFRA_FIX | ✅ INFRA_FIX | INFRA | 0.86 | awaiting_review | allowed | deploy/online-boutique/cartservice.yaml | 0.46 | 1594 |
| checkoutservice | capacity | INFRA_FIX | ✅ INFRA_FIX | INFRA | 0.86 | awaiting_review | allowed | deploy/online-boutique/checkoutservice.yaml | 0.46 | 1627 |
| paymentservice | capacity | INFRA_FIX | ✅ INFRA_FIX | INFRA | 0.86 | awaiting_review | allowed | deploy/online-boutique/paymentservice.yaml | 0.46 | 1619 |
| shippingservice | code_bug | CODE_FIX | ✅ CODE_FIX | CODE | 0.9 | awaiting_review | allowed | src/shippingservice/main.go | 0.47 | 1670 |
| cartservice | code_bug | CODE_FIX | ✅ CODE_FIX | CODE | 0.9 | awaiting_review | allowed | src/cartservice/main.go | 0.45 | 1644 |
| checkoutservice | code_bug | CODE_FIX | ✅ CODE_FIX | CODE | 0.9 | awaiting_review | allowed | src/checkoutservice/main.go | 0.47 | 1671 |
| paymentservice | code_bug | CODE_FIX | ✅ CODE_FIX | CODE | 0.9 | awaiting_review | allowed | src/paymentservice/main.go | 0.46 | 1664 |
| shippingservice | ambiguous | ESCALATE_HUMAN | ✅ ESCALATE_HUMAN | AMBIGUOUS | 0.4 | escalated | blocked / n.a. | — | 0.46 | 975 |
| cartservice | ambiguous | ESCALATE_HUMAN | ✅ ESCALATE_HUMAN | AMBIGUOUS | 0.4 | escalated | blocked / n.a. | — | 0.46 | 957 |
| checkoutservice | ambiguous | ESCALATE_HUMAN | ✅ ESCALATE_HUMAN | AMBIGUOUS | 0.4 | escalated | blocked / n.a. | — | 0.45 | 975 |
| paymentservice | ambiguous | ESCALATE_HUMAN | ✅ ESCALATE_HUMAN | AMBIGUOUS | 0.4 | escalated | blocked / n.a. | — | 0.47 | 970 |
| frontend | none | ESCALATE_HUMAN | ✅ ESCALATE_HUMAN | UNKNOWN | 0.2 | escalated | blocked / n.a. | — | 0.45 | 802 |
| adservice | none | ESCALATE_HUMAN | ✅ ESCALATE_HUMAN | UNKNOWN | 0.2 | escalated | blocked / n.a. | — | 0.45 | 805 |

Proposals generated: 8. Allowed by the policy gate: 8. All 8 stopped at human review; none opened a pull request on its own.
Median analysis time: 0.46 s.

## 2. Automatic detection

- Fault injected → incident opened: **6.7 s** (poll every 5 s, 2 consecutive unhealthy polls required).
- Signals recorded: deployment_replica_shortfall, crash_loop, repeated_container_restarts
- Duplicate incidents while the fault persisted: **0**

## 3. Recovery verification and masking

| Case | Service | What was merged | Argo CD | Health window | Final status |
|---|---|---|---|---|---|
| True infrastructure fault | shippingservice | replica PR #1 | Synced/Healthy | 2-min window: 5/5 healthy samples, stable=True | **verified** after 133 s |
| Code fault treated as infrastructure (masking) | currencyservice | replica PR #1 | Synced/Degraded | 2-min window: 0/5 healthy samples, stable=False | **merged_waiting_verification** |

The masking case is the important one: the pull request merged and Argo CD synced the new revision, yet after 184 s the incident is still **not** reported as recovered, because the service never became healthy.

## 4. Safety and guard test suites

| Suite | Passed | Failed |
|---|---|---|
| Policy gate (allowed changes + bypass attempts) | 74 | 0 |
| State-machine guards and adversarial model output | 39 | 0 |
| Whole backend suite | 139 | 0 |

These are deterministic code-level results and hold regardless of which model is used.

## Not yet measured

- Any fault on the real kind cluster (the F01–F14 fault lab is not built).
- Hidden regression tests, baselines (B0–B3) and repeated trials with uncertainty.
- Diagnostic accuracy of a real LLM on real telemetry.
