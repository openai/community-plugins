# Actual fault correction contracts

The native correction template remains an explicitly pinned ordinary correction
contract. Its permitted file and rendered selector must exactly match the
independently qualified policy. Its profile SHA must match the selected external
Helm profile. The template's base commit is a template value; it never establishes
the actual fault source or grants delivery.

After each primary/replay fault is actually merged, observed and materialized,
the original operation coordinator derives a contract that changes only
`base_commit` to that observed fault commit. It verifies the direct delivery and
checkout origins, the original F0 qualification, the delivered source snapshot,
and the exact rendered fault selector. No permitted file or pointer is expanded.

The original process retains separate stable contract paths and SHA256 values
for primary and replay. Repeated native preparation and scope calls re-audit
the same direct origins and original bytes. A copied document cannot reconstruct
this derivation. The phase evidence retains the original template, profile,
baseline/fault source, actual rendered fault, derived contract and a derivation
receipt naming the original fault operation and qualification. These artifacts
provide evidence; they grant no new mutation authority.

The fixed operation evidence closure reserves one result slot for the enclosing
phase receipt: at most 255 original paths and 64 MB total with the current
256-reference journal bound. It refuses excess evidence rather than omitting it.
