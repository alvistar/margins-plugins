---
description: Publish a single markdown document to a Margins stash for review — one doc, no folder binding, no pre-existing workspace. Returns a review link.
---

# Stash a document to Margins

Publish **one** markdown document to a Margins **stash** (a one-off single-doc workspace) so
it can be reviewed. Use this for a quick standalone doc — no folder to bind, no workspace to
pick. Speak in plain language — no Git/CLI jargon. Use the Margins connector tools.

## 1. Get the document

Figure out which single document the user wants to stash:

- If they name or point at a markdown file in the current folder, read its full text.
- If they paste or describe content, use that.
- If it's ambiguous which file they mean, ask which one (don't guess across several `.md` files).

Optionally derive a **title** — the document's first `#` heading, or the file name. Keep it
under 200 characters (the connector rejects longer titles). It's fine to omit the title; the
stash will be named "Untitled stash doc".

## 2. Publish to a stash

Call **`create_stash_doc`** with:

- `content` — the document's full markdown text
- `title` — optional (from step 1)

Read the result and report it plainly, using the returned `name` to confirm the stored title:

> Published **"`<name>`"** to a Margins stash — open it here: **`<docUrl>`**

The response also includes `workspaceId` and `slug`; keep the `workspaceId` in mind for step 3.

If the connector isn't signed in yet (the call errors on auth), ask the user to complete the
one-time sign-in first (see `/margins-setup`), then retry.

## 3. Offer the review loop

A stash is reviewed like any Margins doc. Offer to help the user work through any comments:
list the open discussions for the stash's `workspaceId`, help address each, update the
document, and resolve the threads — the same flow as `/margins-review`, just scoped to this
stash by its `workspaceId` (no `.margins.json` binding needed).

## 4. Images

A stash carries **markdown only** — same as folder sync. If the document references images,
say so plainly: the text is published, but to include the images the user should use the
**Margins desktop app** or **Claude Code**'s full-fidelity path. Never base64-encode an image
into a tool call.
