#!/usr/bin/env node
// Versioning-hygiene lint for the margins-plugins marketplace.
//
// Enforces, with no external dependencies:
//   - .claude-plugin/marketplace.json is present and valid JSON
//   - no `plugins[]` entry carries a `version` field
//       (each plugin's own plugin.json is the version authority; a plugin.json
//        version silently overrides a marketplace entry, so setting both is a trap)
//   - every entry's `source` resolves to plugins/<source>/.claude-plugin/plugin.json
//
// Run directly to lint this repo (exits non-zero on any violation); or import
// `checkMarketplace(repoRoot)` for testing.

import { readFileSync, existsSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

/**
 * @param {string} repoRoot  path to the marketplace repo root
 * @returns {{ ok: boolean, errors: string[] }}
 */
export function checkMarketplace(repoRoot) {
  const manifestPath = join(repoRoot, ".claude-plugin", "marketplace.json");
  if (!existsSync(manifestPath)) {
    return { ok: false, errors: [`marketplace.json not found at ${manifestPath}`] };
  }

  let manifest;
  try {
    manifest = JSON.parse(readFileSync(manifestPath, "utf8"));
  } catch (err) {
    return { ok: false, errors: [`marketplace.json is not valid JSON: ${err.message}`] };
  }

  const plugins = manifest.plugins;
  if (!Array.isArray(plugins)) {
    return { ok: false, errors: ["marketplace.json has no `plugins` array"] };
  }

  const pluginRoot = manifest.metadata?.pluginRoot ?? ".";
  const errors = [];

  for (const entry of plugins) {
    const name = entry?.name ?? "(unnamed)";

    if (entry && Object.prototype.hasOwnProperty.call(entry, "version")) {
      errors.push(
        `plugin "${name}": remove the \`version\` field — plugin.json is the version ` +
          `authority (a plugin.json version silently overrides the marketplace entry).`,
      );
    }

    const source = entry?.source;
    if (typeof source === "string") {
      const pluginJson = join(repoRoot, pluginRoot, source, ".claude-plugin", "plugin.json");
      if (!existsSync(pluginJson)) {
        errors.push(`plugin "${name}": source "${source}" has no plugin.json at ${pluginJson}`);
      }
    }
  }

  return { ok: errors.length === 0, errors };
}

// CLI entrypoint
if (process.argv[1] && import.meta.url === `file://${process.argv[1]}`) {
  const repoRoot = process.argv[2] ?? join(dirname(fileURLToPath(import.meta.url)), "..");
  const { ok, errors } = checkMarketplace(repoRoot);
  if (!ok) {
    console.error("marketplace.json check FAILED:");
    for (const e of errors) console.error("  - " + e);
    process.exit(1);
  }
  console.log("marketplace.json check passed.");
}
