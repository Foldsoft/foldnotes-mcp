#!/usr/bin/env python3
# Copyright (c) 2026 Foldsoft Pty Ltd. Released under the MIT Licence — see LICENSE.
"""
FoldNotes MCP Server (Python 3.10+ with official MCP SDK)

A Model Context Protocol server that exposes FoldNotes CLI (`fn`) commands
as tools for AI models. Works with Claude Desktop, Claude Code, or any
MCP-compatible client.

Requires:
    Python 3.10+
    pip install mcp
    `fn` CLI installed (via FoldNotes app or manual symlink)

Usage:
    python3 foldnotes_mcp.py

Configure in Claude Desktop's claude_desktop_config.json:
    {
      "mcpServers": {
        "foldnotes": {
          "command": "/path/to/venv/bin/python3",
          "args": ["/path/to/python-sdk/foldnotes_mcp.py"]
        }
      }
    }
"""

__version__ = "2.3.0"

import json
import os
import subprocess
from typing import Any

from mcp.server.fastmcp import FastMCP

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


def run_fn(args: list[str], collection: str | None = None) -> dict:
    """Run an fn CLI command and return parsed JSON or raw text."""
    cmd = [FN_BIN]
    cmd += args
    cmd += ["--quiet", "--json"]
    if collection:
        cmd += ["--collection", collection]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        return {"error": f"fn CLI not found. Tried: {FN_BIN}"}
    except subprocess.TimeoutExpired:
        return {"error": "fn command timed out after 30 seconds"}

    output = result.stdout.strip()
    if result.returncode != 0:
        err = result.stderr.strip() or output or "Unknown error"
        return {"error": err}

    try:
        return json.loads(output)
    except json.JSONDecodeError:
        return {"text": output}


def run_fn_raw(args: list[str], collection: str | None = None) -> str:
    """Run fn without --json, return raw stdout."""
    cmd = [FN_BIN]
    cmd += args
    cmd += ["--quiet"]
    if collection:
        cmd += ["--collection", collection]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        return f"Error: fn CLI not found at {FN_BIN}"
    except subprocess.TimeoutExpired:
        return "Error: command timed out"

    if result.returncode != 0:
        return result.stderr.strip() or result.stdout.strip() or "Unknown error"
    return result.stdout.strip()


def _task_selector(text: str | None, task_id: str | None) -> list[str] | None:
    """Build the CLI args that identify a single task.

    Prefers the exact paragraph UUID (`--id`, from `list_tasks`), which targets
    one task unambiguously even when several share the same wording. Falls back
    to a case-insensitive substring of the task text. A substring must match
    exactly one task or the CLI refuses the command (use task_id to disambiguate).
    Returns None when neither is supplied so the caller can surface an error.
    """
    if task_id:
        return ["--id", task_id]
    if text:
        return [text]
    return None


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------

mcp = FastMCP("FoldNotes")


# ---- Version ----

@mcp.tool()
def version() -> str:
    """Show the FoldNotes MCP server version and fn CLI version."""
    fn_version = run_fn_raw(["--version"])
    return json.dumps({
        "mcp_server": __version__,
        "fn_cli": fn_version,
        "fn_path": FN_BIN,
    }, indent=2)


# ---- Notes: List ----

@mcp.tool()
def list_notes(
    tag: str | None = None,
    property: str | None = None,
    sort: str | None = None,
    limit: int | None = None,
    favourites: bool = False,
    has_tasks: bool = False,
    has_overdue: bool = False,
    include_trashed: bool = False,
    archived: bool = False,
    include_archived: bool = False,
    include_subtags: bool = False,
    modified_after: str | None = None,
    modified_before: str | None = None,
    reverse: bool = False,
    collection: str | None = None,
) -> str:
    """List notes in the active FoldNotes collection with filtering and sorting.

    Args:
        tag: Filter by tag name (without #).
        property: Filter by property (key=value).
        sort: Sort by: modified, created, title.
        limit: Max notes to return.
        favourites: Only show favourited notes.
        has_tasks: Only show notes with active tasks.
        has_overdue: Only show notes with overdue tasks.
        include_trashed: Include trashed notes in results.
        archived: Show only archived notes.
        include_archived: Include archived notes alongside active ones (distinct from `archived`, which shows only archived).
        include_subtags: When filtering by tag, also match sub-tags (e.g. projects matches projects/marketing).
        modified_after: Only notes modified after this date (YYYY-MM-DD).
        modified_before: Only notes modified before this date (YYYY-MM-DD).
        reverse: Reverse sort order.
        collection: Collection name, UUID, or path (default: active).
    """
    args = ["list"]
    if tag:
        args += ["--tag", tag]
    if property:
        args += ["--property", property]
    if sort:
        args += ["--sort", sort]
    if limit:
        args += ["--limit", str(limit)]
    if favourites:
        args.append("--favourites")
    if has_tasks:
        args.append("--has-tasks")
    if has_overdue:
        args.append("--has-overdue")
    if include_trashed:
        args.append("--include-trashed")
    if archived:
        args.append("--archived")
    if include_archived:
        args.append("--include-archived")
    if include_subtags:
        args.append("--include-subtags")
    if modified_after:
        args += ["--modified-after", modified_after]
    if modified_before:
        args += ["--modified-before", modified_before]
    if reverse:
        args.append("--reverse")
    return json.dumps(run_fn(args, collection), indent=2)


# ---- Notes: Show ----

@mcp.tool()
def show_note(
    note: str | None = None,
    id: str | None = None,
    body: bool = True,
    properties: bool = False,
    tasks: bool = False,
    backlinks: bool = False,
    collection: str | None = None,
) -> str:
    """Read a note's content and metadata. Resolves by title, UUID, or filename.

    Args:
        note: Note title, UUID, or filename.
        id: Note UUID (exact lookup; alternative to the fuzzy note/title).
        body: Include full body text (default: true).
        properties: Show only front matter / properties.
        tasks: Show tasks in the note.
        backlinks: Show notes that link to this one.
        collection: Collection name, UUID, or path.
    """
    args = ["show"]
    if id:
        args += ["--id", id]
    elif note:
        args.append(note)
    if properties:
        args.append("--properties")
    elif tasks:
        args.append("--tasks")
    elif backlinks:
        args.append("--backlinks")
    elif body:
        args.append("--body")
    return json.dumps(run_fn(args, collection), indent=2)


# ---- Notes: Create ----

@mcp.tool()
def create_note(
    title: str,
    content: str | None = None,
    tags: list[str] | None = None,
    properties: list[str] | None = None,
    collection: str | None = None,
) -> str:
    """Create a new note with optional content, tags, and properties.

    Args:
        title: Note title (becomes the filename).
        content: Note body content (markdown).
        tags: Tags to add (without #).
        properties: User properties as key=value strings.
        collection: Collection name, UUID, or path.
    """
    args = ["create", title]
    if content:
        args += ["--content", content]
    for t in tags or []:
        args += ["--tag", t]
    for p in properties or []:
        args += ["--property", p]
    return json.dumps(run_fn(args, collection), indent=2)


# ---- Notes: Edit ----

@mcp.tool()
def edit_note(
    note: str,
    append: str | None = None,
    prepend_after_heading: str | None = None,
    content: str | None = None,
    set_property: list[str] | None = None,
    remove_property: list[str] | None = None,
    favourite: bool = False,
    unfavourite: bool = False,
    archive: bool = False,
    unarchive: bool = False,
    force: bool = False,
    collection: str | None = None,
) -> str:
    """Modify a note's content or properties. Property values are validated
    against the schema (name casing, type, select options) unless force=True.

    Args:
        note: Note title, UUID, or filename.
        append: Text to append to the end of the note.
        prepend_after_heading: Text to insert after the first heading.
        content: Replace entire body with this content.
        set_property: Properties to set as key=value strings. Validated against schema.
        remove_property: Properties to remove by key name.
        favourite: Mark as favourite.
        unfavourite: Remove favourite.
        archive: Archive the note.
        unarchive: Unarchive the note.
        force: Bypass property schema validation.
        collection: Collection name, UUID, or path.
    """
    args = ["edit", note]
    if append:
        args += ["--append", append]
    if prepend_after_heading:
        args += ["--prepend-after-heading", prepend_after_heading]
    if content:
        args += ["--content", content]
    for p in set_property or []:
        args += ["--set-property", p]
    for p in remove_property or []:
        args += ["--remove-property", p]
    if favourite:
        args.append("--favourite")
    if unfavourite:
        args.append("--unfavourite")
    if archive:
        args.append("--archive")
    if unarchive:
        args.append("--unarchive")
    if force:
        args.append("--force")
    return json.dumps(run_fn(args, collection), indent=2)


# ---- Notes: Delete ----

@mcp.tool()
def delete_note(
    note: str,
    collection: str | None = None,
) -> str:
    """Soft-delete a note: moves it to the .trash/ folder. Always reversible
    with restore_note.

    Deletion via this MCP is intentionally reversible — notes go to the trash,
    never permanently. Emptying the trash / permanent deletion is deliberately
    NOT exposed here; that irreversible step is left to the user in the app.

    Args:
        note: Note title, UUID, or filename.
        collection: Collection name, UUID, or path.
    """
    return json.dumps(run_fn(["delete", note], collection), indent=2)


@mcp.tool()
def restore_note(
    note: str,
    collection: str | None = None,
) -> str:
    """Restore a note from the trash — the inverse of delete_note.

    Args:
        note: Trashed note title, UUID, or filename.
        collection: Collection name, UUID, or path.
    """
    return json.dumps(run_fn(["restore", note], collection), indent=2)


# ---- Notes: Rename ----

@mcp.tool()
def rename_note(
    note: str,
    new_name: str,
    collection: str | None = None,
) -> str:
    """Rename a note (changes the filename on disk).

    Args:
        note: Current note title, UUID, or filename.
        new_name: New name for the note.
        collection: Collection name, UUID, or path.
    """
    return json.dumps(run_fn(["rename", note, new_name], collection), indent=2)


# ---- Notes: Search ----

@mcp.tool()
def search_notes(
    query: str,
    context: int = 2,
    tag: str | None = None,
    limit: int | None = None,
    titles_only: bool = False,
    regex: bool = False,
    collection: str | None = None,
) -> str:
    """Full-text search across all notes in the collection.

    Args:
        query: Search query string.
        context: Lines of context around matches (default: 2).
        tag: Filter results to notes with this tag.
        limit: Maximum number of results.
        titles_only: Search titles only, not body content.
        regex: Treat query as a regular expression.
        collection: Collection name, UUID, or path.
    """
    args = ["search", query, "--context", str(context)]
    if tag:
        args += ["--tag", tag]
    if limit:
        args += ["--limit", str(limit)]
    if titles_only:
        args.append("--titles-only")
    if regex:
        args.append("--regex")
    return json.dumps(run_fn(args, collection), indent=2)


# ---- Tags ----

@mcp.tool()
def list_tags(
    tag: str | None = None,
    prefix: str | None = None,
    collection: str | None = None,
) -> str:
    """List all tags with note counts, or show notes for a specific tag.

    Args:
        tag: Show notes tagged with this tag.
        prefix: Filter tags by prefix.
        collection: Collection name, UUID, or path.
    """
    args = ["tags"]
    if tag:
        args.append(tag)
    if prefix:
        args += ["--prefix", prefix]
    return json.dumps(run_fn(args, collection), indent=2)


# ---- Tasks: List ----

@mcp.tool()
def list_tasks(
    due_today: bool = False,
    due_this_week: bool = False,
    overdue: bool = False,
    status: str | None = None,
    priority: str | None = None,
    note: str | None = None,
    project: str | None = None,
    context: str | None = None,
    all: bool = False,
    collection: str | None = None,
) -> str:
    """List tasks across all notes with filtering.

    Args:
        due_today: Show only tasks due today.
        due_this_week: Show tasks due this week.
        overdue: Show only overdue tasks.
        status: Filter by status: not-started, in-progress, done, cancelled.
        priority: Filter by priority: high, medium, low.
        note: Filter by note title.
        project: Filter by project name.
        context: Filter by context (tasks carrying this context).
        all: Include done and cancelled tasks.
        collection: Collection name, UUID, or path.
    """
    args = ["tasks"]
    if due_today:
        args.append("--due-today")
    if due_this_week:
        args.append("--due-this-week")
    if overdue:
        args.append("--overdue")
    if status:
        args += ["--status", status]
    if priority:
        args += ["--priority", priority]
    if note:
        args += ["--note", note]
    if project:
        args += ["--project", project]
    if context:
        args += ["--context", context]
    if all:
        args.append("--all")
    return json.dumps(run_fn(args, collection), indent=2)


# ---- Tasks: Add ----

@mcp.tool()
def add_task(
    text: str,
    note: str,
    due: str | None = None,
    priority: str | None = None,
    project: str | None = None,
    context: str | None = None,
    collection: str | None = None,
) -> str:
    """Add a new task to a note.

    Args:
        text: Task description text.
        note: Target note title to add the task to.
        due: Due date (YYYY-MM-DD, 'today', 'tomorrow', 'next monday', etc.).
        priority: Priority level: high, medium, low.
        project: Project name for the task.
        context: Context(s) for the task; comma-separate for several (e.g. "errand,phone").
        collection: Collection name, UUID, or path.
    """
    args = ["tasks", "add", text, "--note", note]
    if due:
        args += ["--due", due]
    if priority:
        args += ["--priority", priority]
    if project:
        args += ["--project", project]
    if context:
        args += ["--context", context]
    return json.dumps(run_fn(args, collection), indent=2)


# ---- Tasks: Complete ----

@mcp.tool()
def complete_task(
    note: str,
    text: str | None = None,
    task_id: str | None = None,
    collection: str | None = None,
) -> str:
    """Mark a task as done.

    Identify the task by `task_id` (exact, preferred) or `text` (substring).

    Args:
        note: Note title containing the task.
        text: Task text to match (case-insensitive substring). Must match exactly
            one task; if several match, the command is refused — pass task_id.
        task_id: Exact task UUID from `list_tasks`. Preferred — it targets the
            precise task even when several share the same wording.
        collection: Collection name, UUID, or path.
    """
    sel = _task_selector(text, task_id)
    if sel is None:
        return json.dumps({"error": "Provide either text or task_id."}, indent=2)
    return json.dumps(run_fn(["tasks", "complete"] + sel + ["--note", note], collection), indent=2)


# ---- Tasks: Cancel ----

@mcp.tool()
def cancel_task(
    note: str,
    text: str | None = None,
    task_id: str | None = None,
    collection: str | None = None,
) -> str:
    """Cancel a task.

    Identify the task by `task_id` (exact, preferred) or `text` (substring).

    Args:
        note: Note title containing the task.
        text: Task text to match (case-insensitive substring). Must match exactly
            one task; if several match, the command is refused — pass task_id.
        task_id: Exact task UUID from `list_tasks`. Preferred — it targets the
            precise task even when several share the same wording.
        collection: Collection name, UUID, or path.
    """
    sel = _task_selector(text, task_id)
    if sel is None:
        return json.dumps({"error": "Provide either text or task_id."}, indent=2)
    return json.dumps(run_fn(["tasks", "cancel"] + sel + ["--note", note], collection), indent=2)


# ---- Tasks: Progress ----

@mcp.tool()
def start_task(
    note: str,
    text: str | None = None,
    task_id: str | None = None,
    collection: str | None = None,
) -> str:
    """Mark a task as in-progress.

    Identify the task by `task_id` (exact, preferred) or `text` (substring).

    Args:
        note: Note title containing the task.
        text: Task text to match (case-insensitive substring). Must match exactly
            one task; if several match, the command is refused — pass task_id.
        task_id: Exact task UUID from `list_tasks`. Preferred — it targets the
            precise task even when several share the same wording.
        collection: Collection name, UUID, or path.
    """
    sel = _task_selector(text, task_id)
    if sel is None:
        return json.dumps({"error": "Provide either text or task_id."}, indent=2)
    return json.dumps(run_fn(["tasks", "progress"] + sel + ["--note", note], collection), indent=2)


# ---- Tasks: Reset ----

@mcp.tool()
def reset_task(
    note: str,
    text: str | None = None,
    task_id: str | None = None,
    collection: str | None = None,
) -> str:
    """Reset a task to not-started.

    Identify the task by `task_id` (exact, preferred) or `text` (substring).

    Args:
        note: Note title containing the task.
        text: Task text to match (case-insensitive substring). Must match exactly
            one task; if several match, the command is refused — pass task_id.
        task_id: Exact task UUID from `list_tasks`. Preferred — it targets the
            precise task even when several share the same wording.
        collection: Collection name, UUID, or path.
    """
    sel = _task_selector(text, task_id)
    if sel is None:
        return json.dumps({"error": "Provide either text or task_id."}, indent=2)
    return json.dumps(run_fn(["tasks", "reset"] + sel + ["--note", note], collection), indent=2)


# ---- Tasks: Set (amend due / priority / project) ----

@mcp.tool()
def set_task(
    note: str,
    text: str | None = None,
    task_id: str | None = None,
    due: str | None = None,
    clear_due: bool = False,
    priority: str | None = None,
    clear_priority: bool = False,
    project: str | None = None,
    clear_project: bool = False,
    context: str | None = None,
    clear_context: bool = False,
    collection: str | None = None,
) -> str:
    """Amend an existing task's due date, priority, project, or context.

    Identify the task by `task_id` (exact, preferred) or `text` (substring),
    then pass at least one field to change. Amending metadata preserves the
    task's stable UUID (identity is hashed over the prose, not the metadata).

    Args:
        note: Note title containing the task.
        text: Task text to match (case-insensitive substring). Must match exactly
            one task; if several match, the command is refused — pass task_id.
        task_id: Exact task UUID from `list_tasks`. Preferred.
        due: New due date (YYYY-MM-DD, 'today', 'tomorrow', 'next monday', ...).
        clear_due: Remove the due date instead of setting one.
        priority: New priority: high, medium, low.
        clear_priority: Remove the priority.
        project: New project name.
        clear_project: Remove the project.
        context: New context(s), replacing any existing; comma-separate for several.
        clear_context: Remove all contexts.
        collection: Collection name, UUID, or path.
    """
    sel = _task_selector(text, task_id)
    if sel is None:
        return json.dumps({"error": "Provide either text or task_id."}, indent=2)
    args = ["tasks", "set"] + sel + ["--note", note]
    if due:
        args += ["--due", due]
    if clear_due:
        args += ["--clear-due"]
    if priority:
        args += ["--priority", priority]
    if clear_priority:
        args += ["--clear-priority"]
    if project:
        args += ["--project", project]
    if clear_project:
        args += ["--clear-project"]
    if context:
        args += ["--context", context]
    if clear_context:
        args += ["--clear-context"]
    return json.dumps(run_fn(args, collection), indent=2)


# ---- Tasks: Remove ----

@mcp.tool()
def remove_task(
    note: str,
    text: str | None = None,
    task_id: str | None = None,
    collection: str | None = None,
) -> str:
    """Delete a task line from a note.

    Identify the task by `task_id` (exact, preferred) or `text` (substring).
    This permanently removes the task line from the note body — unlike
    delete_note it does not go to the trash. Prefer `task_id`, and confirm with
    `list_tasks` first when matching by text. Surviving tasks keep their UUIDs.

    Args:
        note: Note title containing the task.
        text: Task text to match (case-insensitive substring). Must match exactly
            one task; if several match, the command is refused — pass task_id.
        task_id: Exact task UUID from `list_tasks`. Preferred.
        collection: Collection name, UUID, or path.
    """
    sel = _task_selector(text, task_id)
    if sel is None:
        return json.dumps({"error": "Provide either text or task_id."}, indent=2)
    return json.dumps(run_fn(["tasks", "remove"] + sel + ["--note", note], collection), indent=2)


# ---- Tasks: Projects ----

@mcp.tool()
def list_projects(collection: str | None = None) -> str:
    """List all projects (derived from task metadata).

    Args:
        collection: Collection name, UUID, or path.
    """
    return json.dumps(run_fn(["tasks", "projects"], collection), indent=2)


@mcp.tool()
def list_contexts(collection: str | None = None) -> str:
    """List all task contexts with task counts (derived from task metadata).

    Args:
        collection: Collection name, UUID, or path.
    """
    return json.dumps(run_fn(["tasks", "contexts"], collection), indent=2)


# ---- Backlinks ----

@mcp.tool()
def backlinks(
    note: str,
    context: bool = False,
    collection: str | None = None,
) -> str:
    """Show notes that reference (link to) a target note.

    Args:
        note: Target note title, UUID, or filename.
        context: Show the paragraph containing the [[link]].
        collection: Collection name, UUID, or path.
    """
    args = ["backlinks", note]
    if context:
        args.append("--context")
    return json.dumps(run_fn(args, collection), indent=2)


# ---- Daily Notes ----

@mcp.tool()
def daily_note(
    date: str | None = None,
    create: bool = False,
    tasks: bool = False,
    collection: str | None = None,
) -> str:
    """Show or create a daily note.

    Args:
        date: Date (YYYY-MM-DD, 'today', 'yesterday', 'tomorrow'). Default: today.
        create: Create the daily note if it doesn't exist.
        tasks: Show tasks from the daily note.
        collection: Collection name, UUID, or path.
    """
    args = ["daily"]
    if date:
        args += [date]
    if create:
        args.append("--create")
    if tasks:
        args.append("--tasks")
    return json.dumps(run_fn(args, collection), indent=2)


@mcp.tool()
def daily_append(
    text: str,
    date: str | None = None,
    collection: str | None = None,
) -> str:
    """Append text to a daily note (creates it if needed).

    Args:
        text: Text to append.
        date: Date for the daily note (default: today).
        collection: Collection name, UUID, or path.
    """
    args = ["daily", "append", text]
    if date:
        args += ["--date", date]
    return json.dumps(run_fn(args, collection), indent=2)


# ---- Collections ----

@mcp.tool()
def collection_info(collection: str | None = None) -> str:
    """Show information about the active collection (path, note/tag counts, cache status).

    Args:
        collection: Collection name, UUID, or path.
    """
    return json.dumps(run_fn(["collection", "info"], collection), indent=2)


@mcp.tool()
def list_collections() -> str:
    """List all registered collections."""
    return json.dumps(run_fn(["collections"]), indent=2)


@mcp.tool()
def switch_collection(reference: str) -> str:
    """Switch the active collection.

    Args:
        reference: Collection name, UUID, or path.
    """
    return json.dumps(run_fn(["collection", "switch", reference]), indent=2)


# ---- Properties: Schema ----

@mcp.tool()
def list_properties(collection: str | None = None) -> str:
    """List all property definitions in the collection schema.
    Shows name, type, options, validation rules, and usage counts.

    Args:
        collection: Collection name, UUID, or path.
    """
    return json.dumps(run_fn(["properties", "list"], collection), indent=2)


@mcp.tool()
def show_property(
    name: str,
    collection: str | None = None,
) -> str:
    """Show details of a property definition including type, options,
    validation rules, and which notes use it.

    Args:
        name: Property name (case-insensitive).
        collection: Collection name, UUID, or path.
    """
    return json.dumps(run_fn(["properties", "show", name], collection), indent=2)


@mcp.tool()
def add_property(
    name: str,
    type: str,
    options: str | None = None,
    required: bool = False,
    collection: str | None = None,
) -> str:
    """Create a new property definition in the collection schema.

    Args:
        name: Property name.
        type: Property type: text, number, date, dateTime, checkbox, singleSelect, multiSelect, url, email, phone, rating.
        options: Comma-separated options (required for singleSelect/multiSelect).
        required: Mark this property as required.
        collection: Collection name, UUID, or path.
    """
    args = ["properties", "add", name, "--type", type]
    if options:
        args += ["--property-options", options]
    if required:
        args.append("--required")
    return json.dumps(run_fn(args, collection), indent=2)


@mcp.tool()
def delete_property(
    name: str,
    force: bool = False,
    collection: str | None = None,
) -> str:
    """Delete a property definition from the schema. Values in notes are preserved.

    Deleting a definition that is still in use is guarded: with force=False
    (the default) the CLI does NOT delete — it reports how many notes use the
    property and asks you to re-run with force=True to confirm. Unused
    definitions delete without needing force. Set force=True only when you
    intend to remove a definition you know is still referenced.

    Args:
        name: Property name (case-insensitive).
        force: Skip the "used by N notes" confirmation and delete anyway
            (default: false — respects the CLI safety gate).
        collection: Collection name, UUID, or path.
    """
    args = ["properties", "delete", name]
    if force:
        args.append("--force")
    return json.dumps(run_fn(args, collection), indent=2)


@mcp.tool()
def property_orphans(collection: str | None = None) -> str:
    """Find front matter property keys that have no schema definition.
    Useful for detecting typos, case mismatches, or legacy properties.

    Args:
        collection: Collection name, UUID, or path.
    """
    return json.dumps(run_fn(["properties", "orphans"], collection), indent=2)


@mcp.tool()
def property_notes(
    name: str,
    value: str | None = None,
    collection: str | None = None,
) -> str:
    """List notes that use a specific property, optionally filtered by value.

    Args:
        name: Property name (case-insensitive).
        value: Filter by value (case-insensitive).
        collection: Collection name, UUID, or path.
    """
    args = ["properties", "notes", name]
    if value:
        args += ["--value", value]
    return json.dumps(run_fn(args, collection), indent=2)


# ---- Open in App ----

@mcp.tool()
def open_note(
    note: str | None = None,
    id: str | None = None,
    daily: bool = False,
    collection: str | None = None,
) -> str:
    """Open a note in the FoldNotes app via URL scheme.

    Args:
        note: Note title, UUID, or filename.
        id: Note UUID (exact lookup; alternative to note).
        daily: Open today's daily note instead.
        collection: Collection name, UUID, or path.
    """
    args = ["open"]
    if daily:
        args.append("--daily")
    elif id:
        args += ["--id", id]
    elif note:
        args.append(note)
    return json.dumps(run_fn(args, collection), indent=2)


# ---------------------------------------------------------------------------
# v2.2.0 additions — extra fn CLI surface wrapped as thin passthroughs:
# saved queries, templates, project refactoring, attachments, archive,
# notifications. (bind, import, collection add/remove deliberately omitted.)
# ---------------------------------------------------------------------------

@mcp.tool()
def notifications(collection: str | None = None) -> str:
    """Show notification settings and upcoming task reminders.

    Args:
        collection: Collection name, UUID, or path.
    """
    return json.dumps(run_fn(["notifications"], collection), indent=2)


@mcp.tool()
def list_queries(collection: str | None = None) -> str:
    """List saved queries in the collection (.queries/).

    Args:
        collection: Collection name, UUID, or path.
    """
    return json.dumps(run_fn(["query", "list"], collection), indent=2)


@mcp.tool()
def run_query(name: str, limit: int | None = None, collection: str | None = None) -> str:
    """Run a saved query; returns the matching notes (same shape as list_notes).

    Args:
        name: Saved query name (or unambiguous prefix); see list_queries.
        limit: Maximum number of results.
        collection: Collection name, UUID, or path.
    """
    args = ["query", "run", name]
    if limit is not None:
        args += ["--limit", str(limit)]
    return json.dumps(run_fn(args, collection), indent=2)


@mcp.tool()
def list_templates(collection: str | None = None) -> str:
    """List available note templates.

    Args:
        collection: Collection name, UUID, or path.
    """
    return json.dumps(run_fn(["templates"], collection), indent=2)


@mcp.tool()
def rename_project(
    old_name: str,
    new_name: str,
    include_subtree: bool = False,
    collection: str | None = None,
) -> str:
    """Rename a project across the whole collection (tags + task keywords).

    Args:
        old_name: Existing project name.
        new_name: New project name.
        include_subtree: Also rename sub-projects (old/sub -> new/sub).
        collection: Collection name, UUID, or path.
    """
    args = ["projects", "rename", old_name, new_name]
    if include_subtree:
        args.append("--include-subtree")
    return json.dumps(run_fn(args, collection), indent=2)


@mcp.tool()
def strip_project(name: str, collection: str | None = None) -> str:
    """Remove a project's tags and task keywords across the collection.

    Args:
        name: Project name to strip.
        collection: Collection name, UUID, or path.
    """
    return json.dumps(run_fn(["projects", "strip", name], collection), indent=2)


@mcp.tool()
def rename_context(
    old_name: str,
    new_name: str,
    collection: str | None = None,
) -> str:
    """Rename a task context across the whole collection.

    Renames the one entry within each task's context list, deduping if the new
    name is already present, and leaves any other contexts on the line intact.

    Args:
        old_name: Existing context name.
        new_name: New context name.
        collection: Collection name, UUID, or path.
    """
    return json.dumps(run_fn(["contexts", "rename", old_name, new_name], collection), indent=2)


@mcp.tool()
def strip_context(name: str, collection: str | None = None) -> str:
    """Remove a task context across the whole collection.

    Removes the one entry from each task's context list, dropping the token
    entirely only when it was the last context on the line.

    Args:
        name: Context name to strip.
        collection: Collection name, UUID, or path.
    """
    return json.dumps(run_fn(["contexts", "strip", name], collection), indent=2)


@mcp.tool()
def list_attachments(collection: str | None = None) -> str:
    """List images with size and reference count.

    Args:
        collection: Collection name, UUID, or path.
    """
    return json.dumps(run_fn(["attachments", "list"], collection), indent=2)


@mcp.tool()
def orphan_attachments(collection: str | None = None) -> str:
    """List images that no note references.

    Args:
        collection: Collection name, UUID, or path.
    """
    return json.dumps(run_fn(["attachments", "orphans"], collection), indent=2)


@mcp.tool()
def prune_attachments(force: bool = False, collection: str | None = None) -> str:
    """Delete orphaned images. Without force, only reports what would be deleted.

    Args:
        force: Actually delete the orphaned images (default: dry-run).
        collection: Collection name, UUID, or path.
    """
    args = ["attachments", "prune"]
    if force:
        args.append("--force")
    return json.dumps(run_fn(args, collection), indent=2)


@mcp.tool()
def archive_note(note: str | None = None, id: str | None = None, collection: str | None = None) -> str:
    """Archive a note (excluded from list_notes by default). Same as edit --archive.

    Args:
        note: Note title.
        id: Note UUID (alternative to note).
        collection: Collection name, UUID, or path.
    """
    args = ["archive"]
    if id:
        args += ["--id", id]
    elif note:
        args.append(note)
    return json.dumps(run_fn(args, collection), indent=2)


@mcp.tool()
def unarchive_note(note: str | None = None, id: str | None = None, collection: str | None = None) -> str:
    """Restore an archived note. Same as edit --unarchive.

    Args:
        note: Note title.
        id: Note UUID (alternative to note).
        collection: Collection name, UUID, or path.
    """
    args = ["unarchive"]
    if id:
        args += ["--id", id]
    elif note:
        args.append(note)
    return json.dumps(run_fn(args, collection), indent=2)


def main() -> None:
    """Console-script entry point for ``foldnotes-mcp`` / ``uvx foldnotes-mcp``.

    Starts the MCP server on stdio — the transport Claude Desktop and Claude
    Code use. Equivalent to running ``python3 foldnotes_mcp.py`` directly.
    """
    mcp.run()


if __name__ == "__main__":
    main()
