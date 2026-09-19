"""Job handler registrations.

Each phase adds its handlers here. Importing this module is what makes handlers
visible to the worker.
"""

from __future__ import annotations

# Phase 1 registers no domain handlers; later phases import their modules here so the
# worker picks them up without touching the worker loop itself.
