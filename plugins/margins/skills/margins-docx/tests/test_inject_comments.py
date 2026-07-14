"""Fixture suite for `inject_comments.py`.

Assertions read the output .docx as a ZIP and inspect `word/comments.xml` and
`word/document.xml` directly, rather than asking python-docx to read back its
own writes — a library that round-trips its own bug still round-trips it. Word
is the real consumer, and Word reads the XML.

Run:
    uvx --with python-docx --with pytest pytest plugins/margins/skills/margins-docx/tests
"""

import json
import re
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

TESTS = Path(__file__).parent
SCRIPT = TESTS.parent / "scripts" / "inject_comments.py"
SAMPLE = TESTS / "fixtures" / "sample.docx"

# The tag that makes a comment ours. Strip-before-reinject keys on it, so it is
# a contract, not a cosmetic label.
MARGINS_AUTHOR = "Margins Review"

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


# ── helpers ────────────────────────────────────────────────────────────────


def run_injector(inp, out, payload, *, expect_ok=True):
    """Invoke the script the way the skill does: JSON on stdin, paths as args."""
    stdin = payload if isinstance(payload, str) else json.dumps(payload)
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--input", str(inp), "--output", str(out)],
        input=stdin,
        capture_output=True,
        text=True,
    )
    if expect_ok:
        assert proc.returncode == 0, f"injector failed: {proc.stderr}"
    return proc


def part(docx_path, name):
    with zipfile.ZipFile(docx_path) as z:
        try:
            return z.read(name).decode("utf-8")
        except KeyError:
            return ""


def comments(docx_path):
    """[(author, text)] for every comment in the output, in document order."""
    xml = part(docx_path, "word/comments.xml")
    if not xml:
        return []
    out = []
    for block in re.findall(r"<w:comment\b.*?</w:comment>", xml, re.S):
        author = re.search(r'w:author="([^"]*)"', block)
        text = "".join(re.findall(r"<w:t[^>]*>([^<]*)</w:t>", block))
        out.append((author.group(1) if author else "", text))
    return out


def margins_comments(docx_path):
    return [(a, t) for a, t in comments(docx_path) if a == MARGINS_AUTHOR]


def doc_text(docx_path):
    """Concatenated body text — the invariant the injector must never disturb."""
    xml = part(docx_path, "word/document.xml")
    return "".join(re.findall(r"<w:t[^>]*>([^<]*)</w:t>", xml))


def anchored_text(docx_path, comment_id):
    """The body text bracketed by commentRangeStart/End for `comment_id`."""
    xml = part(docx_path, "word/document.xml")
    start = re.search(rf'<w:commentRangeStart[^>]*w:id="{comment_id}"[^>]*/>', xml)
    end = re.search(rf'<w:commentRangeEnd[^>]*w:id="{comment_id}"[^>]*/>', xml)
    if not start or not end:
        return None
    return "".join(re.findall(r"<w:t[^>]*>([^<]*)</w:t>", xml[start.end() : end.start()]))


def margins_ids(docx_path):
    """Comment ids whose author is the Margins tag."""
    xml = part(docx_path, "word/comments.xml")
    return [
        m.group(1)
        for m in re.finditer(r'<w:comment\b[^>]*w:id="([^"]*)"[^>]*w:author="([^"]*)"', xml)
        if m.group(2) == MARGINS_AUTHOR
    ]


def comment(anchor, body, **kw):
    return {"anchor": anchor, "body": body, "author": kw.pop("author", "alice"), **kw}


@pytest.fixture
def out(tmp_path):
    return tmp_path / "out.docx"


# ── happy paths ────────────────────────────────────────────────────────────


def test_happy_exact_anchor_lands_on_quoted_text_with_margins_author(out):
    anchor = "Creazione utenza in modo semplice e veloce"
    run_injector(
        SAMPLE, out, {"comments": [comment(anchor, "Confermare con il cliente.")]}
    )

    ours = margins_comments(out)
    assert len(ours) == 1
    author, text = ours[0]
    assert author == MARGINS_AUTHOR
    assert "Confermare con il cliente." in text

    # The comment must bracket the quoted text — not merely exist somewhere.
    cid = margins_ids(out)[0]
    assert anchored_text(out, cid) == anchor


def test_happy_multi_message_thread_is_one_comment_with_per_message_authors(out):
    body = "alice: Perche solo email?\nbob: Anche Apple/Google, vedi 3.1."
    run_injector(
        SAMPLE,
        out,
        {"comments": [comment("Creazione utenza", body, author="alice")]},
    )

    ours = margins_comments(out)
    assert len(ours) == 1, "a thread is ONE Word comment, not one per message"
    text = ours[0][1]
    assert "alice:" in text and "bob:" in text
    assert "Perche solo email?" in text and "Anche Apple/Google" in text


def test_happy_two_comments_on_different_paragraphs_both_anchor(out):
    a1 = "Creazione utenza in modo semplice"
    a2 = "Il borsellino potra essere ricaricato"
    run_injector(
        SAMPLE,
        out,
        {"comments": [comment(a1, "Primo commento."), comment(a2, "Secondo commento.")]},
    )

    ids = margins_ids(out)
    assert len(ids) == 2
    landed = {anchored_text(out, cid) for cid in ids}
    assert landed == {a1, a2}


# ── edges ──────────────────────────────────────────────────────────────────


def test_edge_anchor_split_across_runs_mid_word_still_anchors(out):
    # The fixture stores this as three runs, split mid-word: "L'accesso bio" +
    # "metrico deve " + "essere opzionale." The anchor crosses both seams, so it
    # can only land if the injector partitions the runs.
    anchor = "biometrico deve essere"
    run_injector(SAMPLE, out, {"comments": [comment(anchor, "Rendere opzionale.")]})

    ids = margins_ids(out)
    assert len(ids) == 1
    assert anchored_text(out, ids[0]) == anchor, "run-splitting did not isolate the anchor"


def test_edge_empty_comment_list_yields_valid_copy_and_strips_stale_margins_comments(out, tmp_path):
    # First pass plants a Margins comment; second pass (empty list) must remove it.
    once = tmp_path / "once.docx"
    run_injector(SAMPLE, once, {"comments": [comment("Creazione utenza", "Stale.")]})
    assert len(margins_comments(once)) == 1

    run_injector(once, out, {"comments": []})
    assert margins_comments(out) == []
    assert zipfile.is_zipfile(out)
    # Stripping ours must not disturb the document's own text.
    assert doc_text(out) == doc_text(SAMPLE)


def test_edge_idempotent_reinjection_does_not_duplicate(out, tmp_path):
    payload = {
        "comments": [
            comment("Creazione utenza in modo semplice", "Primo."),
            comment("biometrico deve essere", "Secondo."),
        ]
    }
    first = tmp_path / "first.docx"
    run_injector(SAMPLE, first, payload)
    run_injector(first, out, payload)

    assert len(margins_comments(first)) == 2
    assert sorted(margins_comments(out)) == sorted(margins_comments(first)), (
        "re-injecting the same threads must yield the same comment set, not duplicates"
    )


def test_edge_human_authored_comments_survive_stripping(out):
    # The fixture ships a real Word comment by Pietro. Strip-before-reinject
    # targets OUR author tag only — blowing away a reviewer's own comments would
    # be data loss.
    run_injector(SAMPLE, out, {"comments": [comment("Creazione utenza", "Nostro.")]})

    authors = {a for a, _ in comments(out)}
    assert "Pietro Mosconi" in authors, "human comment was destroyed by the strip pass"
    assert MARGINS_AUTHOR in authors


def test_edge_document_text_is_byte_identical_before_and_after(out):
    # R6's floor: we annotate the document, we never edit it.
    before = doc_text(SAMPLE)
    run_injector(
        SAMPLE,
        out,
        {
            "comments": [
                comment("Creazione utenza in modo semplice", "A."),
                comment("biometrico deve essere", "B."),  # forces run-splitting
                comment("Testo che non esiste nel documento", "C."),  # forces a flag
            ]
        },
    )
    assert doc_text(out) == before, "injection modified the document's own text"


# ── error paths ────────────────────────────────────────────────────────────


def test_error_unmatched_anchor_becomes_flagged_doc_start_comment_and_still_succeeds(out):
    proc = run_injector(
        SAMPLE,
        out,
        {"comments": [comment("Questa frase non compare da nessuna parte", "Dove va?")]},
    )
    assert proc.returncode == 0, "an unlocatable quote is a flag, not a failure"

    ours = margins_comments(out)
    assert len(ours) == 1
    text = ours[0][1]
    assert "position uncertain" in text.lower(), "unmatched anchors must be flagged, not silently misplaced"
    assert "Dove va?" in text

    summary = json.loads(proc.stdout)
    assert summary["uncertain"] == 1


def test_error_ambiguous_anchor_is_flagged_rather_than_guessed(out):
    # "Da definire in fase di analisi." appears under BOTH Sezione A and B.
    proc = run_injector(
        SAMPLE, out, {"comments": [comment("Da definire in fase di analisi.", "Quale?")]}
    )
    summary = json.loads(proc.stdout)
    assert summary["uncertain"] == 1, "an anchor matching two paragraphs must not be guessed"
    assert "position uncertain" in margins_comments(out)[0][1].lower()


def test_happy_context_disambiguates_an_otherwise_ambiguous_anchor(out):
    # Same duplicated sentence, but the caller supplies the preceding heading —
    # that is enough to pin it to Sezione B.
    proc = run_injector(
        SAMPLE,
        out,
        {
            "comments": [
                comment("Da definire in fase di analisi.", "Serve dettaglio.", before="Sezione B")
            ]
        },
    )
    summary = json.loads(proc.stdout)
    assert summary["uncertain"] == 0, "before-context should have disambiguated the duplicate"
    assert summary["injected"] == 1


def test_error_missing_input_file_exits_nonzero_with_machine_readable_stderr(tmp_path):
    proc = run_injector(
        tmp_path / "nope.docx", tmp_path / "out.docx", {"comments": []}, expect_ok=False
    )
    assert proc.returncode != 0
    assert re.search(r"^margins-docx: [A-Z_]+: ", proc.stderr, re.M), proc.stderr
    assert "INPUT_NOT_FOUND" in proc.stderr


def test_error_malformed_json_exits_nonzero_with_machine_readable_stderr(out):
    proc = run_injector(SAMPLE, out, "{ not valid json", expect_ok=False)
    assert proc.returncode != 0
    assert "INVALID_JSON" in proc.stderr
    assert re.search(r"^margins-docx: [A-Z_]+: ", proc.stderr, re.M)


def test_error_non_docx_input_exits_nonzero_cleanly(tmp_path, out):
    fake = tmp_path / "fake.docx"
    fake.write_text("I am not a zip, let alone a docx.")
    proc = run_injector(fake, out, {"comments": []}, expect_ok=False)
    assert proc.returncode != 0
    assert "NOT_A_DOCX" in proc.stderr
    assert "Traceback" not in proc.stderr, "structural failures must not surface as tracebacks"
