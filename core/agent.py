"""The Pass — the resident agent (Prompt 03).

NOTE: PassAgent owns the clock. It holds the module registry, ticks every
module that has a tick(), and announces service open/close exactly once per day.

WHY a registry instead of hard imports: modules are optional. The agent boots
with whatever is present and degrades gracefully, which is what lets this
program be built one prompt at a time.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from core.config import Config
from core.database import Database
from core.events import SERVICE_CLOSE, SERVICE_OPEN, EventBus
from core.types import ServiceState


class PassAgent:
    def __init__(self, config: Optional[Config] = None,
                 db: Optional[Database] = None,
                 bus: Optional[EventBus] = None) -> None:
        self.config = config or Config.default()
        self.db = db or Database(self.config.db_path).migrate()
        self.bus = bus or EventBus()
        self.modules: Dict[str, Any] = {}
        self._announced = set()

    # -- module registry ---------------------------------------------------
    def register(self, name: str, module: Any) -> Any:
        self.modules[name] = module
        return module

    def get(self, name: str) -> Any:
        return self.modules.get(name)

    # -- clock -------------------------------------------------------------
    def state(self, when: Optional[datetime] = None) -> ServiceState:
        when = when or datetime.now()
        hhmm = when.strftime("%H:%M")
        if hhmm < self.config.service_start:
            return ServiceState.PRE_SERVICE
        if hhmm >= self.config.service_end:
            return ServiceState.POST_SERVICE
        return ServiceState.SERVICE

    def tick(self, when: Optional[datetime] = None) -> Dict[str, Any]:
        """One beat: tick the modules, then announce service transitions.

        Idempotent for a given moment — calling twice does not double-announce.
        """
        when = when or datetime.now()
        results: Dict[str, Any] = {}
        for name, module in self.modules.items():
            ticker = getattr(module, "tick", None)
            if not callable(ticker):
                continue
            try:
                results[name] = ticker(when)
            except Exception as exc:  # one bad module must not stop the beat
                results[name] = {"error": repr(exc)}

        day = when.date().isoformat()
        state = self.state(when)
        if state is ServiceState.SERVICE and (day, SERVICE_OPEN) not in self._announced:
            self._announced.add((day, SERVICE_OPEN))
            self.bus.publish(SERVICE_OPEN, {"date": day, "state": state.value})
        if state is ServiceState.POST_SERVICE and (day, SERVICE_CLOSE) not in self._announced:
            self._announced.add((day, SERVICE_CLOSE))
            self.bus.publish(SERVICE_CLOSE, {"date": day, "state": state.value})
        return results
