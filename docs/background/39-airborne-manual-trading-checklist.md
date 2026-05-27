---
type: research
id: 39-airborne-manual-trading-checklist
name: 에어본 시그널 수동 매매 보조 체크리스트 (프랙탈/FVG/OB/매물대/추세/거래량/MTF)
created: 2026-05-26
updated: 2026-05-26
owner: siwoo
status: reference
sources:
- "https://www.youtube.com/watch?v=HiH15zxEDnk (원본 영상 — 트레이딩 100일 훈련, 오더블럭/FVG/추세선/채널/페이크아웃)"
- "https://innercircletrader.net/tutorials/fair-value-gap-trading-strategy/ (ICT FVG 6-Step Strategy)"
- "https://innercircletrader.net/tutorials/ict-order-block/ (ICT Order Block)"
- "https://www.tradingview.com/support/solutions/43000591663-williams-fractal/ (Williams Fractal)"
- "https://phemex.com/academy/what-is-williams-fractal (Williams Fractal Crypto)"
- "https://goodcrypto.app/ultimate-guide-to-volume-profile-vpvr-vpsv-vpfr-explained/ (VPVR Complete Guide)"
- "https://trendrider.net/blog/multi-timeframe-analysis-explained (MTF Confluence)"
- "https://fibalgo.com/education/volatility-cycle-trading-strategy-bollinger-squeeze (Bollinger Squeeze Mean Reversion)"
- "https://tmsstory.co.kr/how-to-find-bitcoin-order-block-strategy/ (TMSStory — 비트코인 오더블럭 한국어)"
- "https://tommytradingchannel.gitbook.io/tommy_trading/undefined-6 (Tommy Trading TV — 매물대/오더블럭 한국어)"
tags:
- airborne
- manual-trading
- ict
- volume-profile
- order-block
- fvg
- fractal
- trend-analysis
- mtf
---

# 에어본 시그널 수동 매매 보조 체크리스트

[[live-airborne-bb-reversal]] 가 발화한 시각에 자동 진입하지 않고 사람이 한 번 더
판단해서 승률을 끌어올리려 할 때 보는 체크리스트. 5y 자동 백테스트가 천장 PF 1.18
(BBW+wick) 임이 확인된 만큼, 수동 매매에서는 *추가 컨플루언스* 로 알파 보강이 가능.

웹 리서치 (web-research-specialist, 2026-05-26) 결과를 정리.

## 1. 프랙탈 (Bill Williams Fractal)

**체크 질문**: "BB 하단 이탈 봉 또는 그 직후 봉에 1h 저점 프랙탈(↑ 표시)이 확정됐는가?"

**왜 중요한가**: 프랙탈은 5봉 구조로 시장이 해당 저점을 구조적 전환점으로 공식 인식했음을 표시하며, SL 기준선이 객관적으로 정해진다.

**TradingView 사용법**:
1. 인디케이터 검색 → `Williams Fractal` (내장) 추가
2. 1h 차트: 저점 프랙탈은 중심봉 저가가 양옆 각 2봉보다 낮을 때 ↑ 표시
3. 4h 로 전환해 동일 가격대에 4h 프랙탈도 있으면 지지 강도 2배
4. 프랙탈 미확정 상태(아직 오른쪽 2봉 미완성)이면 봉 2개 더 대기

**참고**:
- [TradingView Support — Williams Fractal](https://www.tradingview.com/support/solutions/43000591663-williams-fractal/)
- [Phemex Academy — Williams Fractal Crypto](https://phemex.com/academy/what-is-williams-fractal)

## 2. FVG (Fair Value Gap / 공정가치 갭)

**체크 질문**: "진입가 기준 아래 1.5% 이내에 미채워진 불리시 FVG 가 있는가, 또는 진입가 자체가 FVG 구간 안에 있는가?"

**왜 중요한가**: FVG 는 급락 중 3봉 사이에 생긴 거래 공백 구간으로, 시장은 이 구간을 재방문 목표로 인식하여 자연스러운 반등 지지대 역할.

**TradingView 사용법**:
1. 연속 3봉 확인: 1봉 고가(A)와 3봉 저가(B) 사이에 2봉이 완전히 들어가지 않으면 A~B 구간이 FVG
2. 자동화: `Fair Value Gap` 또는 `Auto FVG` 또는 `FVG [TakingProphets]` 인디케이터
3. TP 활용: 불리시 FVG 상단 = 1차 TP 후보
4. SL 기준: 가격이 FVG 하단을 완전히 이탈하면 구조 무효 → 청산

**참고**:
- [ICT FVG 6-Step Strategy](https://innercircletrader.net/tutorials/fair-value-gap-trading-strategy/)
- [TradingView — FVG in Crypto Complete Guide](https://www.tradingview.com/chart/BTCUSDT/nNV1Qwby-Fair-Value-Gap-FVG-in-Crypto-The-Complete-Guide/)

## 3. 오더블럭 (Order Block)

**체크 질문**: "진입 구간 근처에 강한 임펄스 하락 직전의 마지막 양봉(불리시 OB)이 미취소 상태로 존재하는가?"

**왜 중요한가**: OB 는 기관이 대량 주문을 집행한 마지막 흔적. 미취소 불리시 OB 는 에어본 롱 진입 구간과 겹칠 때 기관 매수대와 동일한 레벨에 올라타는 효과.

**TradingView 사용법**:
1. 1h 차트에서 급락 구간 탐색 → 그 임펄스 시작 직전 양봉을 찾아 고가~저가 구간을 사각형으로 표시 (Draw Rectangle)
2. 자동화: `Order Blocks & Breaker Blocks Pro [ICT]` by tradingbauhaus
3. 유효 OB 3조건:
   - (a) 임펄스가 OB 전봉 고저를 완전히 돌파
   - (b) OB 와 임펄스 사이 FVG 존재
   - (c) 가격이 아직 해당 구간을 재방문하지 않음(신선한 OB)
4. 가격이 OB 50% 수준에서 반응하면 진입 타이밍

**참고**:
- [TMSStory — 비트코인 오더블럭](https://tmsstory.co.kr/how-to-find-bitcoin-order-block-strategy/)
- YouTube 한국어: "ICT 이론 유동성/FVG/OB" — [https://www.youtube.com/watch?v=eSp3OsEs9rQ](https://www.youtube.com/watch?v=eSp3OsEs9rQ)
- [ICT Order Block — innercircletrader.net](https://innercircletrader.net/tutorials/ict-order-block/)

## 4. 매물대 (Volume Profile / VPVR)

**체크 질문**: "진입가는 VPVR VAL 아래 또는 HVN 구간인가? 직하방 1% 이내에 두꺼운 LVN(에어포켓)이 없는가?"

**왜 중요한가**: LVN 에어포켓 위에서 진입하면 가격이 다음 HVN 까지 저항 없이 추가 하락 가능. POC 는 가장 자연스러운 1차 TP 레벨.

**TradingView 사용법**:
1. 인디케이터 검색 → `Volume Profile Visible Range` (VPVR, 내장)
2. 차트 좌측 수평 바: 폭 넓음 = HVN(지지/저항), 폭 좁음 = LVN(에어포켓)
3. 노란 선 = POC = 1차 TP. 파란 박스 = Value Area (VAH~VAL, 거래량 70%)
4. 크립토는 24/7 시장이라 일봉 세션보다 **주간(7일)** VPVR 가 더 신뢰성
5. 실전 규칙: 진입가 직하방 LVN 발견 시 포지션 사이즈 **50% 축소**

**참고**:
- [goodcrypto.app — VPVR 완전 가이드](https://goodcrypto.app/ultimate-guide-to-volume-profile-vpvr-vpsv-vpfr-explained/)
- [Tommy Trading TV — 매물대/오더블럭](https://tommytradingchannel.gitbook.io/tommy_trading/undefined-6)

## 5. 추세 종합 판단 (Multi-Timeframe Trend)

**체크 질문**: "4h EMA21 이 우상향이거나 수평인가? 1D 봉 구조가 HH-HL(상승) 또는 횡보인가? 하락 구조(LH-LL)는 아닌가?"

**왜 중요한가**: BB 평균회귀 전략은 횡보·박스권에서 승률이 집중되며, 상위 타임프레임 다운트렌드가 진행 중이면 BB 하단이 계속 아래로 당겨져 허위 반등 신호가 반복.

**TradingView 사용법**:
1. 주간(1W): 현재 가격이 주요 수평 지지선 위인지, 주간 EMA20 우상향인지
2. 일봉(1D): 직전 3~4개 고점·저점 체크. LH-LL 연속이면 에어본 롱 신호 비선호
3. 4h: EMA Length 21 추가 → 기울기 하락 + 가격이 EMA 아래 = 경고
4. 합격: 1W 중립 이상 + 1D 구조 중립 이상 + 4h EMA21 하락 기울기 아닐 것
5. 셋 중 둘 이상 위반이면 진입 금지, 하나 위반이면 사이즈 50% 제한

**참고**:
- [Multi-Timeframe Analysis: Filter 80% False Signals](https://trendrider.net/blog/multi-timeframe-analysis-explained)
- [altFINS — EMA Crypto Strategy](https://altfins.com/knowledge-base/ema-12-50-crossovers/)

## 6. 거래량 (Volume)

**체크 질문**: "BB 하단 이탈 봉의 거래량이 20봉 평균 대비 150% 이상인가? 이탈 직후 1~2봉의 거래량이 급감하는가?"

**왜 중요한가**: 고거래량 이탈 후 거래량 급감 패턴 = 매도 클라이맥스(패닉셀 소진)의 고전적 시그니처. 거래량 없는 조용한 하단 이탈은 추가 하락의 전형적인 전조.

**TradingView 사용법**:
1. 차트 하단 Volume 바에서 이탈 봉(빨간색) 높이를 전후 봉과 시각적으로 비교
2. `Volume MA` Length 20 추가 → 이탈 봉이 Volume MA 선을 얼마나 초과
3. 이탈 봉 이후 2봉: 거래량 급감 = 클라이맥스 확인 / 거래량 유지 = 매도 압박 지속
4. **펀딩레이트 병행 확인**: Binance/Bybit 펀딩레이트 극단적 음수(−0.05% 이하)이면 숏 청산 연쇄 반등 가능성 ↑
5. 주의: 거래량 급등이 뉴스 이벤트 동반 시 방향 확정 전까지 사이즈 50% 이하

**참고**:
- [FibAlgo — Bollinger Squeeze Mean Reversion](https://fibalgo.com/education/volatility-cycle-trading-strategy-bollinger-squeeze)
- [FMZ — Bollinger Mean Reversion Strategy](https://medium.com/@FMZQuant/bollinger-bands-mean-reversion-trading-strategy-dc80a7ff7a4f)

## 7. MTF 컨플루언스 (Multi-Timeframe Confluence)

**체크 질문**: "1h BB 하단 이탈 레벨이 4h 구조적 지지, 4h OB, 4h FVG 중 하나 이상과 동일 가격대(±0.5%)에 겹치는가?"

**왜 중요한가**: 복수의 시간축에서 같은 가격 레벨이 동시에 의미를 가질 때 기관 참여 가능성 ↑, 단일 TF 신호 대비 손절 확률 통계적으로 ↓.

**TradingView 사용법**:
1. 4h 차트에서 수평 지지선, OB, FVG 를 먼저 표시한 뒤 1h 로 전환
2. 에어본 신호 발생 가격이 4h 에서 표시한 구간 내에 있는지 확인
3. **컨플루언스 점수표** (3점 이상 진입 고려):

| 조건 | 점수 |
|---|---:|
| 4h 수평 지지선 ±0.5% | +1 |
| 불리시 FVG 내부 또는 상단 | +1 |
| 불리시 OB 구간 (미취소) | +1 |
| VPVR HVN 또는 POC 근접 | +1 |
| 1h 저점 프랙탈 확정 | +1 |
| 4h EMA21 우상향 | +1 |
| 이탈 봉 거래량 150% 이상 | +1 |

**참고**:
- [ICT Order Block Confluence](https://innercircletrader.net/tutorials/ict-bullish-order-block/)
- [trendrider.net — MTF Confluence](https://trendrider.net/blog/multi-timeframe-analysis-explained)

---

## 진입 전 최종 7-ITEM 체크리스트

에어본 신호 발동 후 순서대로 체크. **1~2번은 GATE 조건** (실패 시 즉시 스킵). **3~7번은 점수제 필터**.

```text
[ ] GATE 1. 추세 확인
    - 1D 구조: HH-HL 유지 또는 횡보 (LH-LL이면 → 진입 금지)
    - 4h EMA21: 우상향 또는 수평 (하락 기울기이면 → 진입 금지)

[ ] GATE 2. 거래량 클라이맥스
    - 이탈 봉 거래량 > 20봉 평균의 1.5배
    - 이탈 후 1~2봉 거래량 감소 확인
    (미충족이면 → 진입 금지)

[ ] 3. 프랙탈 저점 확정
    - 1h 저점 프랙탈(↑) 생성 여부 확인
    - 4h 프랙탈 추가 확인 시 +1점 가중

[ ] 4. FVG 또는 OB 존재 확인
    - 진입가 ±1.5% 내에 불리시 FVG 또는 미취소 불리시 OB
    (없으면 → 다음 신호 대기)

[ ] 5. VPVR 위치 확인
    - 진입가가 VAL 아래 또는 HVN 구간
    - 직하방 1% 이내 LVN 없음
    (LVN 발견 시 → 사이즈 50% 축소)

[ ] 6. MTF 컨플루언스 점수
    - 위 점수표 3점 이상 → 정상 진입
    - 2점 → 사이즈 50% 제한
    - 1점 이하 → 스킵

[ ] 7. RR 확인
    - SL: 프랙탈 저점 -0.3% (또는 OB 하단)
    - TP1: VPVR POC 또는 BB 중심선
    - TP2: FVG 상단 또는 4h VAH
    - RR >= 2.0 이어야 진입 실행
    (RR 2.0 미만이면 → 그 신호 스킵, TP 억지 조정 금지)
```

---

## 추가 주의사항 (크립토 특유)

1. **펀딩레이트 확인 습관화**:
   - 극단적 음수 (−0.1% 이하) = 숏 과밀 → BB 하단 이탈 시 숏 청산 연쇄 V자 반등 가능성
   - 극단적 양수 = 롱 과밀 → 에어본 롱 신호 신중

2. **변별력 최고 게이트는 Volume + Zone**:
   - GATE 2 (Volume Climax) + #5 (VPVR Zone) 동시 충족 안 되는 신호는 허위 비율 두드러지게 높음

3. **40% 되돌림은 이미 구조적 필터**:
   - 에어본의 40% 되돌림 자체가 진입 타이밍 필터. 단, 강한 다운트렌드(GATE 1 실패)에서는 단순 데드캣 바운스로 끝나는 사례 반복.
   - **GATE 1 (추세) 은 타협 금지.**

4. **SL 타이트닝 금지**:
   - RR 2.0 미만이면 그 신호 스킵. SL 억지로 좁히면 노이즈에 털림.

## 관련

- [[live-airborne-bb-reversal]] — 자동 매매 전략 spec (v0)
- [[live-airborne-bb-reversal-v11]] — Pine v1.2 (close-기반 + ATR body)
- [[38-airborne-indicator-reverse-engineering]] — 에어본 인디케이터 역공학 기록
