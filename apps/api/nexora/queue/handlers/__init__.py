"""Job handler registrations.

Importing this package is what makes handlers visible to the worker. Each phase adds
its module here.
"""

from nexora.queue.handlers import content as _content  # noqa: F401
from nexora.queue.handlers import trends as _trends  # noqa: F401

__all__ = ["_content", "_trends"]
