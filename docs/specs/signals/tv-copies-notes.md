---
type: work-note
id: tv-copies-notes
name: TradingView 카피본 작업 노트
created: 2026-05-22
owner: siwoo
status: shipped
tags:
- tradingview
- pine-script
- indicator-copy
- reverse-engineering
---

# TradingView Indicator 카피본 작업 노트 (2026-05-22)

## 배경

사용자가 사용 중이던 두 TradingView 보조지표가 **closed-source 체험판 (만료 일정 있음)** 이라, 만료 전 우리가 직접 Pine Script 로 복제해서 영구 사용 가능하게.

| 원본 indicator | 우리 카피본 위치 |
|---|---|
| HMA - 훌 이동평균선 | `docs/specs/signals/hull-ma.pine` |
| [Trendlines] | `docs/specs/signals/trendlines.pine` |

## 작업 방식

원본 source code 없음 → **출력값 + screenshot ground truth + 사용자 반복 피드백** 으로 역공학.

### HMA 작업

| 단계 | 행동 | 결과 |
|---|---|---|
| 1 | 표준 Hull MA 공식 v1 | length 21/55 — 원본과 값 완전 다름 |
| 2 | 사용자가 settings screenshot 제공 | length 200, Show as Band, Line Thickness 1 등 확정 |
| 3 | v5 — settings 그대로 매칭 | MHULL≈81,288 SHULL≈81,202 (BTCUSDT 1h) 검증 OK |

**최종 코드 (v5)**:
- `hma(src, len) = WMA(2*WMA(src, len/2) - WMA(src, len), √len)` (Alan Hull 1994)
- MHULL = HMA(close, 200), SHULL = HMA(close, 240)
- Band = MHULL ~ SHULL fill (transparency 40)
- Color = MHULL slope (up=청록 #00C8B4 / down=핑크 #FF506E)

### Trendlines 작업

| 단계 | 행동 | 결과 |
|---|---|---|
| 1 | raw line 데이터 (502 line) 추출 → 3 parallel channel 추정 | v2 — 차트 회색 line 수백개로 덮음, 완전 실패 |
| 2 | 사용자 이미지로 ground truth 재정의 — pivot 1개당 1 추세선 | v3 — TOP/하단 카드·노란 박스 추가 |
| 3 | 빈도·색·박스 등 사용자 피드백 5회 iteration | v4~v6 |
| 4 | "얼추 맞는다" 시점 | **v7 (final)** |
| 5 | 원본 settings screenshot 받음 + 추가 옵션 적용 시도 (v8, v9, v10) | 안 맞음 → v7 로 롤백 |

**최종 코드 (v7)** — `trendlines.pine`:
- Pivot lookback 10 (high/low fractal, ta.pivothigh/low)
- 두 인접 pivot lows 잇기 → 상승 추세선 (빨강 transparency 30, width 3)
- 두 인접 pivot highs 잇기 → 하강 추세선 (완전 초록 color.green)
- Pivot 마다 ▲▼ 마커 (size.tiny)
- 추세선 break 시 Target 라벨 (up=초록 / down=빨강, 1:1 length projection)
- 최근 60개 추세선만 유지 (line.delete prune)

## 한계 (솔직)

- **closed-source 100% 복제 불가** — 알고리즘 내부 룰 (예: ZigZag dedup 방식, ATR strength filter 유무) 추측.
- **시각적으로 "얼추 매칭"** 까지만. 1:1 동일 보장 X.
- 원본 indicator 만료 후 차이 발견 시 settings 토글로 fine-tune 필요.

## 사용 절차

1. 레포 clone 후 `docs/specs/signals/hull-ma.pine` 또는 `trendlines.pine` 열어 코드 통째 복사
2. TradingView → Pine Editor → New indicator → 붙여넣기
3. Save → 차트에 add
4. 무료 플랜 indicator 한도 (2개) 주의

## 관련 코드

Python 신호 모듈도 함께 작업 (별도 PR #275 머지 완료):
- `src/signals/hull_ma.py` — Python 동등 구현
- `src/signals/trendlines.py` — Python 동등 구현
- `docs/specs/signals/hull-ma.md`, `trendlines.md` — Python 모듈 spec

`.pine` 파일은 **TradingView 차트 시각화 전용**, Python 모듈은 **백테스트·자동거래 전용**. 두 코드가 같은 알고리즘 mirror.

## 다음 세션 참고

- 원본 indicator 가 만료된 후에도 우리 `.pine` 으로 계속 사용 가능
- HMA 의 length 200 은 1시간봉·4시간봉 권장. 짧은 TF 에서는 lag 큼
- Trendlines 의 lookback 10 도 1시간봉 기준 — 다른 TF 에서는 settings 조정
- v8~v10 (ATR filter, ZigZag, extend.right 시도) 는 원본과 안 맞아 폐기 — 다시 시도해도 같은 결과 가능, 신중하게
