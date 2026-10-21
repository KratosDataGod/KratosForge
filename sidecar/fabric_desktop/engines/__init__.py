"""Execution engines behind one contract (Unified Fabric + Databricks development, Phase 1).

``capabilities`` says what each engine can do and refuses overclaims; ``contract`` is what an
adapter implements; ``sessions`` keeps one session per notebook and engine; ``authorization``
guards remote compute; ``usage`` records measured, billed and estimated usage without mixing
units; ``registry`` lists every engine. Design: docs/engines.md.
"""
