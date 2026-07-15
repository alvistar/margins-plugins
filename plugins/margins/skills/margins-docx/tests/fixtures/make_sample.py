#!/usr/bin/env python3
"""Regenerate `sample.docx`, the fixture the injector suite runs against.

Committed so the fixture is auditable and reproducible rather than an opaque
blob. Run it via:

    uvx --with "python-docx>=1.2" python3 make_sample.py

The fixture deliberately encodes the shapes the injector has to survive. Several
can only be built by hand-writing OOXML, because python-docx's flat add_run()/
add_paragraph() API cannot produce them — and those are exactly the shapes that
hid real bugs behind a text-only invariant check:

  - a paragraph whose text sits in ONE run (the easy anchor)
  - a paragraph split mid-word across THREE runs (real Word docs do this via
    spell-check and rsid boundaries; the common case, not a corner)
  - a run mixing text with a <w:br/> line break, so an interior split must keep
    the break exactly once rather than duplicating it
  - a run whose visible text lives inside a <w:hyperlink> wrapper
  - a run inside a <w:ins> tracked-change insertion
  - a phrase repeated twice in ONE paragraph and once in another, so a
    globally-ambiguous anchor cannot be silently placed
  - two paragraphs with IDENTICAL text (only before/after context tells them
    apart)
  - a pre-existing comment by a HUMAN author, which strip-before-reinject must
    never remove
  - a pre-existing comment whose author is literally "Margins Review" but which
    carries NO Margins initials marker: a coincidental human, which must survive

Kept text-only (no images), so the fixture stays a few KB. Everything under the
skill directory ships to every installer.
"""

from pathlib import Path

from docx import Document
from docx.oxml.ns import qn

OUT = Path(__file__).with_name("sample.docx")


def _el(parent, tag, text=None, **attrs):
    node = parent.makeelement(qn(tag), {})
    for k, v in attrs.items():
        node.set(qn(k.replace("_", ":", 1)), v)
    if text is not None:
        node.text = text
    parent.append(node)
    return node


def main() -> None:
    d = Document()

    d.add_heading("Credi Wallet — Macro Specifiche", level=1)

    d.add_heading("Onboarding e accesso", level=2)
    # Single-run paragraph: the exact-match happy path.
    p_single = d.add_paragraph(
        "Creazione utenza in modo semplice e veloce tramite email."
    )

    # Three runs, split MID-WORD ("bio|metrico"). An anchor spanning the split
    # can only land after the runs are partitioned.
    p_split = d.add_paragraph()
    p_split.add_run("L'accesso bio")
    p_split.add_run("metrico deve ")
    p_split.add_run("essere opzionale.")

    # One run mixing text with a <w:br/> line break: "prima<br/>dopo". An anchor
    # crossing the break forces an interior split around a non-text child.
    p_break = d.add_paragraph()
    br_run = p_break.add_run()._r
    _el(br_run, "w:t", "prima")
    _el(br_run, "w:br")
    _el(br_run, "w:t", "dopo interruzione")

    # Visible hyperlink text: the run is nested under <w:hyperlink>, so a naive
    # direct-children walk would never see "documentazione ufficiale".
    p_link = d.add_paragraph()
    p_link.add_run("Consultare la ")
    hl = _el(p_link._p, "w:hyperlink", w_anchor="ref1")
    hr = _el(hl, "w:r")
    _el(hr, "w:t", "documentazione ufficiale")
    p_link.add_run(" del progetto.")

    # Tracked-change insertion: the run is nested under <w:ins>.
    p_ins = d.add_paragraph()
    p_ins.add_run("Il testo ")
    ins = _el(p_ins._p, "w:ins", w_id="900", w_author="Reviewer", w_date="2026-07-14T00:00:00Z")
    ir = _el(ins, "w:r")
    _el(ir, "w:t", "revisionato e approvato")
    p_ins.add_run(" resta valido.")

    d.add_heading("Pagamenti", level=2)
    d.add_paragraph("Il borsellino potra essere ricaricato tramite contanti.")

    # A phrase that appears TWICE in one paragraph, and once in another — a
    # globally-ambiguous anchor that must be flagged, not silently placed.
    d.add_paragraph("Vedi la nota. Poi rileggi la nota per conferma.")
    d.add_paragraph("In appendice trovi la nota completa.")

    # Two paragraphs with identical text, under different headings. Anchoring on
    # this string alone is ambiguous by construction.
    d.add_heading("Sezione A", level=2)
    d.add_paragraph("Da definire in fase di analisi.")
    d.add_heading("Sezione B", level=2)
    d.add_paragraph("Da definire in fase di analisi.")

    # A human's own Word comment. Stripping Margins comments must leave it alone.
    d.add_comment(
        runs=p_single.runs[0],
        text="Confermare il flusso di registrazione con il cliente.",
        author="Pietro Mosconi",
        initials="PM",
    )

    # A coincidental human whose display name is literally "Margins Review" but
    # who is NOT this script (no "MGN" initials marker). Must survive stripping.
    d.add_comment(
        runs=p_break.runs[0],
        text="Nota personale, non generata da Margins.",
        author="Margins Review",
        initials="RM",
    )

    d.save(OUT)
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
