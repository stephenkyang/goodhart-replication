"""Messages are written in the shape and key order of Goodhart Labs' published transcripts."""
from __future__ import annotations

import importlib.util
from pathlib import Path

from inspect_ai.model import ChatMessageAssistant, ChatMessageTool, ChatMessageUser, ContentReasoning, ContentText
from inspect_ai.tool import ToolCall

HERE = Path(__file__).resolve().parent.parent / "goodhart-replication"
spec = importlib.util.spec_from_file_location("export_transcripts", HERE / "export_transcripts.py")
export = importlib.util.module_from_spec(spec)
spec.loader.exec_module(export)


def test_assistant_message_keeps_goodharts_fields_and_order():
    m = ChatMessageAssistant(
        content=[ContentReasoning(reasoning="", summary="think first", signature="sig"), ContentText(text="then act")],
        tool_calls=[ToolCall(id="call_1", function="capped_bash", arguments={"cmd": "ls"})],
        model="gpt-6-astra", source="generate", metadata={"redacted_reasoning_tokens": 3})
    out = export.message(m)
    assert list(out) == ["id", "role", "model", "source", "content", "metadata", "tool_calls"]
    assert out["content"] == [{"text": "think first", "type": "reasoning", "encrypted": False}, {"text": "then act", "type": "text"}]
    assert out["tool_calls"] == [{"id": "call_1", "type": "function", "function": "capped_bash", "arguments": {"cmd": "ls"}}]
    assert "signature" not in str(out) and out["metadata"] == {"redacted_reasoning_tokens": 3}


def test_opaque_reasoning_is_null_and_marked_encrypted():
    m = ChatMessageAssistant(content=[ContentReasoning(reasoning="CAQS5QQK", redacted=True), ContentText(text="", refusal=True)],
                             model="claude-fable-5-1", source="generate")
    out = export.message(m)
    assert out["content"][0] == {"text": None, "type": "reasoning", "encrypted": True}
    assert out["content"][1] == {"text": "", "type": "text", "refusal": True}
    assert "tool_calls" not in out and "metadata" not in out


def test_tool_and_user_messages_match_their_layout():
    tool = export.message(ChatMessageTool(content="total 12\n", tool_call_id="call_1", function="capped_bash"))
    assert list(tool) == ["id", "role", "content", "function", "tool_call_id"] and tool["content"] == "total 12\n"
    prompt = export.message(ChatMessageUser(content="## TASK", source="input"))
    nudge = export.message(ChatMessageUser(content="38 turn(s) and 179 minute(s) remaining."))
    assert list(prompt) == ["id", "role", "source", "content"] and list(nudge) == ["id", "role", "content"]
