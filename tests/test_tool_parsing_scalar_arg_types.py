"""Qwen/Hermes text-mode tool calls whose advertised string argument is a scalar.

Issue #6012: `_parse_json_tool_call_body()` only checks that "arguments" is an
object, so `{"name": "bash", "arguments": {"command": 42}}` produced a ToolBlock
whose content was an int. Nothing rejected it -- the empty-required-args guard
runs `str(...)` on the value, so a number reads as present and non-empty. The
mismatch surfaced much later as `block.content.strip()` in the agent loop
(AttributeError, aborting the turn), and for the concatenating tools as a
TypeError inside the converter itself.

Distinct from #5043/#5076, which cover a non-object top-level "arguments"; here
the object is valid and only a field inside it has the wrong type.
"""
import src.agent_tools  # noqa: F401  (break agent_tools<->tool_parsing import cycle)
from src.tool_parsing import parse_tool_blocks
from src.tool_schemas import function_call_to_tool_block

# Verbatim payload from issue #6012.
ISSUE_PAYLOAD = '<tool_call>{"name":"bash","arguments":{"command":42}}</tool_call>'


def test_issue_6012_numeric_bash_command_produces_no_block():
    assert parse_tool_blocks(ISSUE_PAYLOAD) == []


def test_numeric_python_code_produces_no_block():
    text = '<tool_call>{"name":"python","arguments":{"code":42}}</tool_call>'
    assert parse_tool_blocks(text) == []


def test_rejected_scalar_never_reaches_a_block_content():
    # The turn-aborting failure was `block.content.strip()`, so the property that
    # matters is not just "no block" but "no block carrying a non-string".
    for payload in (
        '<tool_call>{"name":"bash","arguments":{"command":42}}</tool_call>',
        '<tool_call>{"name":"bash","arguments":{"command":null}}</tool_call>',
        '<tool_call>{"name":"bash","arguments":{"command":true}}</tool_call>',
        '<tool_call>{"name":"bash","arguments":{"command":["ls"]}}</tool_call>',
        '<tool_call>{"name":"bash","arguments":{"command":{"cmd":"ls"}}}</tool_call>',
    ):
        for block in parse_tool_blocks(payload):
            assert isinstance(block.content, str), payload


def test_converter_rejects_non_string_command_directly():
    assert function_call_to_tool_block("bash", {"command": 42}) is None
    assert function_call_to_tool_block("python", {"code": 3.14}) is None


def test_concatenating_tools_reject_instead_of_raising():
    # write_file built content as `path + "\n" + content`, so a non-string field
    # raised TypeError inside the converter rather than returning None.
    assert function_call_to_tool_block("write_file", {"path": 1, "content": "x"}) is None
    assert function_call_to_tool_block("write_file", {"path": "a.txt", "content": 2}) is None


def test_valid_string_arguments_still_parse():
    blocks = parse_tool_blocks(
        '<tool_call>{"name":"bash","arguments":{"command":"echo ok"}}</tool_call>'
    )
    assert [(b.tool_type, b.content) for b in blocks] == [("bash", "echo ok")]

    block = function_call_to_tool_block("python", {"code": "print(1)"})
    assert block is not None and block.content == "print(1)"


def test_absent_optional_string_keeps_its_default():
    # The guard must only reject a value the model actually sent; an omitted
    # key still falls through to the converter's own default.
    block = function_call_to_tool_block("write_file", {"path": "a.txt"})
    assert block is not None
    assert block.content == "a.txt\n"


def test_non_string_types_are_unaffected():
    # web_search advertises "queries" as an array, so a list must still work.
    block = function_call_to_tool_block("web_search", {"queries": ["odysseus"]})
    assert block is not None
    assert block.content == "odysseus"
