#!/usr/bin/env python3
"""
FoldNotes + Ollama Chat

An interactive chat client that connects a local Ollama model to your
FoldNotes knowledge base via the `fn` CLI. The model can read, create,
search, and manage your notes through tool calls.

Requires:
    - Python 3.9+
    - Ollama running locally (https://ollama.com)
    - `fn` CLI installed (via FoldNotes app or manual symlink)
    - requests library: pip install requests

Usage:
    python3 foldnotes_chat.py                    # defaults to qwen2.5:7b
    python3 foldnotes_chat.py --model llama3.1:8b
    python3 foldnotes_chat.py --url http://192.168.1.100:11434  # remote Ollama

Commands:
    /quit or /exit  — exit the chat
    /clear          — clear conversation history
    /model <name>   — switch model mid-conversation
    /tools          — list available tools
"""

__version__ = "2.1.0"

import argparse
import json
import os
import subprocess
import sys
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_MODEL = "qwen2.5:7b"
DEFAULT_OLLAMA_URL = "http://localhost:11434"

SYSTEM_PROMPT = """You are a helpful assistant with access to the user's FoldNotes knowledge base. You can read, search, create, and modify their notes using the available tools.

Guidelines:
- When the user asks about their notes, search or list them first before answering.
- When showing note content, format it clearly.
- For task queries, use the list_tasks tool with appropriate filters.
- Be concise but thorough. Quote relevant sections from notes when answering questions.
- If a tool returns an error, explain it to the user and suggest alternatives.
- You can chain multiple tool calls — e.g. search for a topic, then show the full note.
- When creating or editing notes, confirm with the user before making changes unless they explicitly asked you to."""

# ---------------------------------------------------------------------------
# fn CLI wrapper
# ---------------------------------------------------------------------------

FN_PATHS = [
    "/usr/local/bin/fn",
    "/opt/homebrew/bin/fn",
]


def _find_fn() -> str:
    for path in FN_PATHS:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return "fn"


FN_BIN = _find_fn()

# Seconds before an `fn` invocation is abandoned. 60, not 30: the FIRST call
# in a session is far slower than the rest — the cold run resolves the iCloud
# container, opens the SwiftData cache and may create that day's daily note.
# Measured 2026-08-09: first call >30 s (timed out), second 12.6 s, then
# 0.06 s warm. 30 s failed only ever on that first call, and the retry always
# worked — which is a confusing way for a tool to behave.
FN_TIMEOUT = 60



def run_fn(args: list, collection: Optional[str] = None) -> dict:
    """Run an fn CLI command and return parsed JSON or error dict."""
    cmd = [FN_BIN, "--quiet"]
    if collection:
        cmd += ["--collection", collection]
    cmd += args + ["--json"]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=FN_TIMEOUT)
    except FileNotFoundError:
        return {"error": f"fn CLI not found at {FN_BIN}. Install via FoldNotes > Install Command Line Tool."}
    except subprocess.TimeoutExpired:
        return {"error": f"Command timed out after {FN_TIMEOUT} seconds."}

    output = result.stdout.strip()
    if result.returncode != 0:
        return {"error": result.stderr.strip() or output or "Unknown error"}

    try:
        return json.loads(output)
    except json.JSONDecodeError:
        return {"text": output}


def _task_selector(args: dict) -> Optional[list]:
    """CLI args identifying one task: prefer the exact UUID, else substring."""
    if args.get("task_id"):
        return ["--id", args["task_id"]]
    if args.get("text"):
        return [args["text"]]
    return None


# ---------------------------------------------------------------------------
# Tool definitions (Ollama format)
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_notes",
            "description": "List notes in the FoldNotes collection with filtering and sorting.",
            "parameters": {
                "type": "object",
                "properties": {
                    "tag": {"type": "string", "description": "Filter by tag name (without #)."},
                    "sort": {"type": "string", "enum": ["modified", "created", "title"], "description": "Sort order."},
                    "limit": {"type": "integer", "description": "Max notes to return."},
                    "favourites": {"type": "boolean", "description": "Only show favourited notes."},
                    "has_tasks": {"type": "boolean", "description": "Only show notes with active tasks."},
                    "has_overdue": {"type": "boolean", "description": "Only show notes with overdue tasks."},
                    "archived": {"type": "boolean", "description": "Show only archived notes."},
                    "modified_after": {"type": "string", "description": "Only notes modified after YYYY-MM-DD."},
                    "modified_before": {"type": "string", "description": "Only notes modified before YYYY-MM-DD."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "show_note",
            "description": "Read a note's full content and metadata. Resolves by title, UUID, or filename.",
            "parameters": {
                "type": "object",
                "properties": {
                    "note": {"type": "string", "description": "Note title, UUID, or filename."},
                    "body": {"type": "boolean", "description": "Include full body (default true)."},
                    "properties": {"type": "boolean", "description": "Show only front matter."},
                    "tasks": {"type": "boolean", "description": "Show tasks in the note."},
                    "backlinks": {"type": "boolean", "description": "Show notes linking to this one."},
                },
                "required": ["note"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_note",
            "description": "Create a new note with optional content and tags.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Note title (becomes the filename)."},
                    "content": {"type": "string", "description": "Note body content (markdown)."},
                    "tags": {"type": "array", "items": {"type": "string"}, "description": "Tags to add (without #)."},
                },
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_note",
            "description": "Modify a note: append text, prepend after heading, replace content, set properties, favourite, archive.",
            "parameters": {
                "type": "object",
                "properties": {
                    "note": {"type": "string", "description": "Note title, UUID, or filename."},
                    "append": {"type": "string", "description": "Text to append to the note."},
                    "prepend_after_heading": {"type": "string", "description": "Text to insert after first heading."},
                    "content": {"type": "string", "description": "Replace entire body with this content."},
                    "set_property": {"type": "array", "items": {"type": "string"}, "description": "Properties as key=value."},
                    "favourite": {"type": "boolean", "description": "Mark as favourite."},
                    "unfavourite": {"type": "boolean", "description": "Remove favourite."},
                    "archive": {"type": "boolean", "description": "Archive the note."},
                    "unarchive": {"type": "boolean", "description": "Unarchive the note."},
                },
                "required": ["note"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_notes",
            "description": "Full-text search across all notes. Returns matching lines with context.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query string."},
                    "context": {"type": "integer", "description": "Lines of context around matches (default: 2)."},
                    "tag": {"type": "string", "description": "Filter to notes with this tag."},
                    "limit": {"type": "integer", "description": "Maximum number of results."},
                    "titles_only": {"type": "boolean", "description": "Search titles only."},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_tasks",
            "description": "List tasks across all notes. Filter by status, due date, priority, project.",
            "parameters": {
                "type": "object",
                "properties": {
                    "due_today": {"type": "boolean", "description": "Show only tasks due today."},
                    "due_this_week": {"type": "boolean", "description": "Show tasks due this week."},
                    "overdue": {"type": "boolean", "description": "Show only overdue tasks."},
                    "status": {"type": "string", "enum": ["not-started", "in-progress", "done", "cancelled"], "description": "Filter by status."},
                    "priority": {"type": "string", "enum": ["high", "medium", "low"], "description": "Filter by priority."},
                    "note": {"type": "string", "description": "Filter by note title."},
                    "project": {"type": "string", "description": "Filter by project."},
                    "all": {"type": "boolean", "description": "Include done and cancelled tasks."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_task",
            "description": "Add a new task to a note.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Task description."},
                    "note": {"type": "string", "description": "Target note title."},
                    "due": {"type": "string", "description": "Due date (YYYY-MM-DD or natural language)."},
                    "priority": {"type": "string", "enum": ["high", "medium", "low"], "description": "Priority level."},
                    "project": {"type": "string", "description": "Project name."},
                },
                "required": ["text", "note"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "complete_task",
            "description": "Mark a task as done. Identify by task_id (exact, preferred) or text (substring).",
            "parameters": {
                "type": "object",
                "properties": {
                    "note": {"type": "string", "description": "Note containing the task."},
                    "text": {"type": "string", "description": "Task text (case-insensitive substring). Must match exactly one task, else the command is refused — use task_id to disambiguate."},
                    "task_id": {"type": "string", "description": "Exact task UUID from list_tasks. Preferred — targets the precise task even when several share the same wording."},
                },
                "required": ["note"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_task",
            "description": "Cancel a task. Identify by task_id (exact, preferred) or text (substring).",
            "parameters": {
                "type": "object",
                "properties": {
                    "note": {"type": "string", "description": "Note containing the task."},
                    "text": {"type": "string", "description": "Task text (case-insensitive substring). Must match exactly one task, else the command is refused — use task_id to disambiguate."},
                    "task_id": {"type": "string", "description": "Exact task UUID from list_tasks. Preferred — targets the precise task even when several share the same wording."},
                },
                "required": ["note"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "start_task",
            "description": "Mark a task as in-progress. Identify by task_id (exact, preferred) or text (substring).",
            "parameters": {
                "type": "object",
                "properties": {
                    "note": {"type": "string", "description": "Note containing the task."},
                    "text": {"type": "string", "description": "Task text (case-insensitive substring). Must match exactly one task, else the command is refused — use task_id to disambiguate."},
                    "task_id": {"type": "string", "description": "Exact task UUID from list_tasks. Preferred — targets the precise task even when several share the same wording."},
                },
                "required": ["note"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reset_task",
            "description": "Reset a task to not-started. Identify by task_id (exact, preferred) or text (substring).",
            "parameters": {
                "type": "object",
                "properties": {
                    "note": {"type": "string", "description": "Note containing the task."},
                    "text": {"type": "string", "description": "Task text (case-insensitive substring). Must match exactly one task, else the command is refused — use task_id to disambiguate."},
                    "task_id": {"type": "string", "description": "Exact task UUID from list_tasks. Preferred — targets the precise task even when several share the same wording."},
                },
                "required": ["note"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_task",
            "description": "Amend a task's due date, priority, or project. Identify by task_id (exact, preferred) or text (substring); pass at least one field to change. Preserves the task's UUID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "note": {"type": "string", "description": "Note containing the task."},
                    "text": {"type": "string", "description": "Task text (case-insensitive substring). Must match exactly one task, else the command is refused — use task_id to disambiguate."},
                    "task_id": {"type": "string", "description": "Exact task UUID from list_tasks. Preferred."},
                    "due": {"type": "string", "description": "New due date (YYYY-MM-DD or natural language)."},
                    "clear_due": {"type": "boolean", "description": "Remove the due date."},
                    "priority": {"type": "string", "enum": ["high", "medium", "low"], "description": "New priority."},
                    "clear_priority": {"type": "boolean", "description": "Remove the priority."},
                    "project": {"type": "string", "description": "New project name."},
                    "clear_project": {"type": "boolean", "description": "Remove the project."},
                },
                "required": ["note"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remove_task",
            "description": "Permanently delete a task line from a note (does NOT go to trash). Identify by task_id (exact, preferred) or text (substring).",
            "parameters": {
                "type": "object",
                "properties": {
                    "note": {"type": "string", "description": "Note containing the task."},
                    "text": {"type": "string", "description": "Task text (case-insensitive substring). Must match exactly one task, else the command is refused — use task_id to disambiguate."},
                    "task_id": {"type": "string", "description": "Exact task UUID from list_tasks. Preferred."},
                },
                "required": ["note"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_tags",
            "description": "List all tags with note counts, or show notes for a specific tag.",
            "parameters": {
                "type": "object",
                "properties": {
                    "tag": {"type": "string", "description": "Show notes for this tag."},
                    "prefix": {"type": "string", "description": "Filter tags by prefix."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "backlinks",
            "description": "Show notes that reference (link to) a target note.",
            "parameters": {
                "type": "object",
                "properties": {
                    "note": {"type": "string", "description": "Target note title, UUID, or filename."},
                    "context": {"type": "boolean", "description": "Show the paragraph containing the link."},
                },
                "required": ["note"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "daily_note",
            "description": "Show or create a daily note.",
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {"type": "string", "description": "Date: YYYY-MM-DD, 'today', 'yesterday', 'tomorrow'."},
                    "create": {"type": "boolean", "description": "Create if it doesn't exist."},
                    "tasks": {"type": "boolean", "description": "Show tasks from the daily note."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "daily_append",
            "description": "Append text to a daily note (creates it if needed).",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Text to append."},
                    "date": {"type": "string", "description": "Date (default: today)."},
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "daily_overdue",
            "description": "Insert or refresh the overdue / due-today task block in a daily note. Safe to call repeatedly — the block is rewritten in place.",
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {"type": "string", "description": "Date (default: today)."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "daily_summary",
            "description": "Insert or refresh the activity summary block in a daily note. Safe to call repeatedly — the block is rewritten in place.",
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {"type": "string", "description": "Date (default: today)."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_note",
            "description": "Soft-delete a note (moves to .trash/, recoverable with restore_note). Permanent deletion / emptying the trash is intentionally not available here — that is left to the user in the app.",
            "parameters": {
                "type": "object",
                "properties": {
                    "note": {"type": "string", "description": "Note title, UUID, or filename."},
                },
                "required": ["note"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "restore_note",
            "description": "Restore a note from the trash (the inverse of delete_note).",
            "parameters": {
                "type": "object",
                "properties": {
                    "note": {"type": "string", "description": "Trashed note title, UUID, or filename."},
                },
                "required": ["note"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "rename_note",
            "description": "Rename a note (changes the filename on disk).",
            "parameters": {
                "type": "object",
                "properties": {
                    "note": {"type": "string", "description": "Current note title."},
                    "new_name": {"type": "string", "description": "New name."},
                },
                "required": ["note", "new_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "collection_info",
            "description": "Show information about the active collection (path, note count, tags).",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_projects",
            "description": "List all projects derived from task metadata.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_properties",
            "description": "List all property definitions in the collection schema with types, options, and usage counts.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "show_property",
            "description": "Show details of a property definition including which notes use it.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Property name (case-insensitive)."},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_property",
            "description": "Create a new property definition in the collection schema.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Property name."},
                    "type": {"type": "string", "enum": ["text", "number", "date", "dateTime", "checkbox", "singleSelect", "multiSelect", "url", "email", "phone", "rating"], "description": "Property type."},
                    "options": {"type": "string", "description": "Comma-separated options (for singleSelect/multiSelect)."},
                    "required": {"type": "boolean", "description": "Mark as required."},
                },
                "required": ["name", "type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_property",
            "description": "Delete a property definition. Values in notes are preserved. If the property is still used by notes, deletion is refused and reports the count unless force=true.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Property name (case-insensitive)."},
                    "force": {"type": "boolean", "description": "Skip the 'used by N notes' confirmation and delete anyway. Default false (respects the CLI safety gate)."},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "property_orphans",
            "description": "Find front matter property keys with no schema definition (typos, legacy data).",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "property_notes",
            "description": "List notes using a specific property, optionally filtered by value.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Property name (case-insensitive)."},
                    "value": {"type": "string", "description": "Filter by value (case-insensitive)."},
                },
                "required": ["name"],
            },
        },
    },
]


# ---------------------------------------------------------------------------
# Tool dispatch
# ---------------------------------------------------------------------------

def execute_tool(name: str, args: dict) -> str:
    """Execute a tool call and return the result as a JSON string."""
    collection = args.get("collection")

    if name == "list_notes":
        cmd = ["list"]
        if args.get("tag"):
            cmd += ["--tag", args["tag"]]
        if args.get("sort"):
            cmd += ["--sort", args["sort"]]
        if args.get("limit"):
            cmd += ["--limit", str(args["limit"])]
        if args.get("favourites"):
            cmd.append("--favourites")
        if args.get("has_tasks"):
            cmd.append("--has-tasks")
        if args.get("has_overdue"):
            cmd.append("--has-overdue")
        if args.get("archived"):
            cmd.append("--archived")
        if args.get("modified_after"):
            cmd += ["--modified-after", args["modified_after"]]
        if args.get("modified_before"):
            cmd += ["--modified-before", args["modified_before"]]
        return json.dumps(run_fn(cmd, collection), indent=2)

    elif name == "show_note":
        cmd = ["show", args["note"]]
        if args.get("properties"):
            cmd.append("--properties")
        elif args.get("tasks"):
            cmd.append("--tasks")
        elif args.get("backlinks"):
            cmd.append("--backlinks")
        elif args.get("body", True):
            cmd.append("--body")
        return json.dumps(run_fn(cmd, collection), indent=2)

    elif name == "create_note":
        cmd = ["create", args["title"]]
        if args.get("content"):
            cmd += ["--content", args["content"]]
        for t in args.get("tags", []):
            cmd += ["--tag", t]
        return json.dumps(run_fn(cmd, collection), indent=2)

    elif name == "edit_note":
        cmd = ["edit", args["note"]]
        if args.get("append"):
            cmd += ["--append", args["append"]]
        if args.get("prepend_after_heading"):
            cmd += ["--prepend-after-heading", args["prepend_after_heading"]]
        if args.get("content"):
            cmd += ["--content", args["content"]]
        for p in args.get("set_property", []):
            cmd += ["--set-property", p]
        if args.get("favourite"):
            cmd.append("--favourite")
        if args.get("unfavourite"):
            cmd.append("--unfavourite")
        if args.get("archive"):
            cmd.append("--archive")
        if args.get("unarchive"):
            cmd.append("--unarchive")
        return json.dumps(run_fn(cmd, collection), indent=2)

    elif name == "search_notes":
        cmd = ["search", args["query"]]
        if args.get("context"):
            cmd += ["--context", str(args["context"])]
        if args.get("tag"):
            cmd += ["--tag", args["tag"]]
        if args.get("limit"):
            cmd += ["--limit", str(args["limit"])]
        if args.get("titles_only"):
            cmd.append("--titles-only")
        return json.dumps(run_fn(cmd, collection), indent=2)

    elif name == "list_tasks":
        cmd = ["tasks"]
        if args.get("due_today"):
            cmd.append("--due-today")
        if args.get("due_this_week"):
            cmd.append("--due-this-week")
        if args.get("overdue"):
            cmd.append("--overdue")
        if args.get("status"):
            cmd += ["--status", args["status"]]
        if args.get("priority"):
            cmd += ["--priority", args["priority"]]
        if args.get("note"):
            cmd += ["--note", args["note"]]
        if args.get("project"):
            cmd += ["--project", args["project"]]
        if args.get("all"):
            cmd.append("--all")
        return json.dumps(run_fn(cmd, collection), indent=2)

    elif name == "add_task":
        cmd = ["tasks", "add", args["text"], "--note", args["note"]]
        if args.get("due"):
            cmd += ["--due", args["due"]]
        if args.get("priority"):
            cmd += ["--priority", args["priority"]]
        if args.get("project"):
            cmd += ["--project", args["project"]]
        return json.dumps(run_fn(cmd, collection), indent=2)

    elif name in ("complete_task", "cancel_task", "start_task", "reset_task"):
        sub = {
            "complete_task": "complete",
            "cancel_task": "cancel",
            "start_task": "progress",
            "reset_task": "reset",
        }[name]
        sel = _task_selector(args)
        if sel is None:
            return json.dumps({"error": "Provide either text or task_id."}, indent=2)
        return json.dumps(run_fn(["tasks", sub] + sel + ["--note", args["note"]], collection), indent=2)

    elif name == "set_task":
        sel = _task_selector(args)
        if sel is None:
            return json.dumps({"error": "Provide either text or task_id."}, indent=2)
        cmd = ["tasks", "set"] + sel + ["--note", args["note"]]
        if args.get("due"):
            cmd += ["--due", args["due"]]
        if args.get("clear_due"):
            cmd.append("--clear-due")
        if args.get("priority"):
            cmd += ["--priority", args["priority"]]
        if args.get("clear_priority"):
            cmd.append("--clear-priority")
        if args.get("project"):
            cmd += ["--project", args["project"]]
        if args.get("clear_project"):
            cmd.append("--clear-project")
        return json.dumps(run_fn(cmd, collection), indent=2)

    elif name == "remove_task":
        sel = _task_selector(args)
        if sel is None:
            return json.dumps({"error": "Provide either text or task_id."}, indent=2)
        return json.dumps(run_fn(["tasks", "remove"] + sel + ["--note", args["note"]], collection), indent=2)

    elif name == "list_tags":
        cmd = ["tags"]
        if args.get("tag"):
            cmd.append(args["tag"])
        if args.get("prefix"):
            cmd += ["--prefix", args["prefix"]]
        return json.dumps(run_fn(cmd, collection), indent=2)

    elif name == "backlinks":
        cmd = ["backlinks", args["note"]]
        if args.get("context"):
            cmd.append("--context")
        return json.dumps(run_fn(cmd, collection), indent=2)

    elif name == "daily_note":
        cmd = ["daily"]
        if args.get("date"):
            cmd.append(args["date"])
        if args.get("create"):
            cmd.append("--create")
        if args.get("tasks"):
            cmd.append("--tasks")
        return json.dumps(run_fn(cmd, collection), indent=2)

    elif name == "daily_append":
        cmd = ["daily", "append", args["text"]]
        if args.get("date"):
            cmd += ["--date", args["date"]]
        return json.dumps(run_fn(cmd, collection), indent=2)

    elif name == "daily_overdue":
        cmd = ["daily", "overdue"]
        if args.get("date"):
            cmd.append(args["date"])
        return json.dumps(run_fn(cmd, collection), indent=2)

    elif name == "daily_summary":
        cmd = ["daily", "summary"]
        if args.get("date"):
            cmd.append(args["date"])
        return json.dumps(run_fn(cmd, collection), indent=2)

    elif name == "delete_note":
        return json.dumps(run_fn(["delete", args["note"]], collection), indent=2)

    elif name == "restore_note":
        return json.dumps(run_fn(["restore", args["note"]], collection), indent=2)

    elif name == "rename_note":
        return json.dumps(run_fn(["rename", args["note"], args["new_name"]], collection), indent=2)

    elif name == "collection_info":
        return json.dumps(run_fn(["collection", "info"], collection), indent=2)

    elif name == "list_projects":
        return json.dumps(run_fn(["tasks", "projects"], collection), indent=2)

    elif name == "list_properties":
        return json.dumps(run_fn(["properties", "list"], collection), indent=2)

    elif name == "show_property":
        return json.dumps(run_fn(["properties", "show", args["name"]], collection), indent=2)

    elif name == "add_property":
        cmd = ["properties", "add", args["name"], "--type", args["type"]]
        if args.get("options"):
            cmd += ["--property-options", args["options"]]
        if args.get("required"):
            cmd.append("--required")
        return json.dumps(run_fn(cmd, collection), indent=2)

    elif name == "delete_property":
        cmd = ["properties", "delete", args["name"]]
        if args.get("force"):
            cmd.append("--force")
        return json.dumps(run_fn(cmd, collection), indent=2)

    elif name == "property_orphans":
        return json.dumps(run_fn(["properties", "orphans"], collection), indent=2)

    elif name == "property_notes":
        cmd = ["properties", "notes", args["name"]]
        if args.get("value"):
            cmd += ["--value", args["value"]]
        return json.dumps(run_fn(cmd, collection), indent=2)

    return json.dumps({"error": f"Unknown tool: {name}"})


# ---------------------------------------------------------------------------
# Ollama API client
# ---------------------------------------------------------------------------

def ollama_chat(
    url: str,
    model: str,
    messages: list,
    tools: list,
) -> dict:
    """Send a chat request to Ollama and return the response."""
    try:
        import requests
    except ImportError:
        print("\nError: 'requests' library required. Install with: pip install requests")
        sys.exit(1)

    payload = {
        "model": model,
        "messages": messages,
        "tools": tools,
        "stream": False,
    }

    try:
        resp = requests.post(f"{url}/api/chat", json=payload, timeout=300)
        resp.raise_for_status()
        return resp.json()
    except requests.ConnectionError:
        print(f"\nError: Cannot connect to Ollama at {url}")
        print("Make sure Ollama is running: ollama serve")
        sys.exit(1)
    except requests.Timeout:
        print("\nError: Ollama request timed out (300s). The model may still be loading — try again, or use a smaller model.")
        sys.exit(1)
    except requests.HTTPError as e:
        if e.response.status_code == 404:
            print(f"\nError: Model '{model}' not found. Pull it first: ollama pull {model}")
        else:
            print(f"\nError: Ollama returned {e.response.status_code}: {e.response.text}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Chat loop
# ---------------------------------------------------------------------------

def print_tool_call(name: str, args: dict) -> None:
    """Print a compact representation of a tool call."""
    arg_str = ", ".join(f"{k}={repr(v)}" for k, v in args.items() if v is not None)
    print(f"  -> {name}({arg_str})")


def chat_loop(url: str, model: str) -> None:
    """Main interactive chat loop."""
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    print(f"FoldNotes Chat v{__version__} — model: {model}")
    print(f"Ollama: {url}")
    print(f"fn CLI: {FN_BIN}")
    print("Type /quit to exit, /clear to reset, /tools to list tools, /model <name> to switch, /version.\n")

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye!")
            break

        if not user_input:
            continue

        if user_input.startswith("/"):
            cmd_parts = user_input.split(maxsplit=1)
            cmd = cmd_parts[0].lower()

            if cmd in ("/quit", "/exit"):
                print("Bye!")
                break
            elif cmd == "/clear":
                messages = [{"role": "system", "content": SYSTEM_PROMPT}]
                print("Conversation cleared.\n")
                continue
            elif cmd == "/model":
                if len(cmd_parts) > 1:
                    model = cmd_parts[1].strip()
                    print(f"Switched to model: {model}\n")
                else:
                    print(f"Current model: {model}\n")
                continue
            elif cmd == "/version":
                print(f"FoldNotes Chat: v{__version__}")
                try:
                    r = subprocess.run([FN_BIN, "--version"], capture_output=True, text=True, timeout=5)
                    print(f"fn CLI: {r.stdout.strip()}")
                except Exception:
                    print(f"fn CLI: unknown (at {FN_BIN})")
                print()
                continue
            elif cmd == "/tools":
                print("Available tools:")
                for t in TOOLS:
                    print(f"  - {t['function']['name']}: {t['function']['description'][:70]}")
                print()
                continue
            else:
                print(f"Unknown command: {cmd}\n")
                continue

        messages.append({"role": "user", "content": user_input})

        max_rounds = 10
        for _ in range(max_rounds):
            response = ollama_chat(url, model, messages, TOOLS)
            message = response.get("message", {})
            messages.append(message)

            tool_calls = message.get("tool_calls")

            if not tool_calls:
                content = message.get("content", "").strip()
                if content:
                    print(f"\nAssistant: {content}\n")
                else:
                    print("\nAssistant: (no response)\n")
                break

            for tc in tool_calls:
                fn_info = tc.get("function", {})
                fn_name = fn_info.get("name", "")
                fn_args = fn_info.get("arguments", {})

                print_tool_call(fn_name, fn_args)
                result = execute_tool(fn_name, fn_args)

                messages.append({
                    "role": "tool",
                    "content": result,
                })
        else:
            print("\nAssistant: (stopped after too many tool rounds)\n")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Chat with your FoldNotes knowledge base using a local Ollama model.",
    )
    parser.add_argument(
        "--model", "-m",
        default=DEFAULT_MODEL,
        help=f"Ollama model name (default: {DEFAULT_MODEL}).",
    )
    parser.add_argument(
        "--url", "-u",
        default=DEFAULT_OLLAMA_URL,
        help=f"Ollama API URL (default: {DEFAULT_OLLAMA_URL}).",
    )
    args = parser.parse_args()

    try:
        result = subprocess.run([FN_BIN, "--version"], capture_output=True, text=True, timeout=5)
        if result.returncode != 0:
            print(f"Warning: fn CLI at {FN_BIN} returned an error. Tools may not work.")
    except FileNotFoundError:
        print(f"Warning: fn CLI not found at {FN_BIN}.")
        print("Install via FoldNotes > Install Command Line Tool, or build with:")
        print("  cd Shared && swift build && sudo cp .build/debug/fn /usr/local/bin/fn")
        print()

    chat_loop(args.url, args.model)


if __name__ == "__main__":
    main()
