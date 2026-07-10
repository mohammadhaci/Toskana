"""Toskana training pipeline: standalone stage scripts (ingest -> export).

Each stage is an independent CLI script (``python training/<stage>.py --help``)
that records a ``manifest.json`` in its output directory (inputs, parameters,
source licenses where relevant, git SHA) so every trained model has an
auditable provenance chain. See ``training/README.md`` for the full walkthrough.
"""
