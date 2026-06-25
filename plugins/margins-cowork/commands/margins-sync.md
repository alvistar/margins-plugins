---
description: Publish the bound folder's markdown to Margins for review, reconciling any concurrent server edits (never clobbers). Tells you how to include images.
---

# Sync this folder to Margins

Publish the markdown in the user's current folder to its bound Margins workspace. Speak in
plain language — no Git/CLI jargon. Use the Margins connector tools.

## 1. Read the binding

Look for `.margins.json` in the folder.

- **If it exists**, read `workspaceId`, `workspaceSlug`, `lastSyncedHead`, and `contentOwner`.
  - **If `contentOwner` is `"desktop"`:** the Margins desktop app is already syncing this
    folder (markdown and images). Do **not** publish content here. Tell the user their desktop
    app handles syncing, and offer `/margins-review` to work through comments. Stop.
- **If it does not exist (first run):**
  1. Make sure the connector is signed in (call `list_workspaces`; if it errors on auth, ask
     the user to finish the one-time sign-in).
  2. Ask whether to **create a new workspace** (default the name to the folder's name) or
     **pick an existing one** (show `list_workspaces`). For a new one, call `create_workspace`.
  3. After the sync below succeeds, write `.margins.json` with `workspaceId`, `workspaceSlug`,
     `lastSyncedHead` (the returned `head`), and `contentOwner: "cowork"`.

## 2. Publish the markdown

1. Collect every `.md` file in the folder (recurse subfolders; skip hidden dirs and
   `node_modules`). Read each file's full text.
2. Call **`sync_workspace_markdown`** with `workspaceId`, `parentSha` = `lastSyncedHead`
   (omit/`null` on first run), and `files` = `[{ path, content }, …]` for every markdown file.
3. Read the result:
   - **Clean (`merged: false`, `conflicts: null`):** success. Update `lastSyncedHead` in
     `.margins.json` to the returned `head`. Report counts: "Published — N added, M changed,
     K deleted," and give the workspace `url`.
   - **Auto-merged (`merged: true`):** a reviewer or another session changed the workspace
     since your last sync, and Margins merged both cleanly. Update `lastSyncedHead` to the new
     `head`. Tell the user their changes were merged with another edit; nothing was lost.
   - **Conflict (`conflicts` is a non-empty list):** the push did **not** land. For each
     `{ path, reason }`, explain in plain language which file collides and why (e.g.
     "`notes.md` was edited both here and on Margins on the same lines"). Help the user
     reconcile: read the current Margins version with `get_document`, decide how to combine
     the two, update the local file, then run `/margins-sync` again. Do **not** discard either
     side.

## 3. Images

If the folder contains image files (`.png`, `.jpg`, `.jpeg`, `.gif`, `.webp`, `.svg`), after
publishing the text say plainly:

> Published your text. I found N image(s) — Cowork can't upload images to Margins (the sandbox
> blocks it). To include them, install the **Margins desktop app** (it syncs this folder's
> images automatically) or open this folder in **Claude Code**. Your markdown is live now.

Never try to base64-encode an image into a tool call.
