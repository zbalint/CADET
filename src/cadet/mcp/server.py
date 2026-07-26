import asyncio
import sys
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from mcp.server.fastmcp import FastMCP

from cadet import config
from cadet.db.schema import init_db
from cadet.jobs.dispatcher import Dispatcher
from cadet.jobs.reconcile import reconcile_on_startup
from cadet.jobs.retention import retention_sweep_loop
from cadet.web.server import run_web_server

logging.basicConfig(
    stream=sys.stderr,  # MUST be stderr — stdout is the MCP stdio transport.
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

_RETENTION_SWEEP_INTERVAL_S = 3600


@asynccontextmanager
async def server_lifespan(server: FastMCP) -> AsyncIterator[dict]:
    """Mirrors SALTMDB's server_lifespan/init_db startup pattern, extended with
    CADET's own job-store reconciliation, dispatcher startup, and retention
    sweep loop."""
    db_path = config.get_db_path()
    logger.info("Initializing CADET database schema at: %s", db_path)
    conn = init_db(db_path)
    conn.close()

    # "agy" is required/fail-fast at server bootstrap (see __main__.py); other
    # providers are optional here — an unconfigured one just isn't in the dict,
    # and delegate_task rejects requests for it per-call with a clean error.
    executable_paths = {"agy": config.resolve_agy_path()}
    dispatcher = Dispatcher(
        executable_paths=executable_paths, max_concurrent=config.get_max_concurrent(), db_path=db_path
    )

    # Deferred import (not at module load time) to avoid a server<->tools
    # circular import: tools.py imports `mcp` from this module at its own
    # import time, so this module can only reach back into tools.py later.
    from cadet.mcp import tools as mcp_tools
    mcp_tools.set_dispatcher(dispatcher)

    summary = await reconcile_on_startup(dispatcher, db_path=db_path)
    logger.info(
        "Startup reconciliation: re-enqueued %d pending job(s), marked %d running job(s) unknown-interrupted",
        summary["reenqueued"], summary["interrupted"],
    )

    dispatcher.start()
    retention_task = asyncio.create_task(retention_sweep_loop(_RETENTION_SWEEP_INTERVAL_S, db_path=db_path))
    web_task = asyncio.create_task(run_web_server(dispatcher, db_path))

    try:
        yield {}
    finally:
        retention_task.cancel()
        web_task.cancel()
        logger.info("CADET server shutting down.")


mcp = FastMCP("CADET", lifespan=server_lifespan)
