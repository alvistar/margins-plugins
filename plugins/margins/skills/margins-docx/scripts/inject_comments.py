#!/usr/bin/env python3
"""Inject Margins discussions into a .docx as native Word comments.

Run by the /margins-docx skill:

    uvx --with "python-docx>=1.2" python3 inject_comments.py --input spec.docx --output spec.reviewed.docx < payload.json

Stdin is JSON:

    {"comments": [
       {"anchor": "text quoted in the document",
        "body":   "alice: why only email?\\nbob: see 3.1",
        "before": "optional preceding context, e.g. the nearest heading",
        "after":  "optional following context",
        "id":     "abc123"}      # optional; carried into the body for traceability
    ]}

Stdout is a JSON summary:
    {"injected": N, "uncertain": N, "document_level": N, "stripped": N,
     "results": [{"id": ..., "placement": "injected|uncertain|document_level"}],
     "output": path}

Division of labour: the LLM decides WHICH text each discussion refers to (it has
the document, the thread, and judgement). This script does the injection, and is
deliberately unwilling to guess — an anchor it cannot place uniquely becomes a
flagged comment at the top of the document rather than a confident wrong
placement.

Three invariants hold no matter what the payload says:

  1. The document's own text AND its layout are never modified. Runs get
     partitioned so a comment can bracket a substring, but every text and
     non-text run child (a line break, tab, or inline image) survives exactly
     once — the rendered document is unchanged, not merely the concatenated text.
  2. Only comments this script authored are removed before re-injecting (matched
     on BOTH the author tag and a paired initials marker), so export is
     idempotent and a human reviewer's own Word comments survive — even a human
     whose display name happens to be "Margins Review".
  3. An anchor that matches zero places, or several with no context to break the
     tie — counting repeats inside a single paragraph — is flagged, never
     silently misplaced.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
import tempfile
import zipfile
from pathlib import Path

try:
    from docx import Document
    from docx.parts.comments import CommentsPart
    from docx.text.paragraph import Paragraph
    from docx.text.run import Run
except ImportError:  # pragma: no cover - the skill always supplies the dep
    print(
        "margins-docx: MISSING_DEPENDENCY: python-docx (>=1.2) is not installed. "
        'Run this script via: uvx --with "python-docx>=1.2" python3 inject_comments.py',
        file=sys.stderr,
    )
    raise SystemExit(1)

# A comment is "ours" (safe to strip on re-export) only when BOTH of these match.
# The author tag alone is not enough: a human whose Word display name is literally
# "Margins Review" would otherwise have their comments deleted. The initials act
# as a paired machine marker no human sets by accident.
MARGINS_AUTHOR = "Margins Review"
MARGINS_INITIALS = "MGN"

UNCERTAIN_PREFIX = "[position uncertain] "
DOC_LEVEL_PREFIX = "[document-level] "

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"

# Inline wrappers whose <w:r> children are part of the paragraph's own text flow.
# We descend through these so hyperlinked and tracked-change (w:ins) text is
# searchable and anchorable — but we do NOT descend into w:drawing / w:pict /
# text boxes, whose runs are separate content, not this paragraph's prose.
_RUN_WRAPPERS = frozenset(
    f"{W}{tag}" for tag in ("hyperlink", "ins", "smartTag", "sdt", "sdtContent")
)

# Comment fields that must be strings when present.
_STR_FIELDS = ("anchor", "before", "after", "body", "id")

# Characters XML 1.0 forbids even when escaped. Reviewer comment text is
# arbitrary and can carry them; lxml raises rather than escaping, so strip them
# before they ever reach add_comment.
_XML_ILLEGAL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")

# Word documents are full of typographic characters that an LLM quoting the
# rendered markdown will have flattened to ASCII. Fold both sides before matching.
FOLD = {
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"',
    "–": "-", "—": "-", "−": "-",
    " ": " ", " ": " ", " ": " ",
    "…": "...",
}


class InjectError(Exception):
    """A structural failure the skill should surface as plain guidance."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


# ── text model ─────────────────────────────────────────────────────────────
#
# A paragraph's "raw text" is its <w:t> content concatenated in document order —
# the same string Word shows and the same one `doc_text` asserts on. Run
# boundaries are invisible in it, which is exactly why anchoring needs a map back.


def run_text(r) -> str:
    return "".join(t.text or "" for t in r.findall(f"{W}t"))


def para_runs(p) -> list:
    """Runs in `p`'s text flow, in document order.

    Includes runs nested one or more levels inside inline wrappers (hyperlinks,
    tracked-change insertions, content controls) so their text is anchorable;
    stops at drawings/text boxes, whose runs are not this paragraph's prose.
    """
    out: list = []

    def walk(el):
        for child in el:
            if child.tag == f"{W}r":
                out.append(child)
            elif child.tag in _RUN_WRAPPERS:
                walk(child)

    walk(p)
    return out


def para_text(p) -> str:
    return "".join(run_text(r) for r in para_runs(p))


def fold(s: str) -> tuple[str, list[int]]:
    """Normalize for matching; return the folded text and folded→raw index map.

    Collapses whitespace runs and flattens smart punctuation, so a quote copied
    out of rendered markdown still finds its way home. The index map is what lets
    us translate a hit back into exact raw offsets — without it we could match but
    not anchor.
    """
    out: list[str] = []
    idx: list[int] = []
    prev_space = False
    for i, ch in enumerate(s):
        c = FOLD.get(ch, ch)
        if c.isspace():
            if prev_space:
                continue
            c, prev_space = " ", True
        else:
            prev_space = False
        # A fold may expand one char into several (…  -> ...); every output char
        # maps back to the same raw index, which keeps offsets monotonic.
        for sub in c:
            out.append(sub)
            idx.append(i)
    return "".join(out), idx


def find_spans(raw: str, needle: str) -> list[tuple[int, int]]:
    """Every non-overlapping occurrence of `needle` in `raw`, as RAW [start, end).

    Returns ALL matches — including repeats inside one paragraph — so the caller
    can judge global uniqueness. Returning only the first (or bailing on a repeat)
    would hide a paragraph from the ambiguity check and let a globally-ambiguous
    anchor land confidently on the wrong occurrence.
    """
    if not needle:
        return []
    folded, idx = fold(raw)
    fneedle, _ = fold(needle)
    fneedle = fneedle.strip()
    if not fneedle:
        return []
    spans: list[tuple[int, int]] = []
    at = folded.find(fneedle)
    while at >= 0:
        start = idx[at]
        end = idx[at + len(fneedle) - 1] + 1
        spans.append((start, end))
        at = folded.find(fneedle, at + len(fneedle))
    return spans


# ── run splitting ──────────────────────────────────────────────────────────


def split_run_at(r, k: int) -> None:
    """Split run `r` at text offset `k`, keeping every child exactly once.

    Word anchors a comment on whole runs, so a quote covering half a run is
    unanchorable until the run is partitioned. The split distributes each child
    to exactly one side: content wholly past the seam MOVES to the new right run,
    the straddling <w:t> is cut in two, and non-text children (<w:br>, <w:tab>,
    <w:cr>, <w:drawing>, …) go to whichever side their position falls on. The
    earlier implementation deep-copied the whole run and only edited <w:t>, which
    silently DUPLICATED every non-text child into both halves — a tripled inline
    image even collides its own docPr id and makes Word offer to repair the file.
    Formatting (rPr) is cloned to both halves; concatenated text is unchanged.
    """
    # The right half starts as a clone carrying only rPr (formatting), no content.
    right = copy.deepcopy(r)
    for child in list(right):
        if child.tag != f"{W}rPr":
            right.remove(child)
    r.addnext(right)

    offset = 0
    for child in list(r):
        if child.tag == f"{W}rPr":
            continue
        if child.tag == f"{W}t":
            text = child.text or ""
            length = len(text)
            if offset >= k:
                right.append(child)  # wholly right of the seam — move it
            elif offset + length <= k:
                pass  # wholly left of the seam — leave in place
            else:
                cut = k - offset
                tail = copy.deepcopy(child)
                tail.text = text[cut:]
                child.text = text[:cut]
                right.append(tail)
            offset += length
        else:
            # Non-text content occupies a position but no text width. Everything
            # at or past the seam belongs to the right half; move it there once.
            if offset >= k:
                right.append(child)

    # Empty <w:t> elements are legal but noisy; drop them, and preserve
    # significant whitespace on what remains.
    for run in (r, right):
        for t in run.findall(f"{W}t"):
            if not t.text:
                run.remove(t)
            else:
                t.set(XML_SPACE, "preserve")


def runs_covering(p, start: int, end: int) -> list:
    """Partition `p`'s runs so [start, end) is exactly a whole-run sequence."""
    # Split at the tail boundary first: splitting at `start` inserts a run and
    # would invalidate an `end` offset computed against the old run list.
    for boundary in (end, start):
        offset = 0
        for r in para_runs(p):
            length = len(run_text(r))
            if offset < boundary < offset + length:
                split_run_at(r, boundary - offset)
                break
            offset += length

    covered, offset = [], 0
    for r in para_runs(p):
        length = len(run_text(r))
        if length and start <= offset and offset + length <= end:
            covered.append(r)
        offset += length
    return covered


# ── stripping our own past output ──────────────────────────────────────────


def _is_ours(comment) -> bool:
    return (
        comment.get(f"{W}author") == MARGINS_AUTHOR
        and comment.get(f"{W}initials") == MARGINS_INITIALS
    )


def strip_margins_comments(doc) -> int:
    """Remove every comment this script authored, plus its anchors in the body.

    Ownership is the author tag AND the initials marker together (see _is_ours):
    comments by anyone else — including a human named "Margins Review" — are left
    strictly alone, since a reviewer's own Word comments are their data.
    """
    part = next(
        (p for p in doc.part.package.iter_parts() if isinstance(p, CommentsPart)), None
    )
    if part is None:
        return 0

    root = part.element
    ours = [c for c in root.findall(f"{W}comment") if _is_ours(c)]
    ids = {c.get(f"{W}id") for c in ours}
    for c in ours:
        root.remove(c)

    # Dangling range markers and reference runs would leave Word with comments
    # that point at nothing. Sweep the body for anything carrying a stripped id.
    body = doc.element.body
    for tag in ("commentRangeStart", "commentRangeEnd"):
        for el in list(body.iter(f"{W}{tag}")):
            if el.get(f"{W}id") in ids:
                el.getparent().remove(el)
    for ref in list(body.iter(f"{W}commentReference")):
        if ref.get(f"{W}id") in ids:
            run = ref.getparent()
            holder = run.getparent()
            # The reference lives in a run of its own; drop the whole run so no
            # empty <w:r> is left behind. It carries no text, so invariant #1 holds.
            (holder if run.tag == f"{W}r" else run).remove(run if run.tag == f"{W}r" else ref)

    return len(ours)


# ── anchoring ──────────────────────────────────────────────────────────────


def locate(paragraphs, item) -> tuple[object, int, int] | None:
    """Pick the one place this comment belongs, or None if genuinely unsure.

    Every occurrence across every paragraph is a candidate — including two
    occurrences inside the same paragraph. A single candidate wins outright;
    otherwise only the caller-supplied before/after context may break the tie,
    and only if it leaves exactly one survivor. Anything else is None, so the
    comment is flagged rather than dropped onto a coin-flip paragraph.
    """
    anchor = (item.get("anchor") or "").strip()
    if not anchor:
        return None

    candidates = []  # (paragraph_index, paragraph, start, end)
    for i, p in enumerate(paragraphs):
        for (s, e) in find_spans(para_text(p), anchor):
            candidates.append((i, p, s, e))

    if not candidates:
        return None
    if len(candidates) == 1:
        _, p, s, e = candidates[0]
        return p, s, e

    before, _ = fold(item.get("before") or "")
    after, _ = fold(item.get("after") or "")
    before, after = before.strip(), after.strip()
    if not before and not after:
        return None

    scored = []
    for i, p, s, e in candidates:
        # Context window: the preceding paragraph (usually the nearest heading)
        # plus whatever precedes/follows the anchor inside its own paragraph.
        raw = para_text(p)
        prev_text = para_text(paragraphs[i - 1]) if i > 0 else ""
        next_text = para_text(paragraphs[i + 1]) if i + 1 < len(paragraphs) else ""
        lead, _ = fold(prev_text + " " + raw[:s])
        trail, _ = fold(raw[e:] + " " + next_text)
        score = (1 if before and before in lead else 0) + (1 if after and after in trail else 0)
        if score:
            scored.append((score, p, s, e))

    if not scored:
        return None
    best = max(x[0] for x in scored)
    winners = [x for x in scored if x[0] == best]
    if len(winners) != 1:
        return None  # context did not actually break the tie
    _, p, s, e = winners[0]
    return p, s, e


def body_paragraphs(text: str, placement: str, item) -> list[str]:
    """Render the thread, prefixed with a note when it did not land on its quote.

    A discussion with no quoted text at all (a doc-level comment in Margins) is
    NOT a failure — it belongs at the top of the document and says so. Labelling
    it "uncertain" would cry wolf at the reviewer.
    """
    lines = (text or "").split("\n")
    if placement == "uncertain":
        anchor = (item.get("anchor") or "").strip()
        lines = [
            UNCERTAIN_PREFIX
            + f'could not locate this quote in the document: "{anchor[:120]}"',
            "",
        ] + lines
    elif placement == "document_level":
        lines = [
            DOC_LEVEL_PREFIX + "this discussion is not anchored to any specific text.",
            "",
        ] + lines
    ref = item.get("id")
    if ref:
        lines = lines + ["", f"(Margins discussion {ref})"]
    return lines


# ── main ───────────────────────────────────────────────────────────────────


def load_payload(stream) -> list[dict]:
    try:
        data = json.load(stream)
    except json.JSONDecodeError as err:
        raise InjectError("INVALID_JSON", f"stdin is not valid JSON: {err}") from err

    if not isinstance(data, dict) or "comments" not in data:
        raise InjectError(
            "INVALID_PAYLOAD", 'stdin JSON must be an object with a "comments" array.'
        )
    comments = data["comments"]
    if not isinstance(comments, list):
        raise InjectError("INVALID_PAYLOAD", '"comments" must be an array.')
    for c in comments:
        if not isinstance(c, dict):
            raise InjectError("INVALID_PAYLOAD", "each comment must be an object.")
        # A number/list where a string is expected would otherwise crash with a
        # raw AttributeError deep in matching; reject it as clean guidance here.
        for key in _STR_FIELDS:
            if c.get(key) is not None and not isinstance(c[key], str):
                raise InjectError(
                    "INVALID_PAYLOAD", f'comment field "{key}" must be a string.'
                )
            if isinstance(c.get(key), str):
                c[key] = _XML_ILLEGAL.sub("", c[key])
    return comments


def open_docx(path: Path):
    if not path.is_file():
        raise InjectError("INPUT_NOT_FOUND", f"no such file: {path}")
    if not zipfile.is_zipfile(path):
        raise InjectError(
            "NOT_A_DOCX", f"{path.name} is not a Word .docx (not a zip archive)."
        )
    # is_zipfile only checks the end-of-archive record; a truncated or corrupt
    # central directory still raises BadZipFile when the parts are enumerated.
    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
    except zipfile.BadZipFile as err:
        raise InjectError("NOT_A_DOCX", f"{path.name} is a corrupt zip archive.") from err
    if "word/document.xml" not in names:
        raise InjectError(
            "NOT_A_DOCX", f"{path.name} is a zip but has no word/document.xml."
        )
    try:
        return Document(str(path))
    except Exception as err:  # python-docx raises a grab-bag on malformed packages
        raise InjectError("NOT_A_DOCX", f"could not read {path.name}: {err}") from err


def _save_atomically(doc, output: Path) -> None:
    """Write to a temp file in the target dir, then os.replace onto `output`.

    A direct doc.save(output) truncates the destination before writing, so a
    failure mid-save destroys a previously-good export. Writing beside it and
    renaming means `output` is only ever replaced by a complete file.
    """
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(output.parent), prefix=".margins-docx-", suffix=".tmp")
    os.close(fd)
    try:
        doc.save(tmp)
        os.replace(tmp, str(output))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="inject_comments.py",
        description="Inject Margins discussions into a .docx as native Word comments.",
    )
    ap.add_argument("--input", "-i", required=True, type=Path, help="the original .docx")
    ap.add_argument("--output", "-o", required=True, type=Path, help="where to write the annotated copy")
    args = ap.parse_args(argv)

    comments = load_payload(sys.stdin)
    doc = open_docx(args.input)

    stripped = strip_margins_comments(doc)

    paragraphs = list(doc.element.body.iter(f"{W}p"))

    # `add_comment` takes python-docx Run proxies, not bare lxml elements, and
    # anchors on run boundaries — hence all the splitting above.
    def proxies(p_elm, run_elms):
        para = Paragraph(p_elm, doc)
        return [Run(r, para) for r in run_elms]

    # Fallback anchor for unplaceable comments: the document's first run of text.
    doc_start = next(
        ((p, r) for p in paragraphs for r in para_runs(p) if run_text(r)), None
    )

    injected = uncertain = document_level = 0
    results: list[dict] = []
    for item in comments:
        hit = locate(paragraphs, item)
        runs = []
        if hit:
            p, start, end = hit
            runs = proxies(p, runs_covering(p, start, end))

        if runs:
            placement = "injected"
        else:
            if doc_start is None:
                raise InjectError(
                    "EMPTY_DOCUMENT",
                    "the document has no text to anchor a comment to.",
                )
            p0, r0 = doc_start
            runs = proxies(p0, [r0])
            # No quote to begin with is a doc-level discussion; a quote we could
            # not place is a genuine miss. Only the latter is a warning.
            placement = (
                "document_level" if not (item.get("anchor") or "").strip() else "uncertain"
            )

        lines = body_paragraphs(item.get("body", ""), placement, item)
        comment = doc.add_comment(
            runs=runs,
            text=lines[0],
            author=MARGINS_AUTHOR,
            initials=MARGINS_INITIALS,
        )
        for line in lines[1:]:
            comment.add_paragraph(line)

        results.append({"id": item.get("id"), "placement": placement})
        if placement == "injected":
            injected += 1
        elif placement == "uncertain":
            uncertain += 1
        else:
            document_level += 1

    try:
        _save_atomically(doc, args.output)
    except OSError as err:
        raise InjectError("OUTPUT_WRITE_FAILED", f"could not write {args.output}: {err}") from err

    json.dump(
        {
            "injected": injected,
            "uncertain": uncertain,
            "document_level": document_level,
            "stripped": stripped,
            "results": results,
            "output": str(args.output),
        },
        sys.stdout,
    )
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except InjectError as err:
        print(f"margins-docx: {err.code}: {err.message}", file=sys.stderr)
        raise SystemExit(1)
