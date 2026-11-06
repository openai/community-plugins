#!/usr/bin/env python3
"""Bounded PDF structure/text readback, executed inside the pinned runtime.

This parser does not judge the visual fidelity of the outlined CAD views.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
import re
import sys


def inspect_pdf(path: Path) -> dict:
    from pypdf import PdfReader

    if not 100 <= path.stat().st_size <= 25_000_000:
        raise ValueError("PDF size is outside the supported bound")
    reader = PdfReader(path, strict=True)
    if reader.is_encrypted:
        raise ValueError("Encrypted PDFs are unsupported")
    if len(reader.pages) > 10:
        raise ValueError("PDF page count exceeds the readback bound")
    pages = []
    for page in reader.pages:
        width, height = float(page.mediabox.width), float(page.mediabox.height)
        if not all(math.isfinite(value) and value > 0 for value in (width, height)):
            raise ValueError("PDF media box is invalid")
        text = re.sub(r"\s+", " ", page.extract_text() or "").strip()
        if len(text) > 100_000:
            raise ValueError("PDF text exceeds the readback bound")
        pages.append({"width_pt": width, "height_pt": height,
                      "has_contents": page.get_contents() is not None, "text": text})
    return {"schema": 1, "status": "pass", "encrypted": False,
            "page_count": len(reader.pages), "pages": pages}


def main() -> int:
    try:
        if len(sys.argv) != 2:
            raise ValueError("Expected one PDF path")
        result = inspect_pdf(Path(sys.argv[1]))
    except Exception as error:
        print(json.dumps({"schema": 1, "status": "error",
                          "error": f"{type(error).__name__}: {error}"}))
        return 1
    print(json.dumps(result, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
