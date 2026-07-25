import logging
import threading
import time
from datetime import datetime, timezone
from typing import Callable, Optional

from fronius.adapter import normalize_live_data
from solar_data.storage import SolarDatabase


class Collector:
    def __init__(self, client, database: SolarDatabase, interval: float = 10,
                 retention_days: float = 7, logger: Optional[logging.Logger] = None,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> None:
        if interval <= 0:
            raise ValueError("interval must be positive")
        if retention_days < 0:
            raise ValueError("retention_days must not be negative")
        self.client = client
        self.database = database
        self.interval = interval
        self.retention_days = retention_days
        self.logger = logger or logging.getLogger(__name__)
        self.clock = clock

    def collect_once(self) -> bool:
        try:
            snapshot = normalize_live_data(**self.client.get_live_payloads())
            inserted = self.database.store_snapshot(snapshot)
            self.database.aggregate_completed(self.clock())
            self.database.delete_expired_raw(self.retention_days, self.clock())
            if inserted:
                self.logger.info("stored snapshot at %s", snapshot.timestamp)
            else:
                self.logger.info("snapshot at %s already exists", snapshot.timestamp)
            return inserted
        except Exception:
            # A bad API cycle must never be converted into data or stop the loop.
            self.logger.exception("collector cycle failed; no snapshot was stored")
            return False

    def run(self, stop_event: threading.Event) -> None:
        deadline = time.monotonic()
        while not stop_event.is_set():
            self.collect_once()
            deadline += self.interval
            delay = deadline - time.monotonic()
            if delay <= 0:
                missed = int(-delay // self.interval) + 1
                deadline += missed * self.interval
                delay = max(0, deadline - time.monotonic())
            stop_event.wait(delay)
