"""A closing marker inside a JSON string value is argument data, not a delimiter.

Issue #6013: the <tool_call> wrapper scan paired an opener with the first
*textual* `</tool_call>`, with no JSON-string awareness. A perfectly valid call
whose argument happens to contain that sequence -- documentation, a tool-format
example, anything written with write_file -- was truncated mid-string, so the
call was dropped, and `strip_tool_blocks` consumed a different span than
`parse_tool_blocks`, leaving trailing wrapper syntax in the persisted text.

The wrapper body is bare JSON (Qwen/Hermes text mode, #5187), so the closer is
now searched from the end of the decoded JSON value, and parsing and stripping
share the same spans.
"""
import src.agent_tools  # noqa: F401  (break agent_tools<->tool_parsing import cycle)
from src.tool_parsing import parse_tool_blocks, strip_tool_blocks

# Verbatim payload from issue #6013.
ISSUE_PAYLOAD = (
    '<tool_call>{"name":"write_file","arguments":'
    '{"path":"notes.txt","content":"alpha </tool_call> omega"}}</tool_call>'
)


def test_issue_6013_closing_marker_inside_a_string_is_data():
    blocks = parse_tool_blocks(ISSUE_PAYLOAD)
    assert len(blocks) == 1
    assert blocks[0].tool_type == "write_file"
    # write_file content is "<path>\n<content>".
    assert blocks[0].content == "notes.txt\nalpha </tool_call> omega"


def test_stripping_consumes_the_same_span_as_parsing():
    assert strip_tool_blocks(ISSUE_PAYLOAD).strip() == ""


def test_second_wrapper_after_one_containing_a_marker_still_parses():
    text = ISSUE_PAYLOAD + '\n<tool_call>{"name":"bash","arguments":{"command":"ls"}}</tool_call>'
    blocks = parse_tool_blocks(text)
    assert [(b.tool_type, b.content) for b in blocks] == [
        ("write_file", "notes.txt\nalpha </tool_call> omega"),
        ("bash", "ls"),
    ]
    assert strip_tool_blocks(text).strip() == ""


def test_escaped_quote_before_the_marker_does_not_end_the_string():
    text = (
        '<tool_call>{"name":"bash","arguments":'
        '{"command":"echo \\"quoted </tool_call> still data\\""}}</tool_call>'
    )
    blocks = parse_tool_blocks(text)
    assert len(blocks) == 1
    assert blocks[0].content == 'echo "quoted </tool_call> still data"'


def test_marker_in_a_nested_object_value():
    text = (
        '<tool_call>{"name":"edit_file","arguments":'
        '{"path":"a.py","edits":[{"find":"x","replace":"</tool_call>"}]}}</tool_call>'
    )
    blocks = parse_tool_blocks(text)
    assert len(blocks) == 1
    assert "</tool_call>" in blocks[0].content


def test_prose_around_the_wrapper_survives_stripping():
    text = "before\n" + ISSUE_PAYLOAD + "\nafter"
    cleaned = strip_tool_blocks(text)
    assert "before" in cleaned and "after" in cleaned
    assert "tool_call" not in cleaned
    assert "omega" not in cleaned


# ---- behaviour that must not change -------------------------------------

def test_plain_wrapper_without_any_marker_is_unaffected():
    text = '<tool_call>{"name": "bash", "arguments": {"command": "mkdir -p agent-test"}}</tool_call>'
    blocks = parse_tool_blocks(text)
    assert [(b.tool_type, b.content) for b in blocks] == [("bash", "mkdir -p agent-test")]
    assert strip_tool_blocks(text).strip() == ""


def test_xml_wrapper_body_still_uses_the_textual_closer():
    text = '<tool_call><invoke name="bash"><parameter name="command">echo ok</parameter></invoke></tool_call>'
    blocks = parse_tool_blocks(text)
    assert [(b.tool_type, b.content) for b in blocks] == [("bash", "echo ok")]


def test_unclosed_json_wrapper_still_parses():
    # Covered by tests/test_tool_parsing_hermes_json.py; re-asserted here because
    # the JSON-aware scan changes where the closer search starts.
    text = '<tool_call>\n{"name": "bash", "arguments": {"command": "ls -la"}}'
    blocks = parse_tool_blocks(text)
    assert [(b.tool_type, b.content) for b in blocks] == [("bash", "ls -la")]


def test_opener_flood_without_a_closer_terminates():
    # The forward-only O(n) property of the scan must survive the change:
    # many openers and no closer must not trigger a quadratic rescan.
    text = '<tool_call>{"name":"bash","arguments":{"command":"ls"}}' * 2000
    assert parse_tool_blocks(text) is not None
    strip_tool_blocks(text)
