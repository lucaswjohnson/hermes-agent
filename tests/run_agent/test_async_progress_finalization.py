"""A turn cannot present a final answer while delegated work is unsettled."""

from types import SimpleNamespace

from agent.conversation_loop import _guard_async_delegation_final_response


def test_final_sounding_response_becomes_explicit_progress_update(monkeypatch):
    monkeypatch.setattr(
        "tools.async_delegation.outstanding_for_session",
        lambda **_kwargs: [
            {
                "delegation_id": "deleg_1234",
                "status": "running",
                "goal": "verify the deployment",
                "delivery_state": "pending",
            }
        ],
    )
    agent = SimpleNamespace(session_id="parent-session")
    final_msg = {"role": "assistant", "content": "Fixed and deployed."}

    response, pending = _guard_async_delegation_final_response(
        agent, "Fixed and deployed.", final_msg
    )

    assert pending is True
    assert response.startswith("Progress update — work is continuing.")
    assert "Running: verify the deployment" in response
    assert "Remaining: wait for the worker result" in response
    assert "Waiting for another worker: yes" in response
    assert final_msg["content"] == response
    assert final_msg["finish_reason"] == "async_delegation_pending"


def test_accepted_completion_stays_progress_until_its_turn_starts(monkeypatch):
    monkeypatch.setattr(
        "tools.async_delegation.outstanding_for_session",
        lambda **_kwargs: [
            {
                "delegation_id": "deleg_1234",
                "status": "completed",
                "goal": "verify the deployment",
                "delivery_state": "accepted",
            }
        ],
    )
    agent = SimpleNamespace(session_id="parent-session")
    final_msg = {"role": "assistant", "content": "Fixed and deployed."}

    response, pending = _guard_async_delegation_final_response(
        agent, "Fixed and deployed.", final_msg
    )

    assert pending is True
    assert "Running: no worker process; a completed worker result is queued" in response
    assert "Remaining: receive and review the worker result" in response
    assert "Waiting for another worker: no; the result is queued" in response


def test_real_final_response_is_unchanged_after_completion_delivery(monkeypatch):
    monkeypatch.setattr(
        "tools.async_delegation.outstanding_for_session",
        lambda **_kwargs: [],
    )
    agent = SimpleNamespace(session_id="parent-session")
    final_msg = {"role": "assistant", "content": "Fixed and verified."}

    response, pending = _guard_async_delegation_final_response(
        agent, "Fixed and verified.", final_msg
    )

    assert pending is False
    assert response == "Fixed and verified."
    assert final_msg == {"role": "assistant", "content": "Fixed and verified."}
