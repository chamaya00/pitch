"""Workflow coordination.

The one place that spans the other service packages: it takes a recording,
drives it through a provider, the deterministic metrics and a second provider,
and persists what came back at every step.

It owns no measurement, no vendor knowledge and no HTTP. Everything it does is
expressed in terms of the protocols in ``services.ai`` and the models in
``services.analysis``, so a route can hand it a recording id and nothing else.
"""
