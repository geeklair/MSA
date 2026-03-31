# MCP Setup — Direct Claude Access to MSA Codebase

This guide configures the Model Context Protocol (MCP) so Claude can directly
read, edit, and run code on your Spark workstation without copy/paste.

## What MCP Gives You

- Claude reads files directly from your filesystem
- Claude edits code and you see the diff
- Claude runs shell commands and sees the output
- Full back-and-forth without downloading/uploading zips

## Option A: Claude Code (Recommended)

Claude Code is Anthropic's CLI tool that gives Claude direct filesystem and
shell access via MCP.

### Install on Spark

```bash
# Requires Node.js 18+
node --version   # check

# Install Claude Code
npm install -g @anthropic-ai/claude-code

# Navigate to your MSA directory
cd ~/projects/msa

# Start a Claude Code session
claude
```

Claude Code will have full access to your MSA directory — it can read, write,
and execute files directly.

### Usage

```
> Review the dispatcher.py and suggest improvements
> Run the agent once and show me the log output
> Add a new tool called "slack_post" that sends a message to a channel
```

## Option B: MCP Filesystem Server (for claude.ai web)

This exposes your filesystem to Claude in the browser via an MCP server.

### Install

```bash
npm install -g @modelcontextprotocol/server-filesystem
```

### Run the MCP server pointing at your MSA directory

```bash
npx @modelcontextprotocol/server-filesystem ~/projects/msa
```

### Connect in Claude.ai

1. Go to claude.ai → Settings → Integrations
2. Add MCP server: `http://spark-68ad:PORT`
3. Claude can now browse and edit your files directly

## Option C: SSH + shell_tool via MCP

For full shell access (not just filesystem):

```bash
npm install -g @modelcontextprotocol/server-ssh
```

Configure with your Spark credentials and Claude gets SSH-level access.

## Security Notes

- MCP servers run locally on Spark — traffic stays on your network
- Use SSH tunneling if connecting from outside your network
- The `shell` tool in MSA itself gives the agent shell access — scope carefully
- Consider running the MCP server only when actively working with Claude

## Recommended Workflow

```
Terminal on Spark:
  cd ~/projects/msa && claude    ← Claude Code session

In the Claude Code session:
  "Run the agent once and show me what happened"
  "The dispatcher isn't parsing JSON correctly — fix it"
  "Add a Sage/Waggle tool that posts to a plugin"
```

This is the tightest possible loop between you, Claude, and the running code.
