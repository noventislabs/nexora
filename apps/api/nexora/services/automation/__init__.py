"""Autonomous content automation.

Four pieces, deliberately separate so each can be tested and reasoned about alone:

* :mod:`gates` — may automation act on this channel at all, and how far?
* :mod:`dedupe` — would this topic repeat work the channel has already done?
* :mod:`locks` — durable claims, so two workers never build the same video.
* :mod:`factgate` — may automation publish these claims?
* :mod:`orchestrator` — the run that walks the pipeline.
"""

from nexora.services.automation import dedupe, factgate, gates, locks, orchestrator

__all__ = ["dedupe", "factgate", "gates", "locks", "orchestrator"]
