"""Fixture suite for `inject_comments.py`.

Assertions read the output .docx as a ZIP and inspect `word/comments.xml` and
`word/document.xml` directly, rather than asking python-docx to read back its
own writes — a library that round-trips its own bug still round-trips it. Word
is the real consumer, and Word reads the XML.

Run:
    uvx --with "python-docx>=1.2" --with pytest pytest plugins/margins/skills/margins-docx/tests
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

# What makes a comment ours: the author tag AND the paired initials marker,
# together. Keying on the author alone would misclassify a human whose Word
# display name is literally "Margins Review" — the suite mirrors the script's
# own _is_ours() ownership test, not a cosmetic label.
MARGINS_AUTHOR = "Margins Review"
MARGINS_INITIALS = "MGN"

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
    return [(a, t) for a, _i, t in _comment_blocks(docx_path)]


def _comment_blocks(docx_path):
    """[(author, initials, text)] per comment — initials distinguishes ours."""
    xml = part(docx_path, "word/comments.xml")
    if not xml:
        return []
    out = []
    for block in re.findall(r"<w:comment\b.*?</w:comment>", xml, re.S):
        author = re.search(r'w:author="([^"]*)"', block)
        initials = re.search(r'w:initials="([^"]*)"', block)
        text = "".join(re.findall(r"<w:t[^>]*>([^<]*)</w:t>", block))
        out.append(
            (author.group(1) if author else "", initials.group(1) if initials else "", text)
        )
    return out


def margins_comments(docx_path):
    """Only the comments THIS SCRIPT authored — author + initials marker."""
    return [
        (a, t)
        for a, i, t in _comment_blocks(docx_path)
        if a == MARGINS_AUTHOR and i == MARGINS_INITIALS
    ]


def comment_paragraph_count(docx_path, index=0):
    """Number of <w:p> paragraphs in the index-th Margins comment's body."""
    xml = part(docx_path, "word/comments.xml")
    blocks = [
        b
        for b in re.findall(r"<w:comment\b.*?</w:comment>", xml, re.S)
        if re.search(r'w:initials="' + MARGINS_INITIALS + '"', b)
    ]
    if index >= len(blocks):
        return 0
    return len(re.findall(r"<w:p\b", blocks[index]))


def doc_text(docx_path):
    """Concatenated body text — the invariant the injector must never disturb."""
    xml = part(docx_path, "word/document.xml")
    return "".join(re.findall(r"<w:t[^>]*>([^<]*)</w:t>", xml))


def body_element_counts(docx_path):
    """Per-tag counts of non-text body run children (breaks, tabs, drawings…).

    doc_text() is w:t-only, so it is blind to a duplicated or lost <w:br>/<w:tab>/
    <w:drawing>. Layout preservation is asserted against these counts instead.
    """
    xml = part(docx_path, "word/document.xml")
    # strip the comments-anchor markers, which legitimately change on injection
    return {
        tag: len(re.findall(rf"<w:{tag}\b", xml))
        for tag in ("br", "tab", "cr", "drawing", "noBreakHyphen")
    }


def anchored_text(docx_path, comment_id):
    """The body text bracketed by commentRangeStart/End for `comment_id`."""
    xml = part(docx_path, "word/document.xml")
    start = re.search(rf'<w:commentRangeStart[^>]*w:id="{comment_id}"[^>]*/>', xml)
    end = re.search(rf'<w:commentRangeEnd[^>]*w:id="{comment_id}"[^>]*/>', xml)
    if not start or not end:
        return None
    return "".join(re.findall(r"<w:t[^>]*>([^<]*)</w:t>", xml[start.end() : end.start()]))


def margins_ids(docx_path):
    """Comment ids this script authored (author + initials marker)."""
    xml = part(docx_path, "word/comments.xml")
    ids = []
    for block in re.findall(r"<w:comment\b.*?</w:comment>", xml, re.S):
        cid = re.search(r'w:id="([^"]*)"', block)
        author = re.search(r'w:author="([^"]*)"', block)
        initials = re.search(r'w:initials="([^"]*)"', block)
        if (
            cid
            and author
            and initials
            and author.group(1) == MARGINS_AUTHOR
            and initials.group(1) == MARGINS_INITIALS
        ):
            ids.append(cid.group(1))
    return ids


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
    # Structure, not just substring membership: each message is its own paragraph.
    # A regression that space-joins the thread into one run-on paragraph keeps all
    # the substrings but collapses the count — so assert on <w:p> boundaries.
    assert comment_paragraph_count(out) >= 2, "thread messages must stay separate paragraphs"


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


def test_edge_unanchored_discussion_is_document_level_not_uncertain(out):
    # Margins allows a discussion on the document as a whole — `anchorSelectedText`
    # is simply absent. That is not a failed match, and labelling it "uncertain"
    # would cry wolf. It lands at the top, correctly described.
    proc = run_injector(
        SAMPLE, out, {"comments": [{"body": "alice: commento generale sul documento."}]}
    )
    summary = json.loads(proc.stdout)
    assert summary["document_level"] == 1
    assert summary["uncertain"] == 0

    text = margins_comments(out)[0][1]
    assert "document-level" in text.lower()
    assert "position uncertain" not in text.lower()


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


# ── layout preservation (non-text run children) ─────────────────────────────


def test_edge_split_run_across_line_break_does_not_duplicate_the_break(out):
    # The fixture's p_break run is <w:t>prima</w:t><w:br/><w:t>dopo interruzione</w:t>.
    # An anchor crossing the break forces an interior split on BOTH sides of the
    # <w:br>. The old deep-copy split left the break in both halves (1 -> 3),
    # changing the rendered layout while doc_text() stayed identical.
    # The <w:br> carries no text, so the run reads "primadopo interruzione".
    # This anchor starts inside "prima" and ends inside "dopo interruzione", so
    # the split lands on both sides of the break — the interior case that tripled it.
    before = body_element_counts(SAMPLE)
    run_injector(SAMPLE, out, {"comments": [comment("rimadopo interr", "commento sull'interruzione")]})

    after = body_element_counts(out)
    assert after["br"] == before["br"], "run-splitting duplicated or dropped a <w:br>"
    assert doc_text(out) == doc_text(SAMPLE)
    # ...and it actually anchored (proving the split ran, not that it was skipped).
    assert len(margins_ids(out)) == 1
    assert anchored_text(out, margins_ids(out)[0]) == "rimadopo interr"


def test_edge_split_run_with_inline_drawing_preserves_it_and_its_docpr_id(tmp_path, out):
    # An inline image split by an anchor is the worst case: the old code tripled
    # the <w:drawing>, producing three identical wp:docPr id="1" — a duplicate-id
    # collision that makes Word offer to repair the document. Built inline because
    # a committed image would bloat the shipped fixture.
    from docx import Document
    from docx.oxml.ns import qn

    d = Document()
    p = d.add_paragraph()
    r = p.add_run()._r
    _t1 = r.makeelement(qn("w:t"), {}); _t1.text = "A"; r.append(_t1)
    drawing = r.makeelement(qn("w:drawing"), {})
    docpr = drawing.makeelement(qn("wp:docPr"), {}); docpr.set("id", "1"); docpr.set("name", "img")
    drawing.append(docpr); r.append(drawing)
    _t2 = r.makeelement(qn("w:t"), {}); _t2.text = "B end"; r.append(_t2)
    src = tmp_path / "img.docx"
    d.save(str(src))

    run_injector(src, out, {"comments": [comment("B end", "commento sull'immagine")]})

    xml = part(out, "word/document.xml")
    assert xml.count("<w:drawing") == 1, "inline drawing was duplicated by the split"
    docpr_ids = re.findall(r'<wp:docPr[^>]*\bid="([^"]*)"', xml)
    assert docpr_ids == ["1"], f"duplicate docPr ids corrupt the package: {docpr_ids}"


# ── nested runs (hyperlinks, tracked changes) ───────────────────────────────


def test_edge_anchor_inside_hyperlink_is_found_and_anchored(out):
    # The fixture wraps "documentazione ufficiale" in <w:hyperlink>. A direct-
    # children run walk never sees it; the descendant-aware walk does.
    proc = run_injector(
        SAMPLE, out, {"comments": [comment("documentazione ufficiale", "quale versione?")]}
    )
    summary = json.loads(proc.stdout)
    assert summary["injected"] == 1, "hyperlinked text was invisible to matching"
    assert anchored_text(out, margins_ids(out)[0]) == "documentazione ufficiale"


def test_edge_anchor_inside_tracked_insertion_is_found(out):
    # "revisionato e approvato" lives inside a <w:ins> tracked-change wrapper.
    proc = run_injector(
        SAMPLE, out, {"comments": [comment("revisionato e approvato", "conferma la modifica")]}
    )
    summary = json.loads(proc.stdout)
    assert summary["injected"] == 1, "tracked-change text was invisible to matching"
    assert anchored_text(out, margins_ids(out)[0]) == "revisionato e approvato"


# ── global ambiguity (never silently misplace) ──────────────────────────────


def test_error_anchor_repeated_within_one_paragraph_is_flagged_not_misplaced(out):
    # "la nota" appears twice in one paragraph and once in another. Counting only
    # the paragraph that has a *single* hit would place this confidently on the
    # wrong occurrence; every occurrence must count toward ambiguity.
    proc = run_injector(SAMPLE, out, {"comments": [comment("la nota", "quale nota?")]})
    summary = json.loads(proc.stdout)
    assert summary["uncertain"] == 1, "a globally-ambiguous anchor was silently placed"
    assert summary["injected"] == 0
    assert "position uncertain" in margins_comments(out)[0][1].lower()


def test_happy_after_context_disambiguates(out):
    # Same duplicated sentence as the ambiguity test, but `after` pins it to
    # Sezione A (whose following paragraph is the Sezione B heading).
    proc = run_injector(
        SAMPLE,
        out,
        {"comments": [comment("Da definire in fase di analisi.", "dettagli?", after="Sezione B")]},
    )
    summary = json.loads(proc.stdout)
    assert summary["uncertain"] == 0, "after-context should have disambiguated the duplicate"
    assert summary["injected"] == 1


# ── ownership marker (author-name collision) ────────────────────────────────


def test_edge_human_comment_named_margins_review_survives(out):
    # The fixture carries a human comment authored "Margins Review" but with NO
    # "MGN" initials marker. Author-only stripping would delete it; ownership is
    # author AND initials together.
    run_injector(SAMPLE, out, {"comments": [comment("Creazione utenza", "nostro commento")]})

    blocks = _comment_blocks(out)
    coincidental = [
        (a, i, t) for a, i, t in blocks if a == MARGINS_AUTHOR and i != MARGINS_INITIALS
    ]
    assert coincidental, "a human named 'Margins Review' had their comment stripped"
    assert any("non generata da Margins" in t for _a, _i, t in coincidental)
    # and our own comment is still present, tagged with the marker
    assert len(margins_comments(out)) == 1


# ── robustness: clean errors, never tracebacks ──────────────────────────────


def test_error_non_string_field_exits_nonzero_cleanly(out):
    proc = run_injector(SAMPLE, out, {"comments": [{"anchor": 123, "body": "x"}]}, expect_ok=False)
    assert proc.returncode != 0
    assert "INVALID_PAYLOAD" in proc.stderr
    assert "Traceback" not in proc.stderr, "a non-string field must not crash with AttributeError"


def test_edge_control_characters_in_body_are_sanitized_not_crashed(out):
    # A reviewer comment carrying a NUL/control char must not blow up lxml with a
    # raw traceback; the char is stripped and the comment still lands.
    body = "prima" + chr(0) + "dopo" + chr(7)
    proc = run_injector(
        SAMPLE, out, {"comments": [comment("Creazione utenza in modo semplice", body)]}
    )
    assert proc.returncode == 0
    text = margins_comments(out)[0][1]
    assert "primadopo" in text
    assert chr(0) not in text and chr(7) not in text


def test_error_corrupt_zip_exits_nonzero_cleanly(tmp_path, out):
    # A file that passes zipfile.is_zipfile() (valid end-of-archive record) but
    # has a broken central directory raises BadZipFile on read — must be caught.
    corrupt = tmp_path / "corrupt.docx"
    corrupt.write_bytes(b"PK\x03\x04" + b"\x00" * 40 + b"PK\x05\x06" + b"\xff" * 18)
    proc = run_injector(corrupt, out, {"comments": []}, expect_ok=False)
    assert proc.returncode != 0
    assert "NOT_A_DOCX" in proc.stderr
    assert "Traceback" not in proc.stderr


# ── output integrity ────────────────────────────────────────────────────────


def test_edge_export_is_atomic_no_temp_file_left_behind(out):
    run_injector(SAMPLE, out, {"comments": [comment("Creazione utenza", "ok")]})
    leftovers = list(out.parent.glob(".margins-docx-*"))
    assert not leftovers, f"atomic save left a temp file behind: {leftovers}"
    assert zipfile.is_zipfile(out)


def test_happy_summary_reports_per_id_placement(out):
    proc = run_injector(
        SAMPLE,
        out,
        {
            "comments": [
                comment("Creazione utenza in modo semplice", "ok", id="d1"),
                comment("Questa frase non esiste nel documento", "?", id="d2"),
                {"body": "commento generale", "id": "d3"},
            ]
        },
    )
    summary = json.loads(proc.stdout)
    by_id = {r["id"]: r["placement"] for r in summary["results"]}
    assert by_id == {"d1": "injected", "d2": "uncertain", "d3": "document_level"}
