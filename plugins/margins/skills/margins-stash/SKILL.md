---
name: margins-stash
description: |
  Publish a SINGLE markdown document to a Margins stash — a one-off, single-doc
  workspace for review. No repo, no workspace to pick, no folder binding. Runs the
  `margins stash` CLI command and returns a review URL. Use when the user wants to:
  - Quickly get one standalone markdown doc reviewed ("stash this", "/margins-stash", "put this doc on Margins for review")
  - Publish a draft, note, or spec that isn't part of a synced folder
  This is the single-doc counterpart to /margins (which pushes a whole folder). To
  read comments back on the stash, use /margins-read with the stash slug.
---

# /margins-stash — Stash one document to Margins

Publish **one** markdown document to a Margins **stash** (a one-off single-doc
workspace) so it can be reviewed. Use this for a quick standalone doc — no folder to
bind, no workspace to pick. Speak in plain language; the CLI is an implementation
detail.

## Preamble: resolve the margins binary

```bash
# Resolve the margins binary. Prefer a local build (likely newer than the
# published version) before falling back to npx.
_ROOT=$(git rev-parse --show-toplevel 2>/dev/null)
if [ -n "$_ROOT" ] && [ -f "$_ROOT/margins-cli/bin/margins.js" ]; then
  MARGINS="node $_ROOT/margins-cli/bin/margins.js"
elif command -v margins &>/dev/null; then
  MARGINS="margins"
elif [ -x "$HOME/.bun/bin/margins" ]; then
  MARGINS="$HOME/.bun/bin/margins"
else
  MARGINS="npx --yes margins-cli"
fi

# Project-scoped CLI config: if .margins/ exists at the git root, use it (isolates
# serverUrl + apiKey per-project so localhost dev and prod don't collide).
if [ -n "$_ROOT" ] && [ -d "$_ROOT/.margins" ]; then
  export MARGINS_CONFIG_DIR="$_ROOT/.margins"
fi
```

> `margins stash` needs a CLI version that includes the `stash` command. If
> `$MARGINS stash --help` reports an unknown command, the installed CLI predates it
> — ask the user to update (`npm i -g margins-cli`).

## 1. Get the document

Figure out which single document the user wants to stash:

- If they name or point at a markdown file, use that path as the `[file]` argument.
- If they paste or describe content, write it to a temp `.md` file (or pipe it to
  the command via stdin — see step 2).
- If it's ambiguous which file they mean, ask which one (don't guess across several
  `.md` files).

Optionally pass a **title** with `--title`. If you omit it, the CLI derives one from
the document's first `#` heading, else the file name. Keep an explicit title under
200 characters.

## 2. Publish to a stash

Run the command and parse the JSON result:

```bash
$MARGINS stash <file> --json            # or: cat <file> | $MARGINS stash --json
# with an explicit title:
$MARGINS stash <file> --title "<title>" --json
```

`--json` prints `{ "id", "slug", "url" }`. Report the URL plainly:

> Stashed for review — open it here: **`<url>`**

If the command errors on **authentication** ("Not authenticated" / "Run: margins
auth login"), ask the user to sign in first (`$MARGINS auth login`, or see
`/margins-setup`), then retry.

## 3. Offer the review loop

A stash is reviewed like any Margins doc. Offer to help the user work through any
comments. The stash has its own slug (the `slug` from step 2's JSON, shaped
`stash/<user>/<id>`); read its open discussions with:

```bash
$MARGINS discuss list <slug> --json
```

Then help address each comment, update the document (re-run `margins stash` is **not**
how you update — a stash is a fresh doc each time; for ongoing edits, open the review
URL or use the web UI), and resolve threads. `/margins-read` reads comments the same
way when given the stash slug.

## 4. Images

A stash carries **markdown only**. If the document references images, say so plainly:
the text is published, but to include images the user should push a folder with
`/margins` (full-fidelity CLI sync) or use the Margins desktop app. Never base64-encode
an image into the document.
