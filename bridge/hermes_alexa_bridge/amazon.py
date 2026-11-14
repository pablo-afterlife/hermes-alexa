"""The Amazon side: one logged-in account, its Echo devices, and the actions we expose.

Everything that touches ``aioamazondevices`` lives here, so a library upgrade (or an
Amazon change) is contained in one module.
"""

from __future__ import annotations

import asyncio
import json
import logging
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aiohttp import ClientSession
from aioamazondevices import __version__ as LIB_VERSION
from aioamazondevices.api import AmazonEchoApi
from aioamazondevices.const.metadata import SEQUENCE_BATCH_DELAY
from aioamazondevices.structures import AmazonDevice, AmazonSaveDataConfig

LOGIN_FILE = "login-data.json"
DEVICE_MAP_FILE = "skill-devices.json"
HISTORY_SLACK_MS = 60_000
_LIB_LOGGER = "aioamazondevices"


class AlexaError(Exception):
    """An action failed; the message is safe to show to the user/agent."""


class NotLoggedIn(AlexaError):
    pass


@dataclass(frozen=True)
class Device:
    name: str
    serial: str
    model: str
    online: bool

    @classmethod
    def from_amazon(cls, d: AmazonDevice) -> Device:
        return cls(d.account_name, d.serial_number, d.model or d.device_family, d.online)


def _norm(text: str) -> str:
    """Case- and accent-insensitive key: 'Échô  Dot' -> 'echo dot'."""
    decomposed = unicodedata.normalize("NFKD", text)
    plain = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(plain.casefold().split())


def pick_device(devices: list[AmazonDevice], wanted: str | None) -> AmazonDevice:
    """Resolve a device by serial, exact name, or unique partial name.

    With no name: the only voice device, or the only online one.
    """
    if not devices:
        raise AlexaError("No Echo devices on this Amazon account.")
    names = ", ".join(d.account_name for d in devices)
    if not wanted:
        online = [d for d in devices if d.online]
        for pool in (devices, online):
            if len(pool) == 1:
                return pool[0]
        raise AlexaError(f"Several Echo devices; say which one: {names}")

    key = _norm(wanted)
    for d in devices:
        if d.serial_number == wanted or _norm(d.account_name) == key:
            return d
    partial = [d for d in devices if key in _norm(d.account_name)]
    if len(partial) == 1:
        return partial[0]
    if partial:
        raise AlexaError(f"'{wanted}' matches several devices: "
                         + ", ".join(d.account_name for d in partial))
    raise AlexaError(f"No Echo named '{wanted}'. Devices: {names}")


class _ErrorCollector(logging.Handler):
    """Collects the library's ERROR logs: background sends report failures only there."""

    def __init__(self) -> None:
        super().__init__(level=logging.ERROR)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        msg = record.getMessage()
        if record.exc_info and record.exc_info[1]:
            msg = f"{msg}: {record.exc_info[1]}"
        self.messages.append(msg)


class AlexaAccount:
    """One Amazon account, logged in from the session saved in ``data_dir``."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)
        self._api: AmazonEchoApi | None = None
        self._login_data: dict[str, Any] | None = None
        self._saved_snapshot = ""
        self._email = ""
        self._devices: list[AmazonDevice] = []
        self._send_lock = asyncio.Lock()

    # -- session --------------------------------------------------------------------------

    @property
    def login_file(self) -> Path:
        return self.data_dir / LOGIN_FILE

    @property
    def logged_in(self) -> bool:
        return self._api is not None

    @property
    def site(self) -> str | None:
        return (self._login_data or {}).get("site")

    def _new_api(self, session: ClientSession, email: str, password: str,
                 login_data: dict[str, Any] | None) -> AmazonEchoApi:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        return AmazonEchoApi(session, email, password,
                             save_data=AmazonSaveDataConfig(path=self.data_dir),
                             login_data=login_data)

    async def start(self, session: ClientSession) -> None:
        """Log in from the saved session. Raises NotLoggedIn if there is none."""
        if not self.login_file.exists():
            raise NotLoggedIn("No Amazon session yet. Run: hermes alexa login")
        stored = json.loads(self.login_file.read_text(encoding="utf-8"))
        self._email, self._login_data = stored["email"], stored["login_data"]
        self._saved_snapshot = json.dumps(self._login_data, sort_keys=True)
        api = self._new_api(session, self._email, "", self._login_data)
        await api.login.login_mode_stored_data()
        self._api = api
        await self.refresh_devices()

    async def login_interactive(self, session: ClientSession, email: str, password: str,
                                otp_code: str) -> None:
        api = self._new_api(session, email, password, None)
        self._login_data = await api.login.login_mode_interactive(otp_code)
        self._email, self._api = email, api
        self._persist()
        await self.refresh_devices()

    def _persist(self) -> None:
        """Save the session when the library changed it (it refreshes tokens in place)."""
        snapshot = json.dumps(self._login_data, sort_keys=True)
        if snapshot == self._saved_snapshot:
            return
        self.data_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.login_file.with_suffix(".tmp")
        tmp.write_text(json.dumps({"email": self._email, "login_data": self._login_data}),
                       encoding="utf-8")
        tmp.replace(self.login_file)
        self._saved_snapshot = snapshot

    def _require_api(self) -> AmazonEchoApi:
        if self._api is None:
            raise NotLoggedIn("Not logged in to Amazon. Run: hermes alexa login")
        return self._api

    # -- devices --------------------------------------------------------------------------

    async def refresh_devices(self) -> list[Device]:
        devices = await self._require_api().get_devices_data()
        self._devices = [d for d in devices.values() if d.voice_control_supported]
        self._persist()
        return [Device.from_amazon(d) for d in self._devices]

    def devices(self) -> list[Device]:
        return [Device.from_amazon(d) for d in self._devices]

    def device(self, wanted: str | None) -> AmazonDevice:
        self._require_api()  # "log in" beats "no devices" when there is no session
        return pick_device(self._devices, wanted)

    # -- which Echo talked to the skill ------------------------------------------------------

    @property
    def device_map_file(self) -> Path:
        return self.data_dir / DEVICE_MAP_FILE

    def _device_map(self) -> dict[str, str]:
        try:
            return json.loads(self.device_map_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _remember(self, key: str, serial: str) -> None:
        known = self._device_map()
        if known.get(key) == serial:
            return
        known[key] = serial
        self.data_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.device_map_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(known, indent=2), encoding="utf-8")
        tmp.replace(self.device_map_file)

    async def resolve_skill_device(self, key: str, since_ms: int | None = None) -> Device:
        """The Echo behind a skill request's device id (``key`` is the plugin's hash of it).

        Skill requests carry an opaque id that no Amazon API maps to a serial. Once known, the
        pair is remembered. Otherwise: the only Echo, or the one with the latest voice record
        in Alexa's history (the one that just spoke to the skill), at or after ``since_ms``.
        """
        self._require_api()
        by_serial = {d.serial_number: d for d in self._devices}
        target = by_serial.get(self._device_map().get(key, ""))
        if target is None and len(self._devices) == 1:
            target = self._devices[0]
        if target is None:
            history = await self._require_api().sync_history_state()
            heard = [(rec.timestamp, serial) for serial, rec in history.items()
                     if serial in by_serial
                     and (since_ms is None or rec.timestamp >= since_ms - HISTORY_SLACK_MS)]
            if not heard:
                raise AlexaError("Could not tell which Echo that was yet.")
            target = by_serial[max(heard)[1]]
        self._remember(key, target.serial_number)
        return Device.from_amazon(target)

    # -- actions --------------------------------------------------------------------------

    async def _send(self, call: Any) -> None:
        """Run one library command and wait until it really left for Amazon.

        The library batches commands for SEQUENCE_BATCH_DELAY and POSTs them from a
        background task, reporting failures only to its logger. We wait for that task and
        turn its error logs into an exception, so callers never get a silent no-op.
        """
        api = self._require_api()
        collector = _ErrorCollector()
        lib_logger = logging.getLogger(_LIB_LOGGER)
        async with self._send_lock:
            lib_logger.addHandler(collector)
            try:
                await call
                await asyncio.sleep(SEQUENCE_BATCH_DELAY + 0.1)
                pending = list(api._sequence_handler._tasks)  # pinned lib version, see tests
                if pending:
                    await asyncio.gather(*pending)
            finally:
                lib_logger.removeHandler(collector)
        if collector.messages:
            raise AlexaError("Amazon rejected the command: " + "; ".join(collector.messages))
        self._persist()

    async def speak(self, text: str, device: str | None = None) -> Device:
        target = self.device(device)
        await self._send(self._require_api().call_alexa_speak(target, text))
        return Device.from_amazon(target)

    async def announce(self, text: str, device: str | None = None) -> Device:
        target = self.device(device)
        await self._send(self._require_api().call_alexa_announcement(target, text))
        return Device.from_amazon(target)

    async def command(self, text: str, device: str | None = None) -> Device:
        target = self.device(device)
        await self._send(self._require_api().call_alexa_text_command(target, text))
        return Device.from_amazon(target)

    async def set_volume(self, volume: int, device: str | None = None) -> Device:
        if not 0 <= volume <= 100:
            raise AlexaError("Volume must be between 0 and 100.")
        target = self.device(device)
        await self._send(self._require_api().set_device_volume(target, volume))
        return Device.from_amazon(target)

    async def routines(self) -> list[str]:
        api = self._require_api()
        await api.update_routines()
        return sorted(api.routines)

    async def run_routine(self, name: str) -> str:
        """Routines are account-wide; Amazon runs them on the account's default device."""
        names = await self.routines()
        key = _norm(name)
        match = [r for r in names if _norm(r) == key] or [r for r in names if key in _norm(r)]
        if len(match) != 1:
            raise AlexaError(f"Routine '{name}' not found or ambiguous. Routines: "
                             + (", ".join(names) or "none"))
        await self._send(self._require_api().call_routine(match[0]))
        return match[0]
