"""Incremental v3.2 contract layer.

This subpackage implements the LEExtractor Development Spec v3.2 (document
version 3.2, shipped under ``docs/v3.2/``).  It deliberately lives beside the
legacy modules instead of replacing them: the specification requires the old
routes and the old ``relevance_score`` semantics to keep working while the new
project-scoped model is added alongside.

Module map (spec name -> this package):

===========================  ==========================
spec / doc 06 name           module
===========================  ==========================
contracts (defaults, schemas)  :mod:`litsearch.v32.contracts`
domain types and enums         :mod:`litsearch.v32.types`
error envelope                 :mod:`litsearch.v32.errors`
core_rank                      :mod:`litsearch.v32.core_rank`
feedback                       :mod:`litsearch.v32.feedback`
pdf_import                     :mod:`litsearch.v32.pdf_import`
ai_rerank                      :mod:`litsearch.v32.ai_rerank`
overview / field insights      :mod:`litsearch.v32.overview`
credentials                    :mod:`litsearch.v32.credentials`
access_gateway                 :mod:`litsearch.v32.access_gateway`
venue_metrics                  :mod:`litsearch.v32.venue_metrics`
zotero_adapter                 :mod:`litsearch.v32.zotero`
project store and migrations   :mod:`litsearch.v32.store`
job scheduler and state machine:mod:`litsearch.v32.jobs`
HTTP surface                   :mod:`litsearch.v32.api`
===========================  ==========================
"""

from __future__ import annotations

__all__ = ["CONTRACT_VERSION"]

#: The document/contract version this package implements.  Distinct from the
#: software version in :mod:`litsearch.version` and from the persisted
#: ``schema_version`` of a project file (see :mod:`litsearch.v32.store`).
CONTRACT_VERSION = "3.2"
