"""Strict, bounded JSON for local scanner catalogs and retained scan evidence."""
from __future__ import annotations

from .http import _bounded_json
from .signatures import _Invalid

MAX_DOCUMENT_BYTES = 16_000_000
MAX_DOCUMENT_VALUES = 500_000


def parse_scanner_document(raw):
    """Admit realistic image inventories within a fixed local-evidence budget.

    The shared strict parser still rejects duplicate keys, nonfinite numbers,
    depth above 64 and excessive values. HTTP/control-plane limits are unchanged.
    """
    if not isinstance(raw, bytes) or not 1 <= len(raw) <= MAX_DOCUMENT_BYTES:
        raise _Invalid('scanner-evidence-bound-exceeded')
    return _bounded_json(raw, MAX_DOCUMENT_VALUES, MAX_DOCUMENT_VALUES)
