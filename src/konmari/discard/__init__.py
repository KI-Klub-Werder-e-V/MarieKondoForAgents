"""Discard pass (c04): dedup detection and human-approved, reversible, audited retention decisions."""

from konmari.discard.corpus import kept_items, register_derived  # noqa: F401  (read by c05, c06, c08)
from konmari.discard.decisions import awaiting_masking, check_propagation, register_masked  # noqa: F401  (c07, c09)
