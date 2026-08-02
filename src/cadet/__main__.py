import os
import signal
import sys

from cadet import config


def _handle_signal(sig: int, frame: object) -> None:
    sys.exit(0)


def main() -> None:
    # Fail fast with a clear, top-level error rather than deferring to the
    # first delegate_task call or letting it surface as an opaque exception
    # from inside FastMCP's async lifespan machinery.
    try:
        config.resolve_agy_docker_image()
    except RuntimeError as exc:
        print(f"CADET failed to start: {exc}", file=sys.stderr)
        sys.exit(1)

    from cadet.mcp.server import mcp
    import cadet.mcp.tools  # noqa: F401 - import registers the @mcp.tool() decorators

    # Handle process termination signals (SIGTERM/SIGINT) gracefully so host
    # process managers (e.g. agy, systemd) can reload or stop CADET cleanly
    # without reporting `signal: terminated`.
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    try:
        mcp.run()
    except BaseExceptionGroup as eg:
        for exc in eg.exceptions:
            if isinstance(exc, SystemExit) and exc.code == 0:
                os._exit(0)
        raise
    except SystemExit as exc:
        if exc.code == 0:
            os._exit(0)
        raise
    except Exception:
        raise
    else:
        os._exit(0)


if __name__ == "__main__":
    main()

