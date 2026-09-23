from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional
import importlib.util
import re
import time

StatusCallback = Optional[Callable[[str], None]]

TARGET_MODULES = {"cyberpi", "mbot2", "mbuild", "event"}

BAUDRATE = 115200
FRAME_HEAD = 0xF3
FRAME_END = 0xF4
PROTOCOL_ID = 0x01
DEVICE_ID = 0x00
SERVICE_ID = 0x5E
CMD_FILE_HEADER = 0x01
CMD_FILE_BLOCK = 0x02
CMD_FILE_STATE = 0xF0

# Current mBlock JS uses 80-byte blocks.
BLOCK_SIZE = 80

# Current CyberPi mBlock extension writes normal projects here.
DEFAULT_REMOTE_PATH = "/flash/_xx_main.py"


@dataclass
class UploadResult:
    ok: bool
    message: str
    stdout: str = ""
    stderr: str = ""


def _status(callback: StatusCallback, text: str) -> None:
    if callback:
        callback(text)


def _have_pyserial() -> bool:
    return importlib.util.find_spec("serial") is not None


def target_side_imports(code: str) -> set[str]:
    found: set[str] = set()
    for m in re.finditer(
        r"(?m)^\s*(?:import|from)\s+([A-Za-z_][A-Za-z0-9_]*)",
        code,
    ):
        if m.group(1) in TARGET_MODULES:
            found.add(m.group(1))
    return found


def _sum8(data: bytes | bytearray | list[int]) -> int:
    return sum(data) & 0xFF


def _xor32(data: bytes) -> bytes:
    checksum = [0, 0, 0, 0]
    for i, value in enumerate(data):
        checksum[i & 3] ^= value
    return bytes(checksum)


def _f3f4_frame(payload: bytes) -> bytes:
    length = len(payload)
    lo = length & 0xFF
    hi = (length >> 8) & 0xFF
    header_checksum = (FRAME_HEAD + lo + hi) & 0xFF
    return bytes(
        [FRAME_HEAD, header_checksum, lo, hi]
    ) + payload + bytes([_sum8(payload), FRAME_END])


def _file_header_packet(remote_path: str, content: bytes) -> bytes:
    path = remote_path.encode("utf-8")
    command_body = (
        bytes([0x00])                 # file type
        + len(content).to_bytes(4, "little")
        + _xor32(content)
        + path
    )
    payload = (
        bytes([PROTOCOL_ID, DEVICE_ID, SERVICE_ID, CMD_FILE_HEADER])
        + len(command_body).to_bytes(2, "little")
        + command_body
    )
    return _f3f4_frame(payload)


def _file_block_packet(offset: int, block: bytes) -> bytes:
    command_body = offset.to_bytes(4, "little") + block
    payload = (
        bytes([PROTOCOL_ID, DEVICE_ID, SERVICE_ID, CMD_FILE_BLOCK])
        + len(command_body).to_bytes(2, "little")
        + command_body
    )
    return _f3f4_frame(payload)


def _upload_mode_packet() -> bytes:
    # CyberPi extension's changeToUploadMode() writes [13, 0, 0]
    # through the same f3/f4 transport.
    return _f3f4_frame(bytes([13, 0, 0]))


def _extract_frames(buffer: bytearray) -> list[bytes]:
    frames: list[bytes] = []

    while True:
        try:
            start = buffer.index(FRAME_HEAD)
        except ValueError:
            buffer.clear()
            break

        if start:
            del buffer[:start]

        if len(buffer) < 4:
            break

        recv_header_checksum = buffer[1]
        lo = buffer[2]
        hi = buffer[3]
        expected_header_checksum = (FRAME_HEAD + lo + hi) & 0xFF

        if recv_header_checksum != expected_header_checksum:
            del buffer[0]
            continue

        payload_len = lo | (hi << 8)
        frame_len = 4 + payload_len + 2
        if len(buffer) < frame_len:
            break

        payload = bytes(buffer[4:4 + payload_len])
        recv_checksum = buffer[4 + payload_len]
        end = buffer[5 + payload_len]

        if recv_checksum == _sum8(payload) and end == FRAME_END:
            frames.append(payload)
            del buffer[:frame_len]
        else:
            del buffer[0]

    return frames


def _wait_for_upload_ack(ser, timeout: float = 2.5) -> tuple[bool, int | None, bytes]:
    """
    mBlock waits for f3/f4 payload:
        01 00 5E F0 01 <status> 00
    status 0 means success.
    """
    deadline = time.monotonic() + timeout
    buffer = bytearray()
    raw = bytearray()

    while time.monotonic() < deadline:
        waiting = getattr(ser, "in_waiting", 0)
        chunk = ser.read(waiting if waiting else 1)
        if not chunk:
            continue

        raw.extend(chunk)
        buffer.extend(chunk)

        for payload in _extract_frames(buffer):
            if (
                len(payload) >= 7
                and payload[0] == PROTOCOL_ID
                and payload[1] == DEVICE_ID
                and payload[2] == SERVICE_ID
                and payload[3] == CMD_FILE_STATE
                and payload[4] == 0x01
            ):
                return payload[5] == 0, payload[5], bytes(raw)

    return False, None, bytes(raw)


def _send_with_ack(
    ser,
    packet: bytes,
    *,
    retries: int,
    timeout: float,
) -> tuple[bool, int | None, bytes]:
    last_raw = b""
    last_status: int | None = None

    for _ in range(retries + 1):
        try:
            ser.reset_input_buffer()
        except Exception:
            pass

        ser.write(packet)
        ser.flush()

        ok, status, raw = _wait_for_upload_ack(ser, timeout)
        last_raw = raw
        last_status = status

        if ok:
            return True, status, raw

        # A real non-zero status is a device error; retrying normally won't help.
        if status is not None:
            return False, status, raw

    return False, last_status, last_raw


def _serial_error_message(port: str | None, exc: Exception) -> str:
    text = str(exc)
    lower = text.lower()

    if "access is denied" in lower or "permissionerror" in lower:
        return (
            f"Could not open {port or 'the serial port'} because it is in use.\n"
            "Disconnect mBlock/mLink from CyberPi and try again."
        )

    return f"Could not open {port or 'the serial port'}: {text}"


def test_connection(
    port: str | None = None,
    *,
    status_callback: StatusCallback = None,
) -> UploadResult:
    """
    Test the serial connection used by Makeblock's Firefly upload protocol.

    This intentionally does NOT use mpremote/raw REPL.
    """
    if not _have_pyserial():
        return UploadResult(
            False,
            "pyserial is not installed.\nRun:\npython -m pip install pyserial",
        )

    import serial
    import serial.tools.list_ports

    if port is None:
        ports = list(serial.tools.list_ports.comports())
        if not ports:
            return UploadResult(False, "No serial ports found.")
        port = ports[0].device

    _status(status_callback, f"Opening {port} at {BAUDRATE} baud...")

    try:
        with serial.Serial(port, BAUDRATE, timeout=0.15, write_timeout=2.0) as ser:
            time.sleep(0.15)
            try:
                ser.reset_input_buffer()
            except Exception:
                pass

            # Send the same CyberPi upload-mode command found in the mBlock extension.
            ser.write(_upload_mode_packet())
            ser.flush()
            time.sleep(0.20)

            received = ser.read(getattr(ser, "in_waiting", 0) or 1)

        return UploadResult(
            True,
            (
                f"{port} opened successfully at {BAUDRATE} baud.\n"
                "CyberPi upload-mode command was sent.\n"
                "The definitive communication test is an actual Upload."
            ),
            stdout=("Received: " + received.hex(" ")) if received else "",
        )

    except Exception as exc:
        return UploadResult(False, _serial_error_message(port, exc), stderr=str(exc))


def upload_to_mbot2(
    code: str,
    port: str | None = None,
    *,
    remote_filename: str = DEFAULT_REMOTE_PATH,
    reset_after_upload: bool = True,
    test_first: bool = False,
    status_callback: StatusCallback = None,
) -> UploadResult:
    """
    Upload Python source using Makeblock's Firefly file-transfer protocol.

    Protocol source:
      - mBlock CyberPi extension: firefly_upload driver, f3/f4 transport
      - Makeblock/YanMinge firefly_upload reference implementation

    `reset_after_upload` is retained for API compatibility with the previous
    mpremote uploader. Firefly upload itself does not use mpremote reset.
    """
    del reset_after_upload  # API compatibility only.

    if not code.strip():
        return UploadResult(False, "Generated code is empty.")

    if not _have_pyserial():
        return UploadResult(
            False,
            "pyserial is not installed.\nRun:\npython -m pip install pyserial",
        )

    import serial
    import serial.tools.list_ports

    if port is None:
        ports = list(serial.tools.list_ports.comports())
        if not ports:
            return UploadResult(False, "No serial ports found.")
        port = ports[0].device

    if test_first:
        test = test_connection(port, status_callback=status_callback)
        if not test.ok:
            return test

    # Match normal text-file behavior and current mBlock generated Python.
    content = code.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")
    if not content.endswith(b"\n"):
        content += b"\n"

    libs = target_side_imports(code)
    if libs:
        _status(status_callback, "Using CyberPiOS modules: " + ", ".join(sorted(libs)))

    # Accept old callers that still pass "main.py".
    if not remote_filename.startswith("/"):
        if remote_filename == "main.py":
            remote_filename = DEFAULT_REMOTE_PATH
        else:
            remote_filename = "/flash/" + remote_filename

    _status(status_callback, f"Opening {port}...")

    try:
        with serial.Serial(port, BAUDRATE, timeout=0.10, write_timeout=2.0) as ser:
            time.sleep(0.20)
            try:
                ser.reset_input_buffer()
                ser.reset_output_buffer()
            except Exception:
                pass

            # mBlock's CyberPi extension explicitly switches to upload/offline mode.
            _status(status_callback, "Switching CyberPi to upload mode...")
            ser.write(_upload_mode_packet())
            ser.flush()
            time.sleep(0.25)
            try:
                ser.reset_input_buffer()
            except Exception:
                pass

            _status(status_callback, f"Uploading {remote_filename}...")

            header = _file_header_packet(remote_filename, content)
            ok, status, raw = _send_with_ack(
                ser, header, retries=4, timeout=2.5
            )
            if not ok:
                if status == 0x01:
                    msg = "CyberPi rejected the file header (status 0x01)."
                elif status == 0xF0:
                    msg = "CyberPi reported a file checksum/encoding error (status 0xF0)."
                elif status is None:
                    msg = (
                        "CyberPi did not acknowledge the file header.\n"
                        "Close/disconnect mBlock from the device and try again."
                    )
                else:
                    msg = f"CyberPi rejected the file header (status 0x{status:02X})."
                return UploadResult(False, msg, stderr=raw.hex(" "))

            total = len(content)
            sent = 0

            for offset in range(0, total, BLOCK_SIZE):
                block = content[offset:offset + BLOCK_SIZE]
                packet = _file_block_packet(offset, block)

                ok, status, raw = _send_with_ack(
                    ser, packet, retries=2, timeout=2.5
                )
                if not ok:
                    if status is None:
                        msg = f"No acknowledgement for block at offset {offset}."
                    else:
                        msg = (
                            f"CyberPi rejected block at offset {offset} "
                            f"(status 0x{status:02X})."
                        )
                    return UploadResult(False, msg, stderr=raw.hex(" "))

                sent += len(block)
                percent = int(sent * 100 / total) if total else 100
                _status(status_callback, f"Uploading... {percent}%")

            _status(status_callback, "Upload complete.")
            return UploadResult(
                True,
                (
                    f"Upload complete: {len(content)} bytes written to "
                    f"{remote_filename} using Makeblock Firefly protocol."
                ),
            )

    except Exception as exc:
        return UploadResult(False, _serial_error_message(port, exc), stderr=str(exc))


def upload_button_callback(
    code_getter: Callable[[], str],
    *,
    port: str | None = None,
    status_callback: StatusCallback = None,
) -> UploadResult:
    return upload_to_mbot2(
        code_getter(),
        port=port,
        status_callback=status_callback,
    )
