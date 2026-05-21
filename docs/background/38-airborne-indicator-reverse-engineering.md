---
type: research
id: 38-airborne-indicator-reverse-engineering
name: 에어본(체험판) 인디케이터 역공학 — 출력값 관찰로 BB 평균회귀 진입 수식 도출
sources:
  - "TradingView Desktop CDP capture (BITMEX:BTCUSD.P 1H, 2026-05-20)"
  - "tradesdontlie/tradingview-mcp — https://github.com/tradesdontlie/tradingview-mcp"
  - "에어본 인디케이터 (TradingView 공개 ID PUB;0b920144158f4848ba5d506932a636d7, Pine v5, 작성자 비공개)"
  - "external-trading-lecture-techniques.md §7 — 강의에서 언급된 자체 도구 (코드 비공개)"
  - "live-mg-bb-reversal.md §1y 검증 결과 — 강의 기반 캔들 게이트 reformulation 의 PF<1 결과"
---

# 에어본(체험판) 인디케이터 역공학

> **요약**: 강의 강사가 배포하는 비공개 Pine v5 인디케이터 "에어본(체험판)" 의 진입 수식을 출력값 관찰만으로 도출. **HTF BB(=차트 TF 1H, 20-period, 2σ) 돌파 → 극값 추적 → 40% 되돌림 트리거 → 확정 close 발화** 의 3단 구조임을 확인. 시뮬레이션 결과가 실제 라이브 인디케이터의 `기준시가/극값/트리거가` 와 BitMEX 0.5-tick 반올림 범위 내 일치. Pine 소스 자체는 비공개 (`PUB;` 접두사로 작성자 잠금).

## 1. 배경 — 왜 이걸 역공학하는가

`external-trading-lecture-techniques.md` §7 은 강의의 자체 도구 "에어본 지표" 를 비공개 인디케이터로 분류하고 "외부 다운로드 가능 여부는 강의 결제자 한정. 본 프로젝트에서는 동일 시그널을 `src/signals/` 모듈로 직접 구현·검증해야 함" 으로 결론지었다.

그 후 [[live-mg-bb-reversal]] 이 **강의 일반 기법(BB 이탈 + 캔들 패턴 + reclaim)** 을 single-timeframe 으로 압축해 1y BTC+ETH sweep 으로 검증했고 **16 조합 모두 PF<1 (범위 0.62~0.83) 로 falsified** 됐다. 그러나 이 실험은 **강의의 일반적 서술** 에 기반한 reformulation 이지, 강사가 실제 사용하는 에어본 인디케이터의 **정확한 트리거 수식** 은 아니었다.

본 노트는 사용자가 라이브로 운용 중인 TradingView Desktop 차트에 적용된 에어본(체험판) 인디케이터를 CDP (Chrome DevTools Protocol) 로 들여다보고, 인디케이터가 차트에 노출하는 모든 출력값(plot · table · 메타)을 시계열로 관찰하여 내부 수식을 도출한 결과를 정리한다.

## 2. 방법

### 2.1 인디케이터 메타 (직접 노출)

`tradingview-mcp indicator get` 으로 다음 메타를 추출:

```
entity_id:    b12KFZ
pineId:       PUB;0b920144158f4848ba5d506932a636d7   ← 공개 잠금 스크립트
pineVersion:  5.0
features:     indicator, plot, str, ta, math, alertcondition, table, request.security
```

`PUB;` 접두사는 작성자가 소스 코드를 비공개로 발행한 TradingView 공개 ID. `pine get` 으로 소스 조회 시 빈 결과 → 원문 코드 접근 불가가 확정.

### 2.2 출력값 스냅샷 (2026-05-20 16:30 KST, BITMEX:BTCUSD.P 1H)

**indicator plots** (9개):

```
에어본 롱           = 0.0
에어본 숏           = 0.0
롱 대기             = 0.0
숏 대기             = 1.0   ← 현재 활성
HTF BB 상단         = 77,260.4
HTF BB 중심(20이평) = 76,780.3
HTF BB 하단         = 76,300.2
LTF BB 상단         = 77,367.7
LTF BB 하단         = 77,111.4
```

**on-chart table**:

```
상태:        🔴 에어본 숏 대기
기준 시가:   77,118.5
현재 극값:   77,393.0
트리거가:    77,283.0
5분 BB 상단: 77,367.5
5분 BB 하단: 77,111.5
```

### 2.3 좌표 정합

스냅샷 시점의 1H OHLCV 데이터 200봉을 동시 추출하여, 출력값이 어느 봉 어느 가격에 정합되는지 매핑:

| 출력 필드 | 값 | 정합 좌표 |
|---|---|---|
| 기준 시가 | 77,118.5 | 1H 봉 (idx 297, 14:00 KST) close = 77,118.7 ≈ 다음봉 open |
| 현재 극값 | 77,393.0 | 1H 봉 (idx 299, 16:00 KST, live) high = 77,392.8 |
| HTF BB 중심 | 76,780.3 | 1H 20-SMA(close) at idx 299 = 76,780.29 |
| HTF BB 상단 | 77,260.4 | 1H 20-SMA + 2×σ(20) at idx 299 = 77,260.4 |
| HTF BB 하단 | 76,300.2 | 1H 20-SMA - 2×σ(20) at idx 299 = 76,300.2 |
| LTF BB ≈ 5분 BB | 같은 1.4 차이 내 | 5m timeframe BB (`request.security` 호출) |

`HTF` 라는 명명에도 불구하고 **HTF BB = 차트 현재 TF(여기서는 1H) BB(20, 2σ)** 임이 정확히 일치하여 확정. 즉 "HTF" 는 인디케이터 입력 옵션의 라벨이며 차트 TF 와 다른 timeframe 을 의미하지 않는다. 실제로 다른 TF 를 쓰려면 인디케이터 설정에서 별도 입력을 줘야 할 가능성이 있으나, 현재 사용자 셋업에서는 차트 TF 와 동일.

### 2.4 트리거가 공식 도출

```
swing       = 극값 - 기준시가 = 77,393.0 - 77,118.5 = 274.5
pullback    = 극값 - 트리거가 = 77,393.0 - 77,283.0 = 110.0
ratio       = pullback / swing = 110.0 / 274.5 = 0.4007
```

40% 되돌림을 강하게 시사. 다른 비율(피보 0.382, 50%, 0.618 등) 어느 것도 매치하지 않음.

### 2.5 시뮬레이션 정합 검증

다음 알고리즘으로 200봉 1H OHLCV 위에서 인디케이터를 재현:

```python
state = None
for each bar i:
    if state is None:
        if bar.high crosses up through bb_upper:           # 첫 돌파봉
            state = "short_setup"
            base = bar.close
            extreme = bar.high
            breakout_bar = i
        elif bar.low crosses down through bb_lower:
            state = "long_setup"
            base = bar.close
            extreme = bar.low
            breakout_bar = i
    else:
        if state == "short_setup":
            extreme = max(extreme, bar.high)
            trigger = extreme - 0.4 * (extreme - base)
            if i > breakout_bar AND bar is confirmed AND bar.close <= trigger:
                fire("에어본 숏")
                state = None
        else:  # long_setup (symmetric)
            extreme = min(extreme, bar.low)
            trigger = extreme + 0.4 * (base - extreme)
            if i > breakout_bar AND bar is confirmed AND bar.close >= trigger:
                fire("에어본 롱")
                state = None
```

**현재 대기 상태 시뮬레이션 결과** (idx 299 live):

| 필드 | 시뮬레이션 | 실제 인디케이터 | 차이 |
|---|---|---|---|
| 기준시가 | 77,118.7 | 77,118.5 | +0.20 |
| 극값 | 77,392.8 | 77,393.0 | -0.20 |
| 트리거가 | 77,283.2 | 77,283.0 | +0.16 |

차이는 BitMEX BTCUSD.P 의 0.5 tick 반올림 범위(±0.5) 내. **모델 확정**.

### 2.6 발화 타이밍 검증

스냅샷 시점 live close = 77,265.8 (트리거 77,283 아래). 그럼에도 인디케이터 상태는 "에어본 숏(=1.0)" 이 아닌 "숏 대기(=1.0)" 유지 → 인트라바 발화 안 함, **확정 close 기준** 으로만 발화함을 확인.

## 3. 결론 — 추론된 인디케이터 사양

### 3.1 상태 머신

```
없음 ─┬─→ 숏 대기 ─→ 에어본 숏 ─→ 없음
      └─→ 롱 대기 ─→ 에어본 롱 ─→ 없음
```

### 3.2 전이 규칙

| 전이 | 조건 |
|---|---|
| 없음 → 숏 대기 | `bar.high ≥ bb_upper` AND `prev_bar.high < prev_bb_upper` |
| 없음 → 롱 대기 | `bar.low ≤ bb_lower` AND `prev_bar.low > prev_bb_lower` |
| 숏 대기 → 에어본 숏 | confirmed `bar.close ≤ trigger` AND `i > breakout_bar` |
| 롱 대기 → 에어본 롱 | confirmed `bar.close ≥ trigger` AND `i > breakout_bar` |

### 3.3 상태 변수

```
base    = 돌파봉 close (= 다음봉 open)
extreme = 돌파 이후 max(high) 또는 min(low)
trigger = extreme ∓ 0.4 × |extreme - base|     # 숏=-, 롱=+
```

### 3.4 보조 출력 (의미 미확정)

- `LTF BB 상단/하단` (=5분 BB): `request.security` 로 별도 산출. 차트 표시 외 발화에 관여하는지 본 1회 스냅샷으로는 불확정. 차후 스트림 관찰 필요.
- `🔴 / 🟡 / 🟢` 상태 색상: 신뢰도 등급 추정 (LTF BB 와 트리거가의 상대 위치가 후보), 현재 스냅샷만으로는 불확정.

## 4. 한계와 후속 검증 필요 항목

### 4.1 단일 스냅샷의 한계

- **숏 setup 만 관찰됨**. 롱 setup 의 대칭성은 수식 가정이며 시뮬레이션으로만 확인 — 라이브로 롱 대기 상태일 때 동일한 정합 검증 필요.
- **시간 흐름 관찰 없음**. 봉이 진행되며 `극값` 이 어떻게 갱신되는지, 신호 발화 직후 어떤 상태로 전이하는지 (즉시 새 setup 받는지 cooldown 인지) 미확인.
- **LTF(5분) BB 의 역할** 미확정. 단순 표시인지, 발화 필터인지, 진입 가격 산정에 쓰이는지 본 노트 범위 밖.

### 4.2 시뮬레이션의 한계

- 200봉(약 8일) 1H 데이터 위에서만 정합 확인. **장기 안정성**(인디케이터 파라미터가 변할 가능성, 5y 백테스트 대비) 별도.
- **승률·기대값·PF 등 알파 지표는 본 노트에서 평가하지 않음**. 본 노트는 "수식이 무엇인가" 까지만 다룸. 알파 평가는 [[live-airborne-bb-reversal]] 의 백테스트 단계에서.

### 4.3 자매 전략 결과의 함의

[[live-mg-bb-reversal]] 의 1y 결과 (BTC+ETH, 1m+15m, 16 조합 PF=0.62~0.83) 는 "BB 이탈 + 캔들 패턴 게이트" 의 음의 엣지를 확정했다. **본 인디케이터의 40% 되돌림 게이트가 캔들 패턴 게이트보다 통계적으로 우수한지는 별개의 가설**이며, [[live-airborne-bb-reversal]] spec 의 사전등록 가설로 분리하여 검증한다.

## 5. 강의에서의 위치 — MG 기법과의 관계

`external-trading-lecture-techniques.md` §1 의 MG 기법은 다음 세 단계로 서술된다:

1. 4시간봉 BB 상단/하단 종가 이탈 확인
2. 15분/5분봉 반전 캔들 패턴 또는 추세선 돌파 더블 확인
3. 진입

본 역공학 결과의 **40% 되돌림 트리거** 는 강의 §1 의 어느 단계에도 명시되어 있지 않다. 강의는 "반전 캔들" 또는 "추세선 돌파" 를 트리거로 가르치는 반면 인디케이터는 **수치적 되돌림 비율(40%)** 을 트리거로 사용한다. 즉:

- **강의(서술) ≠ 인디케이터(실제 구현)** — 강사가 가르치는 것과 도구가 하는 것이 다르다.
- 본 프로젝트가 [[live-mg-bb-reversal]] 에서 검증한 것은 **강의 서술 버전** 이었고, 이는 falsified.
- **인디케이터 실제 버전(40% 되돌림)** 은 미검증이며, 본 노트가 그 검증을 위한 사양 도출의 1단계.

## 6. 차차 단계

1. **롱 setup 검증** (필요 시 라이브 관찰 또는 historical 1H 데이터에서 lower-band 돌파 발생 후 인디케이터 출력 재현)
2. **LTF BB 역할 실험** — `tradingview-mcp stream values` 로 분 단위 관찰. 트리거 부근에서 LTF BB 의 변화와 발화 여부의 상관 측정.
3. **재진입/쿨다운 규칙** — 신호 발화 직후 인디케이터 상태 캡처 (live 관찰).
4. **상태 색상(🔴/🟡/🟢) 의 의미** — 여러 시점 스냅샷으로 패턴 추출.
5. **Pine 재구현 + 시각 비교** — 추론 사양을 `src/backtest/strategies/live_airborne_bb_reversal.py` 와 `src/signals/airborne_bb_reversal.py` 로 구현 후 동일 차트에 오버레이하여 신호 일치율 측정.
6. **5y 백테스트** — `scripts/eval_live_scanners_5y.py` 와 동일 조건. 게이트: **PF > 1.0 AND expectancy/trade > 0**. 미통과 시 spec `status: rejected`.

(2)~(4)는 본 노트의 한계 4.1 해소 작업이며, [[live-airborne-bb-reversal]] spec 의 후속 작업 섹션과 연결된다.

## 7. 도구·인프라

본 역공학에 사용된 도구:

- **TradingView Desktop** (MSIX, Windows) — `--remote-debugging-port=9222` 로 재실행 시 CDP 노출
- **tradesdontlie/tradingview-mcp** (Node.js, MIT) — CDP 위에 `tv values`, `tv data tables`, `tv indicator get`, `tv ohlcv`, `tv screenshot` 등의 CLI/MCP 도구 제공
- **Python REPL** — 수식 정합 검증, 시뮬레이션
- 본 프로젝트 표준 도구가 아니며 강의 인디케이터의 출력 관찰 전용. `src/` 내 코드 의존성 없음.

설치 절차 (재현 시):

```powershell
# 1. TradingView 재실행
Stop-Process -Name TradingView -Force
Start-Process "<TV_EXE_PATH>" --remote-debugging-port=9222

# 2. tradingview-mcp clone + install
git clone https://github.com/tradesdontlie/tradingview-mcp.git
cd tradingview-mcp
npm install

# 3. 단발 실행 예
node src/cli/index.js values
node src/cli/index.js data tables
node src/cli/index.js indicator get <entity_id>
```

## 8. 윤리 고지

- 본 노트는 인디케이터의 **출력값을 관찰하여 동작 수식을 추론**한 결과이며, 보호된 Pine 소스 코드를 복호화·우회·탈취하지 않았다.
- 강사·강의·인디케이터 작성자·강의 결제자 모두에 대한 비방·평판 훼손 의도 없음.
- 본 노트의 시뮬레이션 결과를 **알파의 유효성 증명** 으로 해석하지 말 것. 알파는 별도 5y 검증을 거치며, 자매 [[live-mg-bb-reversal]] 가 같은 가족의 변형 가설이 falsified 됐음을 보였다는 사실은 본 인디케이터 검증의 사전 base rate 가 낮음을 시사.

## 9. 관련

- `external-trading-lecture-techniques.md` — 강의 원본 기법 정리 (repo-root)
- [[live-mg-bb-reversal]] — 강의 서술 버전의 reformulation, 1y rejected
- [[live-bb-lower-bounce]] — BB 평균회귀 가족의 단순 reclaim 버전, 5y rejected
- `[[live-airborne-bb-reversal]]` — 본 역공학 결과의 전략화 (status: draft, 본 노트와 동시에 작성)
- [[live-universe-scanner-paradigm]] — 본 전략이 속할 패러다임 spec

---

## 출처

1. TradingView Desktop CDP capture (2026-05-20, BITMEX:BTCUSD.P 1H) — 본 프로젝트 외부 도구로 추출, 결과는 본 노트 §2 에 인용
2. tradingview-mcp — https://github.com/tradesdontlie/tradingview-mcp (MIT, 비공식 비제휴 도구)
3. 에어본(체험판) 인디케이터 — TradingView Pine Script ID `PUB;0b920144158f4848ba5d506932a636d7`, 작성자 비공개, v5.0
4. `external-trading-lecture-techniques.md` — repo-root 노트, 강의 원본 기법 정리
5. `docs/specs/strategies/live-mg-bb-reversal.md` — 강의 기반 reformulation 의 1y 검증 결과
