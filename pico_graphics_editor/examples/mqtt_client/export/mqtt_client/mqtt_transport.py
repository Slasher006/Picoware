"""Small MQTT 3.1.1 transports for the generated MQTT Client test app."""

try:
    import usocket as socket
except ImportError:
    import socket


def _bytes(value):
    """Return a UTF-8 byte string on CPython and MicroPython."""
    return value if isinstance(value, bytes) else str(value).encode("utf-8")


def _mqtt_string(value):
    """Encode one MQTT UTF-8 string."""
    payload = _bytes(value)
    if len(payload) > 0xFFFF:
        raise ValueError("MQTT strings are limited to 65535 bytes")
    return bytes((len(payload) >> 8, len(payload) & 0xFF)) + payload


def _remaining_length(value):
    """Encode MQTT's variable remaining-length field."""
    output = bytearray()
    while True:
        digit = value % 128
        value //= 128
        if value:
            digit |= 0x80
        output.append(digit)
        if not value:
            return bytes(output)


class MockMqttTransport:
    """Deterministic loopback broker used only by the Picoware simulator."""

    is_mock = True

    def __init__(self, callback):
        self.callback = callback
        self.connected = False
        self.subscriptions = []

    def connect(self, host, port, client_id):
        del host, port, client_id
        self.connected = True
        return True

    def disconnect(self):
        self.connected = False

    def subscribe(self, topic):
        if topic not in self.subscriptions:
            self.subscriptions.append(topic)

    def unsubscribe(self, topic):
        if topic in self.subscriptions:
            self.subscriptions.remove(topic)

    def publish(self, topic, payload, retain=False):
        del retain
        if not self.connected:
            raise OSError("MQTT transport is disconnected")
        if topic in self.subscriptions and self.callback is not None:
            self.callback(_bytes(topic), _bytes(payload))

    def poll(self):
        return None


class SocketMqttTransport:
    """Bounded MQTT 3.1.1 QoS-0 client for an already connected Wi-Fi device."""

    is_mock = False

    def __init__(self, callback):
        self.callback = callback
        self.sock = None
        self.packet_id = 0

    def _next_packet_id(self):
        self.packet_id = (self.packet_id % 0xFFFF) + 1
        return self.packet_id

    def _send(self, packet_type, body):
        if self.sock is None:
            raise OSError("MQTT transport is disconnected")
        self.sock.send(bytes((packet_type,)) + _remaining_length(len(body)) + body)

    def _recv_exact(self, count):
        output = bytearray()
        while len(output) < count:
            part = self.sock.recv(count - len(output))
            if not part:
                raise OSError("MQTT broker closed the connection")
            output.extend(part)
        return bytes(output)

    def _recv_packet(self):
        first = self._recv_exact(1)[0]
        multiplier = 1
        remaining = 0
        while True:
            digit = self._recv_exact(1)[0]
            remaining += (digit & 0x7F) * multiplier
            if not digit & 0x80:
                break
            multiplier *= 128
            if multiplier > 128 * 128 * 128:
                raise ValueError("Invalid MQTT remaining length")
        return first, self._recv_exact(remaining)

    def connect(self, host, port, client_id):
        address = socket.getaddrinfo(host, int(port), 0, socket.SOCK_STREAM)[0][-1]
        self.sock = socket.socket()
        self.sock.settimeout(3)
        self.sock.connect(address)
        variable = b"\x00\x04MQTT\x04\x02\x00<"
        self._send(0x10, variable + _mqtt_string(client_id))
        packet_type, body = self._recv_packet()
        if packet_type != 0x20 or len(body) != 2 or body[1] != 0:
            self.disconnect()
            raise OSError("MQTT broker rejected the connection")
        self.sock.settimeout(0)
        return True

    def disconnect(self):
        if self.sock is not None:
            try:
                self._send(0xE0, b"")
            except OSError:
                pass
            try:
                self.sock.close()
            except OSError:
                pass
        self.sock = None

    def subscribe(self, topic):
        packet_id = self._next_packet_id()
        body = bytes((packet_id >> 8, packet_id & 0xFF)) + _mqtt_string(topic) + b"\x00"
        self._send(0x82, body)

    def unsubscribe(self, topic):
        packet_id = self._next_packet_id()
        body = bytes((packet_id >> 8, packet_id & 0xFF)) + _mqtt_string(topic)
        self._send(0xA2, body)

    def publish(self, topic, payload, retain=False):
        body = _mqtt_string(topic) + _bytes(payload)
        self._send(0x31 if retain else 0x30, body)

    def poll(self):
        if self.sock is None:
            return
        try:
            packet_type, body = self._recv_packet()
        except OSError:
            return
        if packet_type >> 4 != 3 or len(body) < 2:
            return
        topic_size = (body[0] << 8) | body[1]
        if topic_size < 1 or 2 + topic_size > len(body):
            return
        topic = body[2 : 2 + topic_size]
        payload = body[2 + topic_size :]
        if self.callback is not None:
            self.callback(topic, payload)


def create_transport(callback, simulator=False):
    """Create the deterministic simulator transport or the device socket client."""
    return MockMqttTransport(callback) if simulator else SocketMqttTransport(callback)
