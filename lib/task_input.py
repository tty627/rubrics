"""Normalize original task messages without exposing candidate answers."""
import json
import re
from collections import defaultdict
from pathlib import Path

_ALLOWED_TASK_ROLES = {"system", "developer", "user"}


def normalize_text(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def task_messages(messages):
    """Keep only instructions and user turns; assistant turns are candidate answers."""
    out = []
    for message in messages or []:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role", "")).lower()
        content = message.get("content")
        if role not in _ALLOWED_TASK_ROLES or not isinstance(content, str) or not content.strip():
            continue
        out.append({"role": role, "content": content})
    return out


def user_task_text(messages):
    """Return the first user turn, matching the repository conversion contract."""
    users = [m["content"] for m in task_messages(messages) if m["role"] == "user"]
    return users[0] if users else ""


def load_source_index(path):
    """Index source sessions by normalized final user turn; ambiguous matches stay explicit."""
    index = defaultdict(list)
    if not path:
        return index
    with Path(path).open(encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            record = json.loads(line)
            messages = task_messages(record.get("messages"))
            key = normalize_text(user_task_text(messages))
            if key:
                index[key].append({
                    "task_messages": messages,
                    "source_session_id": str(record.get("session_id", "")),
                })
    return index


def attach_source_messages(record, source_index):
    key = normalize_text(record.get("question"))
    matches = source_index.get(key, [])
    if not matches and len(str(record.get("question", ""))) >= 7900:
        # convert_ol_logs.py truncates the first user turn to 8000 characters.
        prefix_matches = [items for source_key, items in source_index.items()
                          if source_key.startswith(key)]
        if len(prefix_matches) == 1:
            matches = prefix_matches[0]
    out = dict(record)
    if len(matches) == 1:
        out.update(matches[0])
        out["task_message_status"] = "matched"
    elif len(matches) > 1:
        out["task_messages"] = []
        out["source_session_id"] = ""
        out["task_message_status"] = "ambiguous"
    else:
        out["task_messages"] = [{"role": "user", "content": str(record.get("question", ""))}]
        out["source_session_id"] = ""
        out["task_message_status"] = "question_only"
    out["system_messages"] = [m["content"] for m in out["task_messages"]
                              if m["role"] in ("system", "developer")]
    out["user_messages"] = [m["content"] for m in out["task_messages"] if m["role"] == "user"]
    return out


def prompt_context(record, question=None, max_chars=12000):
    """Render explicit source instructions only; never include assistant messages."""
    messages = task_messages(record.get("task_messages"))
    blocks = []
    for message in messages:
        if message["role"] in ("system", "developer"):
            blocks.append(f'【原始{message["role"]}约束】\n{message["content"]}')
    users = [m["content"] for m in messages if m["role"] == "user"]
    effective = question or record.get("query_eff") or (users[0] if users else record.get("question", ""))
    if users:
        for i, content in enumerate(users[1:], 1):
            blocks.append(f"【后续用户提供的任务资料 {i}】\n{content}")
    blocks.append(f"【当前题目】\n{effective}")
    return "\n\n".join(blocks)[:max_chars]
