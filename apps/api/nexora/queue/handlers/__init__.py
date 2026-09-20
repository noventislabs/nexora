"""Job handler registrations.

Importing this package is what makes handlers visible to the worker. Each phase adds
its module here.
"""

from nexora.queue.handlers import automation as _automation  # noqa: F401
from nexora.queue.handlers import content as _content  # noqa: F401
from nexora.queue.handlers import media as _media  # noqa: F401
from nexora.queue.handlers import publishing as _publishing  # noqa: F401
from nexora.queue.handlers import trends as _trends  # noqa: F401

__all__ = ["_content", "_media", "_publishing", "_trends"]
