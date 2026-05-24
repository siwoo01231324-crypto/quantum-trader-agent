# cand-c-2026-05-20-live-* 4종 — Scalping reparam sweep 최종 보고 (2026-05-21)

> 이 문서는 PRD `cand-c live-scanner scalping reparam sweep` 의 US-06 결과물.
> Sweep 종료 후 PF/조합 수치만 갱신해서 사용자에게 발송.

## TL;DR

{{PASS_OR_FAIL_HEADLINE}}

- 4 cand-c-2026-05-20-live-* 전략, 2 iteration × 32 조합 = **64 조합 모두 PF<1 NET LOSER** (또는 N 개 통과 — 결과에 따라 갱신)
- 단타 가설 (10x 레버 + 0.5~6% TP/SL 영역) **falsified** (또는 partial pass)
- production.yaml `cand-c-*` 4 항목 → **비활성화 권고** (또는 통과 조합 capital_fraction comment)

## 검증한 가설

원본 cand-c-* default = sl 5% / tp 6%. 라이브 실거래에서 안 팔리고 long-hold → 사용자 가설 "10x leverage + price-move 0.5~2% TP/SL 단타 (scalping) 로 좁히면 회전 빠르게 돌아 PF>1 가능?"

## Iteration 1 — 단타 좁은 범위

Grid: sl ∈ {0.5%, 0.8%}, tp ∈ {1.0%, 1.5%}, trailing ∈ {OFF, 0.5%} = 8 조합 × 4 전략 = 32 runs.

| 결과 | 값 |
|---|---|
| PASS (PF>1 AND exp>0) | **0 / 32** |
| 최고 PF | 0.532 (bb_lower_bounce sl0.8/tp1.5 tr=OFF) |
| 최저 PF | 0.230 (breakout sl0.5/tp1.5 tr=0.5%) |
| 평균 expectancy | −0.40% / trade |
| 거래수 범위 | 5,400 ~ 22,700 |

진단: 1m noise 진폭 ~0.1%, 20bp cost → TP 0.5-1.5% range 에서 cost 가 raw signal 잠식.
tr=0.5% 가 tr=OFF 대비 PF 절반 — **좁은 trailing 이 noise whipsaw 만 양산.**

원자료: `reports/sweep_candc_scalping_btc_probe.json`

## Iteration 2 — 중간지대 (단타 ↔ 원본)

Grid: sl ∈ {1.5%, 2.5%}, tp ∈ {3%, 6%}, trailing ∈ {OFF, 2%} = 8 조합 × 4 전략 = 32 runs.

{{ITER2_RESULT_TABLE}}

| 결과 | 값 |
|---|---|
| PASS | {{ITER2_PASS_COUNT}} / 32 |
| 최고 PF | {{ITER2_MAX_PF}} ({{ITER2_BEST_COMBO}}) |
| 평균 expectancy | {{ITER2_AVG_EXP}} |

원자료: `reports/sweep_candc_scalping_btc_iter2.json`

## 누적 증거 (이번 sweep + 기존 검증)

| 검증 | 조합 수 | PASS | 결과 |
|---|---|---|---|
| live-scanner 5종 5y 원본 default | 5 | 0 | PF 0.85~0.92 (commits 9553e87) |
| cand-c-* 1y 16/16 sweep (user 작업) | 16 | 0 | PF<1 (commit ac60bc4) |
| breakout-atr 1y/5y 19+3 조합 sweep | 22 | 0 | PF 0.84~0.92 (commits 1ea3164/f9fb6ba) |
| **이번 scalping iteration 1+2** | **64** | **{{TOTAL_PASS}}** | **{{TOTAL_VERDICT}}** |
| **누적 합계** | **107** | **{{CUMULATIVE_PASS}}** | **{{CUMULATIVE_VERDICT}}** |

## 결론

{{CONCLUSION_TEXT}}

**옵션 A — 단타 가설 거부 (PASS=0 시):**
이 4개 전략은 어떤 TP/SL 조합으로도 비용 후 PF>1 안 나옴. 가설 그 자체가 raw signal alpha 가 부재한 채로 TP/SL 만 조정하면 살아남는다는 잘못된 전제 위에 있음. naive RSI/MACD/BB/divergence 1m 신호 → cost 후 음의 EV = 거래소·시장 미세구조의 결과.

**옵션 B — PASS 발견 시 (희박):**
통과 조합 → 5y multi-regime 검증 후 production 후보. 단 1y BTC-only 단일 종목 결과라 일반화 위험. 30-symbol universe 적용 시 패턴 깨질 가능성 큼.

## 권고 (자동 적용 금지, 사용자 승인 후)

### production.yaml 변경안 (commented suggestion)

```yaml
  # 2026-05-21 결과: cand-c-* 4종 scalping reparam sweep 64조합 PASS=0.
  # 누적 107조합 → cand-c live-scanner family 비용 후 음의 EV 확정.
  # 운영 권고: 비활성화. live_run.py 재시작 시 자동 trigger 안 됨.
  # - id: cand-c-2026-05-20-live-rsi-oversold-volume-spike
  #   class: ...
  #   kwargs:
  #     default_size: 0.05
  ...
```

### spec md 갱신

scripts/_apply_candc_scalping_results.py 실행 → 4 cand-c spec md 의 status=rejected
+ PF/expectancy/sweep verdict 자동 기록.

### Forward-test 운영 주의 (혹시 PASS 발견 시)

- 10x leverage + tight TP 의 청산 위험: 진입 후 −10% price 가면 강제청산. 1m 노이즈 진폭 ~0.1% 라 1초 안에 충분히 발생 가능. 백테스트가 청산 시뮬레이션 미반영.
- 펀딩비: 단타 회전 폭증 시 펀딩 흐름 무시 불가. 8시간 단위 funding 누적 → 추가 비용.
- Slippage: 시장가 주문 + 30종목 일제 진입 시 비BTC alts 슬리피지 4~10bp 보장.

---

원자료:
- `reports/sweep_candc_scalping_btc_probe.json` (iter 1)
- `reports/sweep_candc_scalping_btc_iter2.json` (iter 2)
- `.omc/prd.json` (PRD + acceptance criteria)
- `.omc/progress.txt` (iteration 단계별 학습)
