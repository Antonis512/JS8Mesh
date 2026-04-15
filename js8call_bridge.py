import socket
import time
import json

from pyjs8call import Message


SPEED_NAME_TO_CODE = {
    "NORMAL": 0,
    "FAST": 1,
    "TURBO": 2,
    "SLOW": 4,
    "ULTRA": 8,
}

SPEED_CODE_TO_NAME = {value: key for key, value in SPEED_NAME_TO_CODE.items()}


def normalize_incoming_speed_name(value):
    if value is None:
        return None
    if isinstance(value, str):
        text = str(value).strip().upper()
        if not text:
            return None
        if text.isdigit():
            try:
                return SPEED_CODE_TO_NAME.get(int(text))
            except Exception:
                return None
        for known_name in ("TURBO", "FAST", "NORMAL", "SLOW", "ULTRA"):
            if known_name in text:
                return known_name
        return text if text in SPEED_NAME_TO_CODE else None
    try:
        return SPEED_CODE_TO_NAME.get(int(value))
    except Exception:
        return None


class JS8CallBridgeError(RuntimeError):
    pass


class JS8CallBridge:
    def __init__(self, host="127.0.0.1", port=2442, timeout=2.0):
        self.host = str(host or "127.0.0.1")
        self.port = int(port or 2442)
        self.timeout = float(timeout or 2.0)
        self.sock = None
        self._recv_buffer = ""

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False

    def connect(self):
        if self.sock is not None:
            return
        try:
            self.sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
            self.sock.settimeout(self.timeout)
        except OSError as exc:
            raise JS8CallBridgeError(
                f"Could not connect to JS8Call TCP API at {self.host}:{self.port}. "
                "Make sure JS8Call is running and its TCP API is enabled."
            ) from exc

    def close(self):
        if self.sock is None:
            return
        try:
            self.sock.close()
        except Exception:
            pass
        self.sock = None
        self._recv_buffer = ""

    def _send_message_object(self, msg):
        self.connect()
        try:
            self.sock.sendall(msg.pack())
        except OSError as exc:
            raise JS8CallBridgeError("Failed to send message to JS8Call.") from exc

    def _send_raw_json(self, payload):
        self.connect()
        try:
            packed = (json.dumps(dict(payload or {})) + "\n").encode("utf-8")
            self.sock.sendall(packed)
        except OSError as exc:
            raise JS8CallBridgeError("Failed to send raw command to JS8Call.") from exc

    def _send_request(self, msg_type, params=None):
        self._send_raw_json({"type": msg_type, "params": params or {}})

    def _read_messages_until(self, predicate, timeout=None):
        self.connect()
        deadline = time.time() + (self.timeout if timeout is None else float(timeout))
        while time.time() < deadline:
            if "\n" in self._recv_buffer:
                line, self._recv_buffer = self._recv_buffer.split("\n", 1)
                line = line.strip()
                if not line:
                    continue
                try:
                    parsed = Message().parse(line)
                except Exception:
                    continue
                if predicate(parsed):
                    return parsed

            try:
                data = self.sock.recv(4096)
            except socket.timeout:
                continue
            except OSError as exc:
                raise JS8CallBridgeError("Lost connection to JS8Call TCP API.") from exc

            if not data:
                break
            self._recv_buffer += data.decode("utf-8", errors="ignore")

        return None

    def read_message(self, timeout=None):
        return self._read_messages_until(lambda _msg: True, timeout=timeout)

    def get_speed(self):
        self._send_request(Message.MODE_GET_SPEED)
        response = self._read_messages_until(lambda msg: msg.type == Message.MODE_SPEED, timeout=2.5)
        if response is None:
            return None
        speed = normalize_incoming_speed_name(getattr(response, "speed", ""))
        return speed or None

    def get_rx_text(self):
        self._send_request(Message.RX_GET_TEXT)
        response = self._read_messages_until(lambda msg: msg.type == Message.RX_TEXT, timeout=2.5)
        if response is None:
            return None
        text = getattr(response, "value", None)
        if text is None:
            text = getattr(response, "text", "")
        return str(text or "")

    def set_speed(self, speed_name):
        normalized = str(speed_name or "").strip().upper()
        if normalized not in SPEED_NAME_TO_CODE:
            raise ValueError(f"Unsupported JS8Call speed: {speed_name}")

        self._send_request(Message.MODE_SET_SPEED, {"SPEED": SPEED_NAME_TO_CODE[normalized]})
        time.sleep(0.25)
        confirmed = self.get_speed()
        if confirmed is None:
            return False, "Unable to confirm JS8Call mode after speed-change request."
        return confirmed == normalized, confirmed

    def get_tx_text(self):
        self._send_request(Message.TX_GET_TEXT)
        response = self._read_messages_until(lambda msg: msg.type == Message.TX_TEXT, timeout=2.5)
        if response is None:
            return None
        text = getattr(response, "value", None)
        if text is None:
            text = getattr(response, "text", "")
        return str(text or "")

    def get_selected_call(self):
        self._send_request(Message.RX_GET_SELECTED_CALL)
        response = self._read_messages_until(lambda msg: msg.type == Message.RX_SELECTED_CALL, timeout=2.5)
        if response is None:
            return None
        selected = getattr(response, "call", None)
        if selected is None:
            selected = getattr(response, "value", "")
        selected_text = str(selected or "").strip().upper()
        return selected_text or ""

    def get_dial_frequency(self):
        self._send_request(Message.RIG_GET_FREQ)
        response = self._read_messages_until(lambda msg: msg.type == Message.RIG_FREQ, timeout=2.5)
        if response is None:
            return None
        dial = getattr(response, "dial", None)
        if dial is None or str(dial).strip() == "":
            dial = getattr(response, "freq", None)
        dial_text = str(dial or "").strip()
        return dial_text or None

    def send_text(self, text):
        self._send_raw_json({"type": Message.TX_SEND_MESSAGE, "params": {}, "value": str(text or "")})

    def set_tx_text(self, text):
        self._send_raw_json({"type": Message.TX_SET_TEXT, "params": {}, "value": str(text or "")})
        time.sleep(0.2)
        confirmed = self.get_tx_text()
        if confirmed is None:
            return False
        return str(confirmed).strip() == str(text or "").strip()

    def wait_for_text_cycle(self, timeout_seconds=600, on_tx_started=None):
        deadline = time.time() + max(5.0, float(timeout_seconds or 600))
        saw_non_empty = False
        last_text = ""
        notified_tx_started = False

        while time.time() < deadline:
            current = self.get_tx_text()
            if current is None:
                time.sleep(0.5)
                continue

            current = str(current or "")
            last_text = current

            if current.strip():
                if not saw_non_empty:
                    saw_non_empty = True
                    if callable(on_tx_started) and not notified_tx_started:
                        try:
                            on_tx_started()
                        except Exception:
                            pass
                        notified_tx_started = True
            elif saw_non_empty:
                return {
                    "tx_started": True,
                    "tx_completed": True,
                    "last_text": "",
                }

            time.sleep(0.75)

        return {
            "tx_started": bool(saw_non_empty),
            "tx_completed": False,
            "last_text": last_text,
        }

    def send_text_and_wait(self, text, wait_timeout=600):
        self.send_text(text)
        cycle = self.wait_for_text_cycle(timeout_seconds=wait_timeout)
        if not bool(cycle.get("tx_started")):
            raise JS8CallBridgeError(
                "Timed out waiting for JS8Call to begin transmitting the queued text."
            )
        return True

    def aggressive_send_with_temporary_speed(self, text, target_speed=None):
        requested = str(target_speed or "").strip().upper() or None
        original_speed = self.get_speed()
        switched = False
        switch_attempted = False

        if requested:
            switch_attempted = True
            try:
                self._send_raw_json({"type": Message.MODE_SET_SPEED, "params": {"SPEED": SPEED_NAME_TO_CODE[requested]}})
                switched = True
            except Exception:
                switched = False

        self.send_text(text)
        return {
            "original_speed": original_speed,
            "requested_speed": requested,
            "speed_switch_attempted": switch_attempted,
            "speed_switched": switched,
        }

    def send_text_with_temporary_speed(self, text, target_speed=None, wait_timeout=600, on_tx_started=None):
        result = {
            "requested_speed": str(target_speed or "").strip().upper() or None,
            "original_speed": None,
            "speed_switch_supported": False,
            "speed_switched": False,
            "speed_restored": False,
            "tx_started": False,
            "tx_completed": False,
            "warning": "",
        }

        original_speed = self.get_speed()
        result["original_speed"] = original_speed

        requested = result["requested_speed"]
        if requested and original_speed and requested != original_speed:
            switched, detail = self.set_speed(requested)
            result["speed_switch_supported"] = True
            result["speed_switched"] = bool(switched)
            if not switched:
                result["warning"] = (
                    f"JS8Call stayed on {detail} instead of switching to {requested}."
                )
        elif requested and original_speed == requested:
            result["speed_switch_supported"] = True
            result["speed_switched"] = True

        self.send_text(text)

        def _notify_tx_started():
            result["tx_started"] = True
            if callable(on_tx_started):
                try:
                    on_tx_started(dict(result))
                except Exception:
                    pass

        cycle = self.wait_for_text_cycle(timeout_seconds=wait_timeout, on_tx_started=_notify_tx_started)
        result["tx_started"] = bool(cycle.get("tx_started"))
        result["tx_completed"] = bool(cycle.get("tx_completed"))

        if original_speed and requested and original_speed != requested and result["speed_switched"]:
            restored, detail = self.set_speed(original_speed)
            result["speed_restored"] = bool(restored)
            if not restored and not result["warning"]:
                result["warning"] = (
                    f"Message was sent, but JS8Call mode did not restore cleanly. "
                    f"Current mode appears to be {detail}."
                )

        if not result["tx_started"]:
            raise JS8CallBridgeError(
                "Timed out waiting for JS8Call to begin transmitting the queued text."
            )
        if result["tx_started"] and not result["tx_completed"] and not result["warning"]:
            result["warning"] = (
                "JS8Call began transmitting the queued text, but JS8Mesh could not confirm that the transmission finished cleanly."
            )

        return result
