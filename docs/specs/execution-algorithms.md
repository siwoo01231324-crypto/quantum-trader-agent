# 실행 알고리즘 스펙 (TWAP/VWAP/지정가/시장가 + KRX 단일가 핸들러)

## 1. 목적
주문 실행 슬리피지를 최소화하기 위해 다양한 실행 알고리즘을 선택 가능한 인터페이스로 제공한다. KRX 시장의 단일가 매매 구간(시가/종가/서킷브레이커)을 안전하게 처리한다.

## 2. 인터페이스

```python
class ExecutionAlgorithm(Protocol):
    name: str
    def plan(self, parent_order: ParentOrder, market_state: MarketState) -> list[ChildOrder]: ...
    def on_fill(self, fill: Fill) -> list[ChildOrder]: ...
    def on_market_tick(self, tick: Tick) -> list[ChildOrder]: ...
    def cancel(self) -> None: ...
```

- `ParentOrder`: symbol, side, qty, time_in_force, algo_params, deadline
- `ChildOrder`: 전송 단위 주문 (limit/market, qty, price, post_only)
- `Fill`: 체결 통지 (qty, price, ts, fee)

## 3. 구현 알고리즘

| 알고 | 설명 | 핵심 파라미터 |
|------|------|---------------|
| Market | 즉시 시장가 단일 전송 | - |
| Limit  | 단일 지정가 (TIF=DAY/IOC/FOK) | price, tif |
| TWAP   | 시간 균등 분할 | duration, slice_count, jitter |
| VWAP   | 과거 거래량 곡선 추종 | profile_window, participation_rate |

### 3.1 슬리피지 모델 (플러그인 포인트)
`SlippageModel.estimate(child_order, market_state) -> float` 형태. 백테스트/모의체결 엔진에서 주입.
구현체 예: ConstantBps, SquareRootImpact (`I = k * sigma * sqrt(qty/ADV)`).

## 4. KRX 단일가 핸들러
- 시가 단일가 (08:30~09:00), 종가 단일가 (15:20~15:30), 시간외 단일가 (16:00~18:00).
- 서킷브레이커/VI(변동성 완화장치) 발동 시 일시 정지.
- 핸들러는 단일가 구간 진입 시 자식 주문을 **대기 큐**에 적재 → 정규 시간 재개 시 재전송 또는 단일가 호가로 직접 참여.
- 정책: `SingleAuctionPolicy.{WAIT, PARTICIPATE_AT_REFERENCE, CANCEL}`.

## 5. 모의 체결 엔진 (테스트용)
- `MockMatchingEngine`: 가격·수량을 받고 부분/완전 체결 통지를 알고에 전달.
- 결정적 시드 기반 → 재현 가능한 단위 테스트.

## 6. 디렉토리 구조
```
src/execution/
  base.py          # ExecutionAlgorithm 프로토콜, 데이터클래스
  market.py        # MarketAlgo
  limit.py         # LimitAlgo
  twap.py          # TWAPAlgo
  vwap.py          # VWAPAlgo
  krx_handler.py   # 단일가 대기 큐 + 정책
tests/test_execution.py
```

## 7. 출처
- Nautilus Trader execution algorithms: https://nautilustrader.io/docs/latest/concepts/execution.html
- QuantConnect LEAN execution models: https://www.quantconnect.com/docs/v2/writing-algorithms/algorithm-framework/execution
- KRX 단일가 매매 안내: https://global.krx.co.kr/contents/GLB/04/0403/0403010000/GLB0403010000.jsp
- Almgren-Chriss optimal execution: https://www.smallake.kr/wp-content/uploads/2016/03/optliq.pdf
- KRX VI(변동성완화장치): https://regulation.krx.co.kr/contents/RUL/03/03020100/RUL03020100.jsp
