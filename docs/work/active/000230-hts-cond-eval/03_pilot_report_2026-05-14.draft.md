# HTS 검색식 1일 pilot — 2026-05-14

옵션 A: 오늘 KIS 1분봉 fetch + 3종 검색식 evaluator + +2/-2% 시뮬레이션.

- 필터된 universe: 281 종목 (price 900~10,000 + 5d cumvol ≥ 500k)
- 1m 데이터 적재: 281 종목
- E 체결강도: pilot 1차 placeholder (power_ratio=100.0 통과 처리)

## 결과 요약

| profile | signals | trades | wins | win_rate | avg_pnl | decision |
|---------|--------:|-------:|-----:|---------:|--------:|----------|
| dts | 28 | 28 | 14 | 50.0% | -0.057% | **reject** |
| wait5m | 26 | 26 | 15 | 57.7% | +0.228% | **reject** |
| swing | 36 | 36 | 20 | 55.6% | +0.127% | **reject** |

## 한계
1. 표본 1일 → 통계 신뢰도 낮음. 옵션 B 로 5거래일 누적 후 본 검증 필요.
2. E 체결강도 1차 placeholder. KIS `inquire-price` `tday_rltv` 분봉 시점별 누적 재구성 미구현.
3. 단타 H "지지" 단일 봉 1회 기준. 키움 내부 정의 단정 불가.

## 출처
- 검색식 캡처 3장: 사용자 제공 (2026-05-14, 이슈 #230)
- KIS FHKST03010200 (분봉), 한국 lake `/lake/ohlcv/freq=1m/`