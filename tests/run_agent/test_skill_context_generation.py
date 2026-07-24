"""Context-generation contract for skill deduplication."""

import json
from unittest.mock import patch

from model_tools import handle_function_call
from run_agent import AIAgent
from tools.skills_tool import _LOADED_SKILL_REVISIONS


def test_compression_advances_skill_context_generation():
    """A compaction boundary must permit skill reinjection afterwards."""
    agent = object.__new__(AIAgent)
    agent._skill_context_generation = 4
    compressed_messages = [{"role": "user", "content": "compressed"}]

    with patch(
        "agent.conversation_compression.compress_context",
        return_value=(compressed_messages, "system"),
    ) as compress:
        result = agent._compress_context(
            [{"role": "user", "content": "before"}],
            "system",
            approx_tokens=100,
            task_id="task",
        )

    assert result == (compressed_messages, "system")
    assert agent._skill_context_generation == 5
    compress.assert_called_once()


def test_skill_view_reinjects_after_compression_via_agent_dispatch(tmp_path):
    """Exercise registry dispatch, dedupe, and a real agent compression boundary."""
    skill_dir = tmp_path / "dispatch-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: dispatch-skill\ndescription: dispatch test\n---\n\nDispatch body.\n"
    )
    agent = object.__new__(AIAgent)
    agent.session_id = "dispatch-session"
    agent._skill_context_generation = 0

    def call_skill():
        return json.loads(
            handle_function_call(
                "skill_view",
                {"name": "dispatch-skill"},
                context_id=f"{agent.session_id}:{agent._skill_context_generation}",
            )
        )

    _LOADED_SKILL_REVISIONS.clear()
    try:
        with patch("tools.skills_tool.SKILLS_DIR", tmp_path):
            first = call_skill()
            unchanged_repeat = call_skill()
            with patch(
                "agent.conversation_compression.compress_context",
                return_value=([{"role": "user", "content": "compressed"}], "system"),
            ):
                agent._compress_context(
                    [{"role": "user", "content": "before"}],
                    "system",
                    approx_tokens=100,
                    task_id="dispatch-task",
                )
            after_compression = call_skill()
    finally:
        _LOADED_SKILL_REVISIONS.clear()

    assert first["content"] == "Dispatch body.\n"
    assert unchanged_repeat["already_loaded"] is True
    assert "content" not in unchanged_repeat
    assert agent._skill_context_generation == 1
    assert after_compression["content"] == "Dispatch body.\n"
    assert not after_compression.get("already_loaded", False)
