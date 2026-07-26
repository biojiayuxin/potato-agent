from __future__ import annotations

import argparse

import uvicorn


UVICORN_WEBSOCKET_MAX_BYTES = 1024 * 1024


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Potato Interface")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=3000)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    uvicorn.run(
        "interface.app:app",
        host=args.host,
        port=args.port,
        ws_max_size=UVICORN_WEBSOCKET_MAX_BYTES,
    )


if __name__ == "__main__":
    main()
