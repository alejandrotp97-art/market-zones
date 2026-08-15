"""Regime Engine v2 — research sandbox (P7).

Fully isolated laboratory. Reuses production ONLY as a read-only data/feature
source (zones.fetch_daily, regime.analyze for the causal axes). It NEVER imports
mutable state from, nor writes to, the production engine / dashboard / P5 validation.
Its sole purpose is to prove or refute the RFC-v2 hypotheses out-of-sample.
"""
