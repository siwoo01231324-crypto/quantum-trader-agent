# 옵션 B: 5거래일 누적을 위한 cron 확장 (#230)

> 옵션 A (오늘 1일 pilot, `scripts/run_hts_cond_pilot.py`) 의 sanity 가 적절(신호 ≥ 0건 + 채택 후보 수익률 추세)할 경우만 진행. 옵션 A 결과 후 결정.

## 목적
KIS 분봉 API 는 당일만 반환하므로 (#97 v5 확정), 5거래일 백테스트를 위해서는 **매일 fetch 누적** 외 방법 없음. #152 의 기존 cron 은 KOSPI200 round-robin top-30 풀만 fetch → 단타 검색식 universe (저가주 ~수백) 와 미스매치.

본 문서는 단타 universe 를 매일 fetch 하는 별도 cron 설정 가이드.

## 옵션 B.1 — 별도 daemon (권장)

### 구성
1. **새 스크립트**: `scripts/cron_fetch_screener_universe.py`
   - FDR snapshot → 단타 검색식 A+B+C EOD 근사 필터 → 후보 universe
   - 각 종목 KIS 1m fetch → `lake/ohlcv/freq=1m/year=YYYY/month=MM/symbol=XXX/` 적재
   - cron_loop 패턴 (KST 16:00 1회/일, 평일만)

2. **새 docker service** (또는 Windows Task Scheduler 항목):
   ```yaml
   # docker-compose.live.yml 추가
   screener-fetch-cron:
     build: { context: ., dockerfile: Dockerfile }
     image: qta-phase2:latest
     entrypoint: ["/bin/bash", "/app/scripts/screener_fetch_loop.sh"]
     env_file: ./.env
     environment:
       TZ: Asia/Seoul
       FETCH_HOUR_KST: "16"   # KRX 마감 30분 후
       LAKE_DIR: /data/lake
       UNIVERSE_FILTER: "dts"  # dts | wait5m | swing | all
     volumes:
       - ./lake:/data/lake
       - ./logs/screener-fetch:/data/logs
     restart: unless-stopped
     deploy:
       resources:
         limits: { memory: 512M }
   ```

3. **Windows Task Scheduler 대안** (docker 안 쓸 때):
   ```
   - Trigger: Daily 16:00 KST, weekday only
   - Action: python D:\project\quantum-trader-agent\.worktree\000230-hts-cond-eval\scripts\cron_fetch_screener_universe.py
   - Working dir: D:\project\quantum-trader-agent
   - Run with highest privileges: no
   ```

### 시작일 / 5거래일 완료 추정
- 시작: 다음 영업일 첫 fetch (예: 2026-05-15 16:00 KST)
- 5거래일 누적 완료: 2026-05-22 16:30 KST (5/15, 5/18, 5/19, 5/20, 5/21 + 약간의 5/22)
- 본 검증 백테스트 실행: 2026-05-22 이후

## 옵션 B.2 — 기존 #152 cron 확장 (비추)

`scripts/cron_fetch_kis_daily.py` 에 `--screener-universe` 플래그 추가 → FDR 필터된 universe 사용.

**단점**:
- 기존 #133 Phase 2 운영 (KIS 모의계좌 4주) 의 KOSPI200 30 풀과 충돌
- universe 가 매일 바뀌므로 5거래일 동안 동일 종목 누적이 안 됨 (날짜별 다른 set)
- 5/15 에 fetch 한 종목이 5/16 universe 에 없으면 5/16 시점 데이터 없음

**결론**: B.1 신규 daemon 으로 가는 게 안전.

## 5거래일 누적 후 본 검증

```bash
# 워크트리 안에서
python scripts/run_hts_cond_pilot.py \
  --use-fdr \
  --skip-fetch \           # lake 의 누적 데이터 사용
  --multi-day 5            # 5거래일 walk-forward (옵션 A 의 1일을 5일로 확장 — 추가 구현 필요)
```

⚠️ `--multi-day` 는 현 pilot 스크립트에 미구현. 옵션 A 결과 후 본 검증 단계에서 추가.

## 운영 체크리스트

- [ ] `scripts/cron_fetch_screener_universe.py` 작성 (옵션 A 결과가 채택 후보일 때만)
- [ ] `scripts/screener_fetch_loop.sh` 작성 (docker entrypoint, KST cron 패턴)
- [ ] docker-compose.live.yml 에 service 추가 OR Windows Task Scheduler 설정
- [ ] 5거래일 후 본 검증 백테스트 실행
- [ ] 결과 정식 리포트 → `docs/research/hts-cond-eval-2026-05.draft.md` 승격

## 출처
- #152 KIS 1분봉 fetch cron 인프라
- #133 Phase 2 운영 컨테이너 구성 (`docker-compose.live.yml`)
- KIS 분봉 API 당일 제한 확정: #97 v5 검증
- 검색식 캡처 3장: 사용자 제공 (2026-05-14, 이슈 #230)
