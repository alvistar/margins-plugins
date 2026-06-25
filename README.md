# Margins plugins for Claude

One marketplace, two plugins for [Margins](https://margins.thealvistar.com) — the
git-native document review platform. Pick the plugin that matches where you run Claude:

| Plugin | For | Transport | Fidelity |
|--------|-----|-----------|----------|
| **`margins`** | **Claude Code** (terminal, IDE, desktop) | the `margins` npm CLI | full — markdown **and** images |
| **`margins-cowork`** | **Claude Cowork / web** | bundled MCP connector | markdown only |

Both talk to the same Margins backend. You normally install **one** — the one for the
runtime you use. Installing both is fine; their commands/skills are plugin-namespaced.

## Install

Add this marketplace, then install the plugin for your runtime.

1. **Add the marketplace by its GitHub repo** (not a raw file URL):
   ```
   /plugin marketplace add alvistar/margins-plugins
   ```
   > Add it by repo (the GitHub form), not by linking the raw `marketplace.json`. The
   > plugins are vendored in this repo via local paths, which only resolve through a
   > git-based add.

2. **Install the plugin for your runtime:**
   - **Claude Code →** install **`margins`**. It drives the platform through the
     `margins` CLI, so install that too: `npm i -g margins` (full-fidelity sync,
     images included).
   - **Claude Cowork / web →** install **`margins-cowork`**. No Git, npm, or CLI —
     just complete the **one-time OAuth sign-in** when prompted. Markdown only (see
     [Markdown only](#markdown-only-in-cowork) below).

3. **Choose an install scope.** Install **personal** (user scope) so the plugin
   follows you across every workspace; choose **workspace** scope only if you want it
   limited to one project.

## Updating

**Claude Code** picks up new versions on its normal update cadence.

**Cowork does _not_ auto-update.** It shows the **installed** version and keeps it
until you refresh the marketplace. To get the latest:

> **Remove the marketplace and re-add it** (Settings → Plugins), then reinstall the
> plugin. A plain refresh often won't bump a cached install.

Each plugin versions independently — its own `plugin.json` is the source of truth, and
the marketplace listing carries no version (a CI check enforces this).

## Markdown only in Cowork

`margins-cowork` publishes **markdown** but **not images** — Cowork's sandbox can't
upload binary bytes. To include images, use **Claude Code** with the `margins` plugin
(full-fidelity) or the Margins desktop app.

## Layout

```
plugins/
  margins/          # Claude Code plugin (skills, CLI transport)
  margins-cowork/   # Cowork/web plugin (commands + bundled MCP connector)
```

The `margins` plugin was previously distributed from the `margins-cli` repo and
`margins-cowork` from its own repo; both now live here under one marketplace.
