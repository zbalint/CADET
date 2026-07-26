import sys

from cadet import config


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

    mcp.run()


if __name__ == "__main__":
    main()
