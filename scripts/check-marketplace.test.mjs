import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { checkMarketplace } from "./check-marketplace.mjs";

// Build a throwaway marketplace repo. `manifest` may be an object (serialized)
// or a raw string (to test malformed JSON). `pluginDirs` get a real plugin.json.
function makeRepo(manifest, pluginDirs = []) {
  const root = mkdtempSync(join(tmpdir(), "mp-test-"));
  mkdirSync(join(root, ".claude-plugin"), { recursive: true });
  writeFileSync(
    join(root, ".claude-plugin", "marketplace.json"),
    typeof manifest === "string" ? manifest : JSON.stringify(manifest),
  );
  for (const p of pluginDirs) {
    mkdirSync(join(root, "plugins", p, ".claude-plugin"), { recursive: true });
    writeFileSync(
      join(root, "plugins", p, ".claude-plugin", "plugin.json"),
      JSON.stringify({ name: p, version: "0.0.0" }),
    );
  }
  return root;
}

const manifest = (plugins) => ({
  name: "margins-plugins",
  metadata: { pluginRoot: "./plugins" },
  plugins,
});

function withRepo(root, fn) {
  try {
    fn();
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
}

test("happy path: clean manifest with existing plugin dirs passes", () => {
  const root = makeRepo(
    manifest([
      { name: "margins", source: "margins" },
      { name: "margins-cowork", source: "margins-cowork" },
    ]),
    ["margins", "margins-cowork"],
  );
  withRepo(root, () => {
    const { ok, errors } = checkMarketplace(root);
    assert.equal(ok, true, errors.join("; "));
    assert.deepEqual(errors, []);
  });
});

test("error path: an entry with a version field fails and names the entry", () => {
  const root = makeRepo(
    manifest([{ name: "margins", source: "margins", version: "1.2.3" }]),
    ["margins"],
  );
  withRepo(root, () => {
    const { ok, errors } = checkMarketplace(root);
    assert.equal(ok, false);
    assert.match(errors.join("\n"), /"margins".*version/i);
  });
});

test("edge: a dangling source (missing plugin dir) fails", () => {
  const root = makeRepo(manifest([{ name: "margins", source: "margins" }]), []);
  withRepo(root, () => {
    const { ok, errors } = checkMarketplace(root);
    assert.equal(ok, false);
    assert.match(errors.join("\n"), /no plugin\.json/i);
  });
});

test("error path: malformed JSON fails gracefully (no crash)", () => {
  const root = makeRepo("{ not valid json", []);
  withRepo(root, () => {
    const { ok, errors } = checkMarketplace(root);
    assert.equal(ok, false);
    assert.match(errors.join("\n"), /not valid json/i);
  });
});
