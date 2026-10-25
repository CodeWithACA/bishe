import pytest

from control.arbiter import ControlArbiter, ControlAuthority, ControlLeaseError


def test_manual_preempts_agent_and_blocks_agent_reacquire():
    arbiter = ControlArbiter()
    arbiter.acquire_agent("mission-1")
    snapshot = arbiter.acquire_manual("operator")
    assert snapshot.authority == ControlAuthority.MANUAL
    assert snapshot.holder == "operator"
    with pytest.raises(ControlLeaseError):
        arbiter.acquire_agent("mission-1")


def test_safety_blocks_manual_control():
    arbiter = ControlArbiter()
    arbiter.enter_safety("telemetry lost")
    with pytest.raises(ControlLeaseError):
        arbiter.acquire_manual("operator")
