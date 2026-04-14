# Backtest Summary Prompt

다음 백테스트 결과 JSON 을 1~2문단으로 해석해 달라.

- 전략: {{strategy}}
- 기간: {{period}}
- 지표: {{metrics}}

요구사항:
1. Sharpe, MDD, 체결 건수를 해석하라 (절대 수치가 아닌 비교/맥락).
2. 잠재적 과적합 신호를 1문장으로 짚어라.
3. walk-forward/OOS 검증이 필요한 구간이 있다면 제시하라.
4. 한국어로 작성, Markdown 불필요.
