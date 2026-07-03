# FoldNotes MCP Server

A [Model Context Protocol](https://modelcontextprotocol.io) server that exposes the
FoldNotes command-line tool (`fn`) as tools any MCP-compatible client can call —
[Claude Desktop](https://claude.ai/download), [Claude Code](https://claude.com/claude-code),
or third-party clients. It's a thin, read/write bridge to your FoldNotes collection:
everything the CLI can do, an AI assistant can do — under the same licensing and safety limits.

Made by **Foldsoft Pty Ltd** · [foldnotes.io](https://foldnotes.io)

> **Source-available, install-only.** This repository is published so you can install and run the
> server — we don't accept code contributions. Questions, tips, and bug reports are welcome in
> [Discussions](https://github.com/Foldsoft/foldnotes-mcp/discussions); see
> [CONTRIBUTING](CONTRIBUTING.md) for direct support and security reporting.

## Requirements

- **macOS** with the FoldNotes app, and the **`fn` CLI** installed from the app:
  **FoldNotes → Install Command Line Tool**. The server shells out to `fn`.
- **Python 3.10+**.

## Install

```bash
git clone https://github.com/Foldsoft/foldnotes-mcp.git
cd foldnotes-mcp/python-sdk
python3 -m venv venv
source venv/bin/activate
pip install mcp
```

Verify:

```bash
fn version                 # confirm the CLI is on your PATH
python3 foldnotes_mcp.py   # starts the server on stdio — Ctrl-C to stop
```

The server auto-discovers `fn` at `/usr/local/bin/fn` or `/opt/homebrew/bin/fn`, falling
back to `fn` on your `PATH`.

## Connect to Claude Desktop

Add a `foldnotes` entry to `~/Library/Application Support/Claude/claude_desktop_config.json`
(absolute paths to the venv's Python and the server script):

```json
{
  "mcpServers": {
    "foldnotes": {
      "command": "/absolute/path/to/foldnotes-mcp/python-sdk/venv/bin/python3",
      "args": ["/absolute/path/to/foldnotes-mcp/python-sdk/foldnotes_mcp.py"]
    }
  }
}
```

Restart Claude Desktop; the FoldNotes tools appear under the tools menu.

## Connect to Claude Code

```bash
claude mcp add foldnotes -- \
  /absolute/path/to/foldnotes-mcp/python-sdk/venv/bin/python3 \
  /absolute/path/to/foldnotes-mcp/python-sdk/foldnotes_mcp.py
```

Add `--scope user` for all projects, or `--scope project` to share a `.mcp.json`. Confirm with
`claude mcp list` or `/mcp` inside a session.

## What it exposes

40+ tools covering notes, tasks, tags, backlinks, daily notes, properties/schema, saved
queries, templates, projects, attachments, collections, and search. The full, always-current
list is in the docs: **[foldnotes.io → AI & Automation](https://foldnotes.io/advanced/ai-integration/)**.

Two safety rules are built in:

- **Reading is free; writing needs a licence.** Read tools always work; write tools and `bind`
  require a valid FoldNotes licence or active trial, otherwise they change nothing.
- **Trash is human-owned.** The server can move a note to the trash and restore it, but cannot
  empty the trash or permanently delete — that stays a deliberate, human-only action in the app.

## Questions & support

- **Setup help, usage questions, bugs, tips** → [Discussions](https://github.com/Foldsoft/foldnotes-mcp/discussions).
- **Direct support** → [support@foldnotes.io](mailto:support@foldnotes.io).
- **Security** → report privately via the repo's **Security → Report a vulnerability**. We don't use
  Issues or accept pull requests — see [CONTRIBUTING](CONTRIBUTING.md).

## Licence

MIT © 2026 Foldsoft Pty Ltd — see [LICENSE](LICENSE).
