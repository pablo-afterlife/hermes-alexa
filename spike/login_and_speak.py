"""Phase 0 spike: can we log in to an Amazon account and make an Echo speak?

Run it from a terminal (it asks for your password and 2FA code):

    bridge\\.venv\\Scripts\\python.exe -I spike\\login_and_speak.py

First run: interactive login (email, password, then a code from your
authenticator app). The session is saved OUTSIDE the repository, in
%LOCALAPPDATA%\\hermes-alexa\\ (it is as sensitive as a password).
Next runs reuse the saved session and skip the password.
"""

from __future__ import annotations

import asyncio
import getpass
import json
import logging
import os
import sys
from pathlib import Path

from aiohttp import ClientSession
from aioamazondevices import __version__ as lib_version
from aioamazondevices.api import AmazonEchoApi
from aioamazondevices.exceptions import AmazonError, CannotAuthenticate
from aioamazondevices.structures import AmazonSaveDataConfig

DATA_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "hermes-alexa"
LOGIN_FILE = DATA_DIR / "login-data.json"

GREETING = "Olá! Aqui é o Hermes, falando pelo hermes-alexa."


def load_login() -> tuple[str, dict | None]:
    if LOGIN_FILE.exists():
        stored = json.loads(LOGIN_FILE.read_text(encoding="utf-8"))
        return stored["email"], stored["login_data"]
    return "", None


def save_login(email: str, login_data: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOGIN_FILE.write_text(
        json.dumps({"email": email, "login_data": login_data}), encoding="utf-8"
    )


async def ask(prompt: str) -> str:
    """input() without blocking the event loop (the library sends in background)."""
    return (await asyncio.to_thread(input, prompt)).strip()


async def flush(api: AmazonEchoApi) -> None:
    """Wait until queued speak/announce commands were actually POSTed.

    The library batches commands for 0.3 s and sends them from a background
    task; exiting (or blocking the loop) before that silently drops them.
    """
    await asyncio.sleep(0.5)
    pending = list(api._sequence_handler._tasks)  # spike only: private attribute
    if pending:
        await asyncio.gather(*pending)


async def main() -> int:
    print(f"aioamazondevices {lib_version}")
    email, login_data = load_login()

    async with ClientSession() as session:
        if login_data:
            print(f"Reusing saved session for {email} ({LOGIN_FILE})")
            api = AmazonEchoApi(
                session, email, "",
                save_data=AmazonSaveDataConfig(path=DATA_DIR),
                login_data=login_data,
            )
            await api.login.login_mode_stored_data()
        else:
            email = input("Amazon email: ").strip()
            password = getpass.getpass("Amazon password (hidden): ")
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            api = AmazonEchoApi(
                session, email, password,
                save_data=AmazonSaveDataConfig(path=DATA_DIR),
            )
            # Ask for the code last: TOTP codes expire in ~30 s.
            otp = input("2FA code from your authenticator app: ").strip()
            login_data = await api.login.login_mode_interactive(otp)
            save_login(email, login_data)
            print(f"Logged in. Session saved to {LOGIN_FILE}")

        print(f"Amazon site: {login_data.get('site')}  |  Alexa domain: {api.domain}")

        devices = await api.get_devices_data()
        echos = [d for d in devices.values() if d.voice_control_supported]
        print(f"\n{len(devices)} devices on the account, {len(echos)} with voice:")
        for i, d in enumerate(echos):
            state = "online" if d.online else "OFFLINE"
            print(f"  [{i}] {d.account_name}  ({d.model or d.device_family}, {state})")

        if not echos:
            print("No Echo found. Is the Echo set up in the Alexa app with this account?")
            return 1

        choice = await ask("\nWhich one should speak? [0]: ") or "0"
        target = echos[int(choice)]

        print(f"Speak -> {target.account_name}")
        await api.call_alexa_speak(target, GREETING)
        await flush(api)
        print("Speak sent.")

        if (await ask("Also test an announcement (chime + text)? [y/N]: ")).lower() == "y":
            await api.call_alexa_announcement(target, "Teste de anúncio do Hermes.")
            await flush(api)
            print("Announcement sent.")

        print("\nDone. Did the Echo talk? Tell Claude what you heard.")
        return 0


if __name__ == "__main__":
    # Show library warnings/errors (e.g. a failed background send) on the console.
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    try:
        sys.exit(asyncio.run(main()))
    except CannotAuthenticate as exc:
        print(f"\nLOGIN FAILED (CannotAuthenticate): {exc}")
        sys.exit(2)
    except AmazonError as exc:
        print(f"\nAMAZON ERROR ({type(exc).__name__}): {exc}")
        sys.exit(3)
