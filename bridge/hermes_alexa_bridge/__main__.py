"""``python -m hermes_alexa_bridge serve|login --data-dir DIR``."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import logging
import sys
from pathlib import Path

from aiohttp import ClientSession

from .amazon import AlexaAccount, AlexaError
from .server import DEFAULT_PORT, serve


async def _login(data_dir: Path) -> int:
    print("Amazon login (your password is not stored; only the session is, in "
          f"{data_dir}).")
    email = input("Amazon email: ").strip()
    password = getpass.getpass("Amazon password (hidden): ")
    # Ask for the code last: authenticator codes expire in ~30 s.
    otp = input("Code from your authenticator app: ").strip()
    account = AlexaAccount(data_dir)
    async with ClientSession() as session:
        await account.login_interactive(session, email, password, otp)
        print(f"Logged in to {account.site}. Echo devices:")
        for d in account.devices():
            print(f"  - {d.name} ({d.model}, {'online' if d.online else 'offline'})")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hermes_alexa_bridge")
    parser.add_argument("command", choices=["serve", "login"])
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if args.command == "serve":
        serve(args.data_dir, args.port)
        return 0
    try:
        return asyncio.run(_login(args.data_dir))
    except AlexaError as exc:
        print(f"Login failed: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - the library raises its own exception types
        print(f"Login failed ({type(exc).__name__}): {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
