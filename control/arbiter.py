from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from enum import StrEnum


class ControlAuthority(StrEnum):
    IDLE = "IDLE"
    MANUAL = "MANUAL"
    AGENT = "AGENT"
    SAFETY = "SAFETY"


class ControlLeaseError(RuntimeError):
    pass


@dataclass(frozen=True)
class ControlSnapshot:
    authority: ControlAuthority
    holder: str | None
    generation: int
    changed_at: float
    reason: str | None


class ControlArbiter:
    """Single-vehicle control lease with SAFETY > MANUAL > AGENT priority."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._authority = ControlAuthority.IDLE
        self._holder: str | None = None
        self._generation = 0
        self._changed_at = time.time()
        self._reason: str | None = None

    def snapshot(self) -> ControlSnapshot:
        with self._lock:
            return ControlSnapshot(
                authority=self._authority,
                holder=self._holder,
                generation=self._generation,
                changed_at=self._changed_at,
                reason=self._reason,
            )

    def acquire_agent(self, mission_id: str) -> ControlSnapshot:
        with self._lock:
            if self._authority in {ControlAuthority.MANUAL, ControlAuthority.SAFETY}:
                raise ControlLeaseError(f"control is owned by {self._authority}")
            if self._authority == ControlAuthority.AGENT and self._holder != mission_id:
                raise ControlLeaseError("another mission owns agent control")
            return self._set(ControlAuthority.AGENT, mission_id, "mission execution")

    def acquire_manual(self, operator_id: str = "local-operator") -> ControlSnapshot:
        with self._lock:
            if self._authority == ControlAuthority.SAFETY:
                raise ControlLeaseError("safety lock is active")
            return self._set(ControlAuthority.MANUAL, operator_id, "human takeover")

    def enter_safety(self, reason: str) -> ControlSnapshot:
        with self._lock:
            return self._set(ControlAuthority.SAFETY, "safety-system", reason)

    def release(self, holder: str | None = None) -> ControlSnapshot:
        with self._lock:
            if holder is not None and self._holder not in {None, holder}:
                raise ControlLeaseError("lease holder does not match")
            return self._set(ControlAuthority.IDLE, None, "lease released")

    def require(self, authority: ControlAuthority, holder: str | None = None) -> None:
        with self._lock:
            if self._authority != authority:
                raise ControlLeaseError(f"{authority} control is required")
            if holder is not None and self._holder != holder:
                raise ControlLeaseError("lease holder does not match")

    def _set(self, authority: ControlAuthority, holder: str | None, reason: str) -> ControlSnapshot:
        if authority != self._authority or holder != self._holder:
            self._generation += 1
        self._authority = authority
        self._holder = holder
        self._changed_at = time.time()
        self._reason = reason
        return self.snapshot()
