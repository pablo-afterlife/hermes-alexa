"""hermes-alexa — Alexa for Hermes Agent.

Tools that make the user's Echo speak, announce and run Alexa commands; the ``hermes alexa``
command; and Alexa as a gateway platform ("Alexa, ask Hermes…", answered on the same Echo).
Wiring goes only through the public plugin API (``register_tool``, ``register_cli_command``,
``register_platform``).
"""

from __future__ import annotations

from . import alexa_tools as _t
from .alexa_cli import handle_cli, setup_cli
from .alexa_platform import register_platform


def register(ctx) -> None:
    """Plugin entry point — called by the Hermes plugin loader."""
    for name, schema, handler, emoji in _t.TOOLS:
        ctx.register_tool(name=name, toolset=_t.TOOLSET, schema=schema, handler=handler,
                          check_fn=_t.check_available, emoji=emoji)
    ctx.register_cli_command(
        name="alexa", help="Set up and control the Alexa bridge (login, status, say)",
        description="hermes-alexa: Amazon login, bridge status, and a quick `say` test.",
        setup_fn=setup_cli, handler_fn=handle_cli)
    register_platform(ctx)


__all__ = ["register"]
