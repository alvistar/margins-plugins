#!/usr/bin/env python3
"""Regenerate `sample.docx`, the fixture the injector suite runs against.

Committed so the fixture is auditable and reproducible rather than an opaque
blob. Run it via:

    uvx --with python-docx python3 make_sample.py

The fixture deliberately encodes the shapes the injector has to survive:

  - a paragraph whose text sits in ONE run (the easy anchor)
  - a paragraph split mid-word across THREE runs (a real Word doc does this on
    its own via spell-check and rsid boundaries; the U1 spike measured 1 anchor
    in 10 landing this way, so it is the common case, not a corner)
  - two paragraphs with IDENTICAL text (only before/after context can tell them
    apart — otherwise the anchor is genuinely ambiguous)
  - a pre-existing comment by a HUMAN author, which strip-before-reinject must
    never remove

Kept text-only: no images, so the fixture stays a few KB. Everything under the
skill directory ships to every installer.
"""

from pathlib import Path

from docx import Document

OUT = Path(__file__).with_name("sample.docx")


def main() -> None:
    d = Document()

    d.add_heading("Credi Wallet — Macro Specifiche", level=1)

    d.add_heading("Onboarding e accesso", level=2)
    # Single-run paragraph: the exact-match happy path.
    p_single = d.add_paragraph(
        "Creazione utenza in modo semplice e veloce tramite email."
    )

    # Three runs, split MID-WORD ("bio|metrico"). An anchor spanning the split
    # can only be commented on after the runs are partitioned.
    p_split = d.add_paragraph()
    p_split.add_run("L'accesso bio")
    p_split.add_run("metrico deve ")
    p_split.add_run("essere opzionale.")

    d.add_heading("Pagamenti", level=2)
    d.add_paragraph("Il borsellino potra essere ricaricato tramite contanti.")

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

    d.save(OUT)
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
