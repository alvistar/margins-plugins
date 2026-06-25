---
description: Set up Margins in Cowork — sign in once and bind a folder to a Margins workspace for review.
---

# Set up Margins

Help the user get started with Margins in Cowork. Keep it to plain language — they may not
use Git or a terminal.

1. **Connector sign-in.** The Margins connector ships with this plugin. If the Margins MCP
   tools (e.g. `list_workspaces`) aren't authorized yet, tell the user to complete the
   one-time OAuth sign-in when Cowork prompts (it opens a Margins/Keycloak login). They never
   enter a Client ID or secret.

2. **Confirm it works.** Call `list_workspaces`. If it returns (even an empty list), the
   connector is live. If it errors with an auth message, the sign-in hasn't completed — ask
   them to finish it.

3. **Bind a folder.** Ask which connected folder holds the markdown they want reviewed. Then
   tell them to run **`/margins-sync`** in that folder — on first run it will create a new
   Margins workspace (named after the folder) or let them pick an existing one, and remember
   the choice in a `.margins.json` file so they never have to choose again.

4. **Set expectations on images.** Mention up front: Cowork publishes **markdown**; for
   images they'll install the Margins desktop app or use Claude Code (`/margins-sync` explains
   this when it sees images).
