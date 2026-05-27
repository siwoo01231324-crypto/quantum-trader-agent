"""qta-airborne-daemon FIRE 라인 polling listener.

daemon (`scripts/airborne_alert_daemon.py`) 은 docker container 안에서 매시간
USDT-perp 상위 100 종목에 대해 v1.2 airborne BB-reversal 시그널을 평가하고
FIRE 라인을 stdout 으로 찍는다. 본 listener 는 그 docker logs 를 polling 해서
새 FIRE 들을 추출하고, 이전에 본 것은 dedup 으로 걸러 list 로 반환한다.

dashboard 의 ``_parse_airborne_fire_line`` + ``_parse_airborne_fires_from_docker_logs``
와 동일 파싱 룰 (KST → UTC, PR #323 fix). dashboard 에서 import 하지 않는
이유: 의존 방향 분리 (live → dashboard 안 됨).

Usage::

    listener = AirborneFireListener()
    listener.start_at(datetime.now(timezone.utc))
    # 매 bar boundary 또는 strategy.on_bar 안에서 호출:
    for fire in listener.poll_new():
        ...
"""
from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

_KST = ZoneInfo("Asia/Seoul")

# daemon 컨테이너의 logging.basicConfig(format="%(asctime)s ...") 출력 포맷.
# 예: ``2026-05-23 02:00:33,327 INFO airborne_alert_daemon — FIRE CBRSUSDT
# long @ close=264.52 trigger=263.156``
_AIRBORNE_FIRE_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+ .*FIRE (\S+) (long|short) "
    r"@ close=([\d.]+) trigger=([\d.]+)"
)


@dataclass(frozen=True)
class FireRecord:
    """단일 FIRE 이벤트. ``ts`` 는 *항상 UTC tz-aware*."""

    ts: datetime
    symbol: str
    side: str         # 'long' | 'short'
    fire_close: float
    trigger: float

    def key(self) -> tuple[str, str, str]:
        """dedup 키 — (UTC ISO, symbol, side)."""
        return (self.ts.isoformat(), self.symbol, self.side)

    def kst_hour(self) -> int:
        """KST hour (0~23) — strategy 의 시각 게이트에서 사용."""
        return self.ts.astimezone(_KST).hour


def _parse_fire_line(line: str) -> FireRecord | None:
    """단일 로그 라인 → FireRecord, 매칭 실패 시 None.

    daemon container 는 ``TZ=Asia/Seoul`` 이라 ``%(asctime)s`` 가 KST 로컬 시각
    으로 찍힘. parser 는 KST 로 인식 후 UTC 로 변환 (PR #323 fix 와 동일 룰).
    """
    m = _AIRBORNE_FIRE_RE.match(line)
    if not m:
        return None
    try:
        ts_kst = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=_KST,
        )
    except ValueError:
        return None
    return FireRecord(
        ts=ts_kst.astimezone(timezone.utc),
        symbol=m.group(2),
        side=m.group(3),
        fire_close=float(m.group(4)),
        trigger=float(m.group(5)),
    )


def _read_docker_logs(
    container: str, since_utc_iso: str, timeout: float = 15.0,
) -> str:
    """``docker logs <container> --since <Z>`` stdout + stderr 텍스트.

    daemon 미가동 / docker CLI 부재 / cp949 console 등 모든 실패에서
    empty string. caller 는 graceful — never raise.

    since_utc_iso 는 반드시 ``Z`` suffix (UTC 명시). 그래야 daemon-local TZ
    로 해석되어 9h 차이 나는 사고를 안 일으킴 (PR #323).
    """
    try:
        p = subprocess.run(
            ["docker", "logs", container, "--since", since_utc_iso],
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return ""
    if p.returncode != 0:
        return ""
    return (p.stdout or "") + (p.stderr or "")


class AirborneFireListener:
    """qta-airborne-daemon docker logs polling listener.

    Lifecycle:
      1. ``listener = AirborneFireListener()`` — 객체 생성
      2. ``listener.start_at(now_utc)`` — 활성화 시각. 이전 fire 는 무시
      3. ``listener.poll_new()`` — 매 호출마다 새 fire 들 반환 (dedup)

    Dedup: 한 번 반환한 ``FireRecord.key()`` 는 다시 반환하지 않음.
    메모리는 ``dedup_window_hours`` 시간 이상 지난 key 를 자동 prune.

    Subprocess hook: ``_read_logs`` / ``_now_utc`` 는 unit test 에서
    monkeypatch 로 시간·로그 fixture 주입 가능.
    """

    def __init__(
        self,
        *,
        container_name: str = "qta-airborne-daemon",
        dedup_window_hours: float = 24.0,
        poll_lookback_minutes: float = 5.0,
    ) -> None:
        if dedup_window_hours <= 0:
            raise ValueError(
                f"dedup_window_hours > 0 required, got {dedup_window_hours}",
            )
        if poll_lookback_minutes < 0:
            raise ValueError(
                f"poll_lookback_minutes >= 0 required, got {poll_lookback_minutes}",
            )
        self.container_name = container_name
        self._dedup_window = timedelta(hours=dedup_window_hours)
        self._poll_lookback = timedelta(minutes=poll_lookback_minutes)
        self._processed: dict[tuple[str, str, str], datetime] = {}
        self._start_at: datetime | None = None

    # ── Lifecycle ──────────────────────────────────────────────────────────
    def start_at(self, ts_utc: datetime) -> None:
        """Listener 활성화 시각 — 이 시각 이전의 fire 는 무시."""
        if ts_utc.tzinfo is None:
            raise ValueError("start_at(): ts_utc must be tz-aware UTC")
        self._start_at = ts_utc.astimezone(timezone.utc)
        self._processed.clear()

    @property
    def started(self) -> bool:
        return self._start_at is not None

    def processed_count(self) -> int:
        """현재 dedup table 크기 — diagnostics 용."""
        return len(self._processed)

    # ── Test hooks ─────────────────────────────────────────────────────────
    def _read_logs(self, since_iso: str) -> str:
        """Subprocess hook — 테스트에서 monkeypatch."""
        return _read_docker_logs(self.container_name, since_iso)

    def _now_utc(self) -> datetime:
        """현재 시각 hook — 테스트에서 monkeypatch."""
        return datetime.now(timezone.utc)

    # ── Core ───────────────────────────────────────────────────────────────
    def _prune(self) -> None:
        """오래된 dedup key 제거 — 메모리 누수 방지."""
        cutoff = self._now_utc() - self._dedup_window
        stale = [k for k, ts in self._processed.items() if ts < cutoff]
        for k in stale:
            del self._processed[k]

    def poll_new(self) -> list[FireRecord]:
        """이전에 안 본 fire 들 반환. start_at 이전은 항상 무시.

        반환 순서: parsing 순 (대체로 발생 시각순). dedup 후 caller 가 sort
        하고 싶으면 직접.
        """
        if self._start_at is None:
            raise RuntimeError(
                "AirborneFireListener.poll_new() called before start_at()"
            )
        # since 는 start_at 또는 last poll 시점보다 lookback 만큼 이전
        # (boundary 안전 마진 — daemon log flush 지연 대비).
        since_ref = self._start_at - self._poll_lookback
        since_iso = since_ref.strftime("%Y-%m-%dT%H:%M:%SZ")

        # prune 을 *먼저* — dedup window 지난 key 는 부활시켜야 다시 emit 됨.
        self._prune()

        log_text = self._read_logs(since_iso)
        new_fires: list[FireRecord] = []
        now = self._now_utc()
        for line in log_text.splitlines():
            rec = _parse_fire_line(line)
            if rec is None:
                continue
            # start_at 이전 fire 무시
            if rec.ts < self._start_at:
                continue
            key = rec.key()
            if key in self._processed:
                continue
            self._processed[key] = now
            new_fires.append(rec)
        return new_fires
