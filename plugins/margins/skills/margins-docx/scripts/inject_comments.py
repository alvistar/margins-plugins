#!/usr/bin/env python3
"""Inject Margins discussions into a .docx as native Word comments.

Run by the /margins-docx skill:

    uvx --with python-docx python3 inject_comments.py --input spec.docx --output spec.reviewed.docx < payload.json

Stdin is JSON:

    {"comments": [
       {"anchor": "text quoted in the document",
        "body":   "alice: why only email?\\nbob: see 3.1",
        "before": "optional preceding context, e.g. the nearest heading",
        "after":  "optional following context",
        "author": "alice",       # optional; only sets the Word bubble initials
        "id":     "abc123"}      # optional; carried into the body for traceability
    ]}

Stdout is a JSON summary: {"injected": N, "uncertain": N, "stripped": N, "output": path}

Division of labour: the LLM decides WHICH text each discussion refers to (it has
the document, the thread, and judgement). This script does the injection, and is
deliberately unwilling to guess — an anchor it cannot place uniquely becomes a
flagged comment at the top of the document rather than a confident wrong
placement.

Three invariants hold no matter what the payload says:

  1. The document's own text is never modified. Runs get partitioned so a comment
     can bracket a substring, but the concatenated text is byte-identical.
  2. Only comments authored by MARGINS_AUTHOR are removed before re-injecting, so
     export is idempotent and a human reviewer's own Word comments survive.
  3. An anchor that matches zero paragraphs, or several with no context to break
     the tie, is flagged — never silently misplaced.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
import zipfile
from pathlib import Path

try:
    from docx import Document
    from docx.parts.comments import CommentsPart
    from docx.text.paragraph import Paragraph
    from docx.text.run import Run
except ImportError:  # pragma: no cover - the skill always supplies the dep
    print(
        "margins-docx: MISSING_DEPENDENCY: python-docx is not installed. "
        'Run this script via: uvx --with python-docx python3 inject_comments.py',
        file=sys.stderr,
    )
    raise SystemExit(2)

# `w:author` on every comment we create. Strip-before-reinject keys on this
# string, so it is a contract with our own past output — not a display label.
MARGINS_AUTHOR = "Margins Review"
UNCERTAIN_PREFIX = "[position uncertain] "

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"

# Word documents are full of typographic characters that an LLM quoting the
# rendered markdown will have flattened to ASCII. Fold both sides before matching.
FOLD = {
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"',
    "–": "-", "—": "-", "−": "-",
    " ": " ", " ": " ", " ": " ",
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
    return p.findall(f"{W}r")


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


def find_span(raw: str, needle: str) -> tuple[int, int] | None:
    """Locate `needle` in `raw`, returning RAW [start, end) offsets, or None."""
    if not needle:
        return None
    folded, idx = fold(raw)
    fneedle, _ = fold(needle)
    fneedle = fneedle.strip()
    if not fneedle:
        return None
    at = folded.find(fneedle)
    if at < 0:
        return None
    if folded.find(fneedle, at + 1) >= 0:
        return None  # ambiguous WITHIN this paragraph — caller treats as no-hit
    start = idx[at]
    end = idx[at + len(fneedle) - 1] + 1
    return start, end


# ── run splitting ──────────────────────────────────────────────────────────


def split_run_at(r, k: int) -> None:
    """Split run `r` so that offset `k` (within the run's text) falls on a seam.

    Word anchors a comment on whole runs, so a quote covering half a run is
    unanchorable until the run is partitioned. The split is a pure re-shaping:
    formatting (rPr) is cloned to both halves and the concatenated text is
    unchanged, which is what keeps invariant #1 true.
    """
    left = r
    right = copy.deepcopy(r)
    r.addnext(right)

    offset = 0
    for lchild, rchild in zip(list(left), list(right)):
        if lchild.tag != f"{W}t":
            continue
        text = lchild.text or ""
        if offset + len(text) <= k:
            rchild.text = ""  # wholly left of the seam
        elif offset >= k:
            lchild.text = ""  # wholly right of it
        else:
            cut = k - offset
            lchild.text, rchild.text = text[:cut], text[cut:]
        offset += len(text)

    # Empty <w:t> elements are legal but noisy; drop them, and preserve
    # significant whitespace on what remains.
    for run in (left, right):
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


def strip_margins_comments(doc) -> int:
    """Remove every comment we authored, plus its anchors in the body.

    Comments by anyone else are left strictly alone — a reviewer's own Word
    comments are their data, not ours to garbage-collect.
    """
    part = next(
        (p for p in doc.part.package.iter_parts() if isinstance(p, CommentsPart)), None
    )
    if part is None:
        return 0

    root = part.element
    ours = [
        c for c in root.findall(f"{W}comment") if c.get(f"{W}author") == MARGINS_AUTHOR
    ]
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
    """Pick the one paragraph this comment belongs to, or None if unsure.

    Ambiguity is resolved ONLY by the caller-supplied context. Two paragraphs
    reading "Da definire in fase di analisi." are genuinely indistinguishable
    without it, and picking the first would be a coin flip dressed up as a result.
    """
    anchor = (item.get("anchor") or "").strip()
    if not anchor:
        return None

    hits = []
    for i, p in enumerate(paragraphs):
        span = find_span(para_text(p), anchor)
        if span:
            hits.append((i, p, span))

    if not hits:
        return None
    if len(hits) == 1:
        _, p, (s, e) = hits[0]
        return p, s, e

    before, _ = fold(item.get("before") or "")
    after, _ = fold(item.get("after") or "")
    before, after = before.strip(), after.strip()
    if not before and not after:
        return None

    scored = []
    for i, p, (s, e) in hits:
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
    best = max(s[0] for s in scored)
    winners = [x for x in scored if x[0] == best]
    if len(winners) != 1:
        return None  # context did not actually break the tie
    _, p, s, e = winners[0]
    return p, s, e


def body_paragraphs(text: str, uncertain: bool, item) -> list[str]:
    lines = [ln for ln in (text or "").split("\n")]
    if not lines:
        lines = [""]
    if uncertain:
        anchor = (item.get("anchor") or "").strip()
        note = UNCERTAIN_PREFIX + (
            f'could not locate this quote in the document: "{anchor[:120]}"'
            if anchor
            else "this comment had no usable quote."
        )
        lines = [note, ""] + lines
    ref = item.get("id")
    if ref:
        lines = lines + ["", f"(Margins discussion {ref})"]
    return lines


def initials_for(item) -> str:
    author = (item.get("author") or "").strip()
    if not author:
        return "MR"
    return "".join(w[0] for w in author.split()[:2]).upper() or "MR"


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
    return comments


def open_docx(path: Path):
    if not path.is_file():
        raise InjectError("INPUT_NOT_FOUND", f"no such file: {path}")
    if not zipfile.is_zipfile(path):
        raise InjectError(
            "NOT_A_DOCX", f"{path.name} is not a Word .docx (not a zip archive)."
        )
    with zipfile.ZipFile(path) as z:
        if "word/document.xml" not in z.namelist():
            raise InjectError(
                "NOT_A_DOCX", f"{path.name} is a zip but has no word/document.xml."
            )
    try:
        return Document(str(path))
    except Exception as err:  # python-docx raises a grab-bag on malformed packages
        raise InjectError("NOT_A_DOCX", f"could not read {path.name}: {err}") from err


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

    injected = uncertain = 0
    for item in comments:
        hit = locate(paragraphs, item)
        runs = []
        if hit:
            p, start, end = hit
            runs = proxies(p, runs_covering(p, start, end))

        if not runs:
            if doc_start is None:
                raise InjectError(
                    "EMPTY_DOCUMENT",
                    "the document has no text to anchor a comment to.",
                )
            p0, r0 = doc_start
            runs, is_uncertain = proxies(p0, [r0]), True
        else:
            is_uncertain = False

        lines = body_paragraphs(item.get("body", ""), is_uncertain, item)
        comment = doc.add_comment(
            runs=runs,
            text=lines[0],
            author=MARGINS_AUTHOR,
            initials=initials_for(item),
        )
        for line in lines[1:]:
            comment.add_paragraph(line)

        if is_uncertain:
            uncertain += 1
        else:
            injected += 1

    try:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        doc.save(str(args.output))
    except OSError as err:
        raise InjectError("OUTPUT_WRITE_FAILED", f"could not write {args.output}: {err}") from err

    json.dump(
        {
            "injected": injected,
            "uncertain": uncertain,
            "stripped": stripped,
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
