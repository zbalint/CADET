import contextlib
import logging

import uvicorn

from cadet import config
from cadet.web.app import create_app

logger = logging.getLogger(__name__)


class _EmbeddedServer(uvicorn.Server):
    """uvicorn.Server.serve() installs process-wide SIGINT/SIGTERM/SIGBREAK
    handlers when run on the main thread, which CADET's process is. Left
    default, that would hijack Ctrl+C/termination for the whole cadet-server
    process for as long as this task is alive (its whole lifetime). The MCP
    stdio host owns those signals instead, so this override is required, not
    optional."""

    @contextlib.contextmanager
    def capture_signals(self):
        yield


async def run_web_server(dispatcher, db_path: str) -> None:
    if not config.is_web_enabled():
        return

    app = create_app(dispatcher, db_path)
    uv_config = uvicorn.Config(
        app,
        host=config.get_web_host(),
        port=config.get_web_port(),
        # None (not uvicorn's default dictConfig) so uvicorn never attaches its
        # own stdout-capable handlers — stdout is the MCP JSON-RPC transport
        # and must stay untouched. Its loggers propagate to root instead,
        # which already has the stderr handler set up in mcp/server.py.
        log_config=None,
    )
    try:
        await _EmbeddedServer(uv_config).serve()
    except SystemExit:
        # uvicorn.Server.startup() catches the bind OSError itself and calls
        # sys.exit(), not raise — that's a SystemExit (BaseException), which
        # `except Exception` below does NOT catch. Left uncaught, asyncio
        # re-raises SystemExit/KeyboardInterrupt out of the Task step instead
        # of containing it, which kills the whole event loop — i.e. the
        # entire MCP server process, not just this task. See SALTMDB memory
        # 56cc7391 for the live-reproduction trace this fixes.
        logger.warning("Web dashboard failed to start (port already in use?); MCP tool surface remains available.")
    except Exception:
        # The dashboard is a convenience layer, not the primary contract — a
        # bind failure (e.g. port already in use) must not take down the MCP
        # tool surface delegate_task/check_task_status/etc. depend on.
        logger.warning("Web dashboard failed to start; MCP tool surface remains available.", exc_info=True)
