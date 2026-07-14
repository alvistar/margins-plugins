---
name: margins-docx
description: |
  Take a Microsoft Word (.docx) document through a Margins review and back again:
  convert it to markdown, publish it as a Margins stash with a share link, and later
  export the open discussions into a copy of the ORIGINAL .docx as native Word comments.
  Use when the user wants to:
  - Get a Word document reviewed on Margins ("review this Word doc", "/margins-docx", "put this docx up for comments")
  - Push an updated .docx to the same review it already has (a new version, same URL, comments intact)
  - Send review comments back to someone who works in Word ("export the comments to Word", "give me the docx with the comments in it")
  Requires `uv` (for the converter) on top of the usual Node + margins CLI. To read
  discussions without exporting them, use /margins-read; for a markdown doc that was
  never a Word file, use /margins-stash.
---

# /margins-docx — Review a Word document on Margins

Two loops, one document:

- **In:** `.docx` → markdown → a Margins stash with a share link. Re-run it after
  editing the Word file and the SAME stash gets a new version — reviewers' comments
  survive and re-anchor.
- **Out:** open Margins discussions → native Word comments in a copy of the
  **original** `.docx`, so the person who lives in Word gets back a Word file.

The original `.docx` is never modified and never leaves the user's disk. Speak plain
language; the CLI and the converter are implementation details.

## Preamble: resolve the tools

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

# The Word toolchain runs entirely through uvx — nothing to install but uv itself.
command -v uvx &>/dev/null || echo "MISSING_UVX"
```

If that prints `MISSING_UVX`, stop and tell the user:

> "This needs `uv` to read Word files. Install it with `brew install uv` (or see
> https://docs.astral.sh/uv/getting-started/installation/), then ask me again."

> Updating an existing stash in place needs **margins-cli 0.12.0 or newer** — that is
> the version that remembers which stash a file belongs to. If `$MARGINS stash --help`
> reports an unknown command, or the update path errors saying the server has no stash
> updates, ask the user to update (`npm i -g margins-cli`).

---

# Part 1 — Get the Word document reviewed

## 1. Find the document

Take the `.docx` the user pointed at. If several are in play and it's ambiguous, ask
which one — don't guess.

**Only `.docx` works.** A legacy `.doc` (Word 97-2003) is a different, binary format
and the converter cannot read it:

> "That's an older `.doc` file. Open it in Word and 'Save As' → `.docx`, then I can
> put it up for review."

## 2. Convert it to markdown

Convert **next to the original**, keeping the same base name:

```bash
DOCX="path/to/spec.docx"
MD="${DOCX%.docx}.md"          # spec.docx -> spec.md, same directory

uvx --from "markitdown[docx]" markitdown "$DOCX" > "$MD"
```

The `[docx]` extra is **required** — a bare `uvx markitdown` fails on Word files. Check
the exit code: if the conversion fails or the `.md` comes out empty/garbled, stop and
tell the user (see the table below) — never publish an empty or mangled document.

Keep that `.md` path stable across runs. The CLI keys a stash to the **file path** it
published, so `spec.md` in the same place is what makes step 3 update the existing
review instead of forking a new one.

If the conversion fails, translate the failure rather than pasting the traceback:

| What happened | What to tell the user |
|---|---|
| File is password-protected / encrypted | "Word is refusing to open this without a password. Remove the protection in Word (Review → Protect → unprotect), save, and try again." |
| File is corrupt, or not really a `.docx` | "This file isn't a readable Word document — it may be corrupt or renamed from another format. Try re-saving it from Word." |
| Empty output | "That document converted to an empty file — is there any text in it?" |

Skim the markdown before publishing. If it came out mangled or empty, say so and stop;
publishing a garbled document wastes the reviewers' time.

## 3. Publish it to Margins

```bash
$MARGINS stash "$MD" --share --json
```

This prints `{ "id", "slug", "url", "action", "shareUrl" }`. **Always re-print the
share URL from this output**, every run — never from memory, and never a URL you
constructed yourself.

Read `action` and say what actually happened:

| `action` | What it means | What to say |
|---|---|---|
| `created` | First publish, or a deliberate fork | "Published for review — share this: `<shareUrl>`" |
| `updated` | Same stash, new version. Existing comments survive and re-anchor | "Updated the review with your latest version — same link: `<shareUrl>`" |
| `unchanged` | The text is identical to what's already up there | "That's already the current version — nothing to push. `<shareUrl>`" |

### The branches you must not paper over

**A binding this machine didn't create.** The CLI refuses to overwrite a stash whose
binding it didn't record, and errors with a "re-run with `--yes` … or `--new`" message.
That guard exists because a binding file committed into a shared repo could otherwise
redirect this push onto someone else's document.

**Never pass `--yes` on your own initiative.** Surface the choice and let the user
decide:

> "This file is already bound to an existing Margins stash (`<slug>`), but that binding
> wasn't created on this machine. Do you want to **update that stash**, or **start a
> fresh one**?"

Then re-run with `--yes` (update) or `--new` (fork) according to their answer. This is a
**separate decision** from the original request — "review this doc" is not consent to
overwrite a stranger's stash, so ask even if the user already said "just publish it."

**The stash was swept or deleted.** The CLI says so and creates a fresh one. The slug
changes, so **the share URL changes too** — re-print the new one and tell the user the
old link is dead.

**Comment-only access.** If the CLI reports you only have comment access to that stash,
you cannot update it. Offer `--new` to publish your own copy, and explain that comments
on the original stay where they are.

**A 409 conflict.** If the CLI reports a conflict on `REVERT_UNSUPPORTED`, the user
reverted the Word file back to the exact text of an earlier published version. Margins
can't record that as a new version. Explain it plainly and offer to make any small edit,
or `--new` for a fresh stash. (Re-publishing text identical to the *current* version is
not this case — that comes back as `unchanged`.)

## 4. Hand over the review

Give the user the share URL and offer the loop: `/margins-read` (with the stash slug)
shows what's come in, and Part 2 below sends it back to Word.

## What survives the trip into Margins

Say this up front, so nobody is surprised later:

- **Kept:** text, headings, lists, tables, bold/italic.
- **Images become placeholders.** The markdown carries `![](data:image/png;base64...)`
  where a picture was — the picture itself does not make the trip. Reviewers see that
  something was there, not what it was.
- **Existing Word comments and tracked changes are dropped** on the way in. If the
  document already has comments in it that matter, they need to be resolved in Word
  first — Margins will not import them.

---

# Part 2 — Export the discussions back into Word

The user gets `spec.reviewed.docx`: a byte-for-byte copy of their original document
with the Margins threads added as **native Word comments**. Their text is untouched.

## 1. Fetch the open discussions

The stash slug is the `slug` from Part 1 (shaped `stash/<user>/<id>`):

```bash
$MARGINS discuss list <slug> --status open --json
```

Each discussion carries:

| Field | Use |
|---|---|
| `anchorSelectedText` | The text the reviewer highlighted — **this is the quote to find in the Word file** |
| `anchorHeadingText` | The nearest heading — pass as `before` to disambiguate a repeated quote |
| `body`, `authorName` | The opening message |
| `replies[]` | The rest of the thread (`body` + `authorName` each) |
| `id` | Carried into the Word comment, and echoed back per-comment in the summary |

## 2. Match each quote to the original document

**You** do the matching — that's the part that needs judgement. The script that follows
does the injection and deliberately refuses to guess.

- **Copy `anchorSelectedText` into `anchor` verbatim.** Don't retype it, shorten it, or
  "fix" a typo or accent — the script folds punctuation and whitespace for you, so an
  exact copy is what carries the match. A cleaned-up quote either misses (safe: flagged)
  or, worse, coincidentally matches different text elsewhere.
- Match **plain text against plain text.** The quote is rendered text as the reviewer
  saw it; the Word document's text is plain too. Never try to match markdown syntax
  (`## Heading`, `**bold**`) against the document — those characters are not in it.
- A discussion with **no** `anchorSelectedText` is a comment on the document as a whole.
  Leave its `anchor` out; it lands at the top of the document, labelled as document-level.
- **Disambiguate a repeated quote with context.** If the same quote could match more than
  one place, pass `before` (usually `anchorHeadingText`) and/or `after` — short snippets
  of the text immediately preceding/following the quote. The script only compares them
  against the quote's own paragraph and its immediate neighbours, so pick context that
  sits right next to the quote, not a heading several paragraphs up. Without enough
  context to leave exactly one match, the script flags the comment rather than guessing.
- If you cannot confidently place a quote, **pass it anyway with its anchor** and let the
  script flag it. A comment parked at the top of the document saying "position uncertain"
  is honest; a comment confidently attached to the wrong sentence is a bug the user will
  only find by reading everything.

Build the payload — one entry per discussion, thread rendered into `body`:

```json
{"comments": [
  {"id": "abc123",
   "anchor": "Accesso all'app tramite dati biometrici",
   "before": "Onboarding e accesso",
   "after": "Il pagamento avviene",
   "body": "alice: Face ID obbligatorio o opzionale?\nbob: Opzionale — la passkey resta l'alternativa."}
]}
```

`before`/`after` are optional — include them only to break a tie. Render the whole thread
into `body`, one message per line, each prefixed with its author — the opening message
first, then the replies in order. Every comment is authored "Margins Review" in Word (the
per-message authors live in the body text); it becomes one Word comment per discussion,
not one per message.

## 3. Inject them into a copy of the original

```bash
uvx --with "python-docx>=1.2" python3 \
  "${CLAUDE_PLUGIN_ROOT}/skills/margins-docx/scripts/inject_comments.py" \
  --input "$DOCX" \
  --output "${DOCX%.docx}.reviewed.docx" \
  < payload.json
```

Pin `python-docx>=1.2` as shown — the comment-anchoring API the script relies on landed
in 1.2. Use `${CLAUDE_PLUGIN_ROOT}` — never a path relative to the working directory. If
it is unset, the export cannot find its script; tell the user their Claude Code host isn't
providing `CLAUDE_PLUGIN_ROOT` rather than guessing at a path.

The script prints a summary:

```json
{"injected": 7, "uncertain": 1, "document_level": 2, "stripped": 0,
 "results": [{"id": "abc123", "placement": "uncertain"}, {"id": "def456", "placement": "injected"}],
 "output": "spec.reviewed.docx"}
```

- `injected` — landed on the quoted text.
- `uncertain` — had a quote the script couldn't place; parked at the top of the document
  with a "position uncertain" note. **Read `results[]` and tell the user exactly which
  discussion ids are uncertain** (`placement == "uncertain"`), so they can place those by
  hand — the counts alone don't say which.
- `document_level` — discussions that were never anchored to specific text.
- `stripped` — Margins comments from a previous export that were removed before
  re-injecting. This is why exporting twice doesn't duplicate anything.

**Sanity-check before handing back:** `injected + uncertain + document_level` should equal
the number of discussions you put in the payload. If it doesn't, you dropped or duplicated
an entry building the payload — fix it and re-run rather than reporting a wrong count.

On a non-zero exit the script prints one line shaped `margins-docx: CODE: message`.
Translate the code into a plain sentence — don't paste the raw line:

| Code | What it means → what to say |
|---|---|
| `INPUT_NOT_FOUND` | The `.docx` path is wrong. Confirm the file with the user. |
| `NOT_A_DOCX` | The file isn't a readable Word `.docx` (corrupt, or renamed from another format). Ask them to re-save it from Word. |
| `INVALID_JSON` / `INVALID_PAYLOAD` | **Your** payload was malformed — a bug on your side. Rebuild it from the discussions and retry; don't blame the user's file. |
| `MISSING_DEPENDENCY` | `python-docx` wasn't available — check you invoked the command with `uvx --with "python-docx>=1.2"`, not the bare interpreter. |
| `OUTPUT_WRITE_FAILED` | Couldn't write the output (permissions/disk). Try a different output path. |

## 4. Hand back the file

> "Here's `spec.reviewed.docx` — your original document with 7 Margins comments added.
> Your text is untouched. One comment (discussion `abc123`) couldn't be placed and is
> sitting at the top of the document flagged 'position uncertain'."

## What export does and doesn't do

- **The original is never overwritten.** Output is always a separate `.reviewed.docx`.
- **The document's own text is never edited** — comments are added, nothing else.
- **Existing Word comments by real people are preserved.** Only the script's own previous
  exports are cleared before re-injecting (matched on the author tag *and* a private
  marker, so even a human whose display name is "Margins Review" keeps their comments).
- **Exporting is safe to repeat.** Re-running replaces our comments rather than stacking
  new copies on top, and the output is written atomically — a failed re-export never
  destroys the previous good one.
- **Word is a dead end for comment state.** Replying to or resolving these comments *in
  Word* does not flow back to Margins. Margins stays the source of truth for the
  discussion; the Word file is a snapshot for people who don't live in Margins.
- **Known limits (all fail safe — flagged, never silently wrong):** a quote that lives
  only in a header, footer, footnote, or text box can't be anchored and comes back
  `uncertain`. Re-exporting a document whose Margins comments were manually resolved in
  Word can leave a stale entry in Word's comment-state metadata; it's harmless (Word
  ignores it), and since comment state doesn't round-trip anyway, don't rely on it.
