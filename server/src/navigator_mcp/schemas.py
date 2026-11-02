"""Navigator tool schemas in the Ollama tools-entry shape (phase 4 section 2.2 holds the parameters).

Order is fixed: read_file, list_dir, grep, screenshot, web_search, need_info. The answer tool is appended per request.
"""

J_FILE = "what you expect to find and which question it settles"
J_WEB = "what you expect the results to say and which question it settles"


def _tool(name: str, description: str, parameters: dict) -> dict:
    return {"type": "function", "function": {"name": name, "description": description, "parameters": parameters}}


def _object(properties: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": properties, "required": required}


_STR = {"type": "string"}

NAVIGATOR_TOOLS: list[dict] = [
    _tool(
        "read_file",
        f"read_file: {J_FILE}",
        _object(
            {"path": _STR, "start": {"type": "integer"}, "end": {"type": "integer"},
             "justification": {"type": "string", "description": J_FILE}},
            ["path", "justification"],
        ),
    ),
    _tool(
        "list_dir",
        f"list_dir: {J_FILE}",
        _object({"path": _STR, "justification": {"type": "string", "description": J_FILE}}, ["path", "justification"]),
    ),
    _tool(
        "grep",
        f"grep: {J_FILE}",
        _object(
            {"pattern": _STR, "path": _STR, "justification": {"type": "string", "description": J_FILE}},
            ["pattern", "justification"],
        ),
    ),
    _tool(
        "screenshot",
        f"screenshot: {J_FILE}",
        _object(
            {"target": {"type": "string", "description": "a URL or the page the task serves"},
             "viewport": {"type": "string", "description": "default 1280x800"},
             "justification": {"type": "string", "description": J_FILE}},
            ["target", "justification"],
        ),
    ),
    _tool(
        "web_search",
        f"web_search: {J_WEB}",
        _object({"query": _STR, "justification": {"type": "string", "description": J_WEB}}, ["query", "justification"]),
    ),
    _tool(
        "need_info",
        "need_info: each question says what it settles",
        _object({"questions": {"type": "array", "items": _STR, "description": "each saying what it settles"}}, ["questions"]),
    ),
]


def _answer_tool(name: str, parameters: dict) -> dict:
    return _tool(name, name, parameters)  # answer tools take no justification, so the description is the name


SET_PLAN_TOOL = _answer_tool(
    "set_plan",
    _object(
        {
            "steps": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["id", "files", "change", "verification"],
                    "properties": {
                        "id": _STR,
                        "files": {"type": "array", "items": _STR},
                        "change": _STR,
                        "verification": _STR,
                    },
                },
            },
            "carry_forward": {"type": "array", "maxItems": 5, "items": _STR},
        },
        ["steps", "carry_forward"],
    ),
)

DECIDE_TOOL = _answer_tool(
    "decide",
    _object(
        {
            "decision": _STR,
            "findings": {
                "type": "array",
                "maxItems": 5,
                "items": {
                    "type": "object",
                    "required": ["severity", "cite", "issue", "fix"],
                    "properties": {
                        "severity": {"type": "string", "enum": ["S1", "S2", "S3"]},
                        "cite": {"type": "string", "description": "path:line"},
                        "issue": _STR,
                        "fix": _STR,
                    },
                },
            },
            "carry_forward": {"type": "array", "maxItems": 5, "items": _STR},
        },
        ["decision", "carry_forward"],
    ),
)
