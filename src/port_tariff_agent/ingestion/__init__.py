"""Ingestion phase: turn one tariff PDF into a cached, machine-readable document.

Steps (see docs/spec/ingestion.md):
1. PageTranscriber  - LLM, one call per PDF page -> pages/*.md -> tariff.md
2. Index builder    - code only, regex on numbered headings -> tariff_index.json
3. ChargeClassifier - LLM, one small call per section -> charges.json
4. DocumentProfiler - LLM, one call on the front pages -> row in documents.json
"""
