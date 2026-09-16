# Onboarding — brain

## What it is

`brain` is the team's shared memory: an MCP server where decisions, preferences, and durable context for each project are stored. Each person connects with their own token, and AI tools (Claude Code, Claude Desktop) query and update it while they work.

## How to connect

Token-based authentication (`brain_<hex>`), issued by whoever administers the project. It can be sent in two equivalent ways: as `Authorization: Bearer <token>`, or as an `x-api-key: <token>` header — this second option exists because some MCP clients (Claude.ai's "custom connector" dialog, for example) reserve the `Authorization` header and don't let you set it manually.

### Claude Code (token)

```bash
claude mcp add brain --transport http \
  <url> \
  --header "Authorization: Bearer <token>"
```

### Claude Desktop

Claude Desktop doesn't allow adding custom headers directly, so `mcp-remote` is used as a bridge. In `claude_desktop_config.json` (Settings → Developer → Edit Config menu):

```json
{
  "mcpServers": {
    "brain": {
      "command": "npx",
      "args": [
        "-y",
        "mcp-remote",
        "<url>",
        "--header",
        "Authorization: Bearer <token>"
      ]
    }
  }
}
```

Replace `<url>` with the server endpoint and `<token>` with your personal token. Restart Claude Desktop after saving.

### Claude.ai (custom connector)

Claude.ai's standard "custom connector" dialog doesn't allow setting the `Authorization` header manually. Use `x-api-key` instead if the dialog allows it, or connect via `mcp-remote` with the headers above from a client that does support them.

## What projects exist

- Team projects (visible to everyone): defined in your instance's `supabase/seed.sql` (example: `acme`, `acme-app`, `acme-docs`, `acme-site`).
- Personal projects (visible only to whoever creates them): each person can have their own.

## Visibility rules (summary)

- A team project (`owner_id` null) is visible to everyone; a personal one is visible only to its owner.
- A memory is `shared` (visible to whoever can see the project) or `private` (only to whoever created it).
- Default: `private` in personal projects, `shared` in team projects.
- Only an admin can create new team projects.

## Recommended text for Claude Desktop "personal preferences"

```
I have access to the brain MCP, the team's shared memory. When starting to
work on a project, call get_project_context with the corresponding
project_slug. Use recall before assuming you don't know something about the
project or the team. Use remember to save decisions or context worth
remembering; mark as private only what teammates shouldn't see.
```

## Recommended text for a repo's `CLAUDE.md`

```
## Shared memory (MCP brain)
This repo corresponds to the `<project_slug>` project in the `brain` MCP.
When starting a session, call `get_project_context` with `project_slug: "<project_slug>"`.
Use `remember` with that `project_slug` to save decisions or durable context.
Before assuming you don't know something about the project or the company, use `recall`.
```

(Replace `<project_slug>` with the repo's real slug.)

## What to try this week

- [ ] Save a decision with `remember` in a team project.
- [ ] Retrieve it the next day with `recall` (or `get_project_context`).
- [ ] Check that a teammate can see what you saved as `shared` and CANNOT see what you saved as `private`.
- [ ] Open the visual memory manager (`open_memory_manager` tool, available in Claude Desktop) and try editing/pinning/deleting something from there.
- [ ] Report to whoever administers the project anything that fails, looks off, or doesn't make sense.

## What NOT to save

Never save keys, tokens, passwords, or sensitive customer data in `brain`. It's working-context memory, not a secrets manager.
