import html
import importlib.util
import json
import logging
import sys
import types
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2] / "backend"

# open_webui.env pulls in heavy runtime dependencies (typer, aiohttp, ...) that are
# irrelevant to the pure functions under test; stub the package and provide a sane
# SRC_LOG_LEVELS so misc.py can be imported standalone.
open_webui_pkg = types.ModuleType("open_webui")
open_webui_pkg.__path__ = [str(BACKEND_DIR / "open_webui")]
sys.modules["open_webui"] = open_webui_pkg

env_stub = types.ModuleType("open_webui.env")
env_stub.SRC_LOG_LEVELS = {
    "GLOBAL": logging.INFO,
    "MAIN": logging.INFO,
    "AUDIO": logging.INFO,
    "COMFYUI": logging.INFO,
    "CONFIG": logging.INFO,
    "DB": logging.INFO,
    "IMAGES": logging.INFO,
    "RAG": logging.INFO,
    "WEBRTC": logging.INFO,
}
sys.modules["open_webui.env"] = env_stub

utils_pkg = types.ModuleType("open_webui.utils")
utils_pkg.__path__ = [str(BACKEND_DIR / "open_webui" / "utils")]
sys.modules["open_webui.utils"] = utils_pkg

_spec = importlib.util.spec_from_file_location(
    "open_webui.utils.misc", BACKEND_DIR / "open_webui" / "utils" / "misc.py"
)
misc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(misc)


def _attr(value) -> str:
    # serialize_content_blocks() stores attribute values as
    # html.escape(json.dumps(value)).
    return html.escape(json.dumps(value))


def test_inline_tool_calls_arguments_are_decoded():
    # OpenAI-native origin: arguments arrive as a JSON string and are stored
    # double-encoded (json.dumps of the JSON string).
    stored = (
        f'<tool_calls id="call_1" name="search" arguments="{_attr(json.dumps({"q": "wikiteq"}))}" '
        f'result="{_attr(["hit"])}"/>'
    )
    messages = [{"role": "assistant", "content": stored}]

    expanded = misc.expand_ui_tags_in_messages(messages)

    assistant = next(m for m in expanded if m.get("tool_calls"))
    assert assistant["tool_calls"][0]["function"]["name"] == "search"
    assert assistant["tool_calls"][0]["id"] == "call_1"
    assert assistant["tool_calls"][0]["type"] == "function"
    # arguments must round-trip back to the original JSON string the model
    # produced, not the double-encoded form stored in the attribute
    assert assistant["tool_calls"][0]["function"]["arguments"] == '{"q": "wikiteq"}'

    # tool message content stays a string per the OpenAI schema
    tool = next(m for m in expanded if m.get("role") == "tool")
    assert tool["tool_call_id"] == "call_1"
    assert tool["content"] == '["hit"]'


def test_inline_tool_calls_object_origin_arguments_are_decoded():
    # Object-origin arguments (e.g. Ollama native tool calls) are stored
    # single-encoded and must be re-encoded as a JSON string for the
    # tool_calls[].function.arguments field.
    stored = (
        f'<tool_calls id="call_2" name="lookup" arguments="{_attr({"city": "Toronto"})}" '
        f'result="{_attr("sunny")}"/>'
    )
    messages = [{"role": "assistant", "content": stored}]

    expanded = misc.expand_ui_tags_in_messages(messages)

    assistant = next(m for m in expanded if m.get("tool_calls"))
    assert json.loads(assistant["tool_calls"][0]["function"]["arguments"]) == {
        "city": "Toronto"
    }
    assert next(m for m in expanded if m.get("role") == "tool")["content"] == "sunny"


def test_details_tool_calls_arguments_are_decoded():
    stored = (
        '<details type="tool_calls" done="true" id="call_9" name="lookup" '
        f'arguments="{_attr({"query": "wikiteq", "limit": 5})}" '
        f'result="{_attr({"ok": True})}">'
        "\n<summary>Tool Executed</summary>\n</details>"
    )
    messages = [{"role": "assistant", "content": f"Let me look.\n{stored}\nDone."}]

    expanded = misc.expand_ui_tags_in_messages(messages)

    assistant = next(m for m in expanded if m.get("tool_calls"))
    assert json.loads(assistant["tool_calls"][0]["function"]["arguments"]) == {
        "query": "wikiteq",
        "limit": 5,
    }
    tool = next(m for m in expanded if m.get("role") == "tool")
    assert json.loads(tool["content"]) == {"ok": True}


def test_model_emitted_tag_without_result_is_stripped():
    messages = [
        {
            "role": "assistant",
            "content": '<tool_calls id="c2" name="ghost" arguments="{}"/>',
        }
    ]

    expanded = misc.expand_ui_tags_in_messages(messages)

    assert expanded == []


def test_non_tool_content_passes_through():
    messages = [
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "content": '<details type="reasoning" done="true">'
            "<summary>Thought</summary>hm</details>Answer",
        },
    ]

    expanded = misc.expand_ui_tags_in_messages(messages)

    assert expanded[0] == {"role": "user", "content": "hi"}
    assert expanded[1] == {"role": "assistant", "content": "Answer"}
