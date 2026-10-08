"""Minimal client for Unreal's Python remote-execution protocol (design §10).

Protocol: JSON messages {"version": 1, "magic": "ue_py", "type", "source", "dest", "data"}.
Discovery is UDP multicast ping/pong; commands go over a TCP connection that the editor opens
back to us after an "open_connection" message. Mirrors the reference client that ships with
Unreal's PythonScriptPlugin (remote_execution.py).
"""

import json
import socket
import sys
import time
import uuid

PROTOCOL_VERSION = 1
MAGIC = "ue_py"

EXEC_FILE = "ExecuteFile"
EXEC_STATEMENT = "ExecuteStatement"
EVAL_STATEMENT = "EvaluateStatement"


class Config:
    def __init__(self, group="239.0.0.1", port=6766, bind="127.0.0.1", ttl=0, command_port=0):
        self.group = group
        self.port = port
        self.bind = bind
        self.ttl = ttl
        self.command_endpoint = (bind if bind != "0.0.0.0" else "127.0.0.1", command_port)


class RemoteError(RuntimeError):
    pass


class Client:
    def __init__(self, config=None):
        self.config = config or Config()
        self.node_id = str(uuid.uuid4())
        self._udp = None

    # ---------------------------------------------------------------- messages

    def _msg(self, type_, dest=None, data=None):
        m = {"version": PROTOCOL_VERSION, "magic": MAGIC, "type": type_, "source": self.node_id}
        if dest:
            m["dest"] = dest
        if data is not None:
            m["data"] = data
        return json.dumps(m, ensure_ascii=False).encode("utf-8")

    def _parse(self, raw):
        try:
            m = json.loads(raw.decode("utf-8"))
        except ValueError:
            return None
        if m.get("version") != PROTOCOL_VERSION or m.get("magic") != MAGIC:
            return None
        if m.get("source") == self.node_id:
            return None
        if m.get("dest") not in (None, self.node_id):
            return None
        return m

    # ---------------------------------------------------------------- discovery

    def _open_udp(self):
        if self._udp:
            return self._udp
        c = self.config
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, "SO_REUSEPORT"):
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        # Windows wants the interface address; elsewhere binding to it would filter out
        # packets addressed to the multicast group.
        s.bind((c.bind if sys.platform == "win32" else "", c.port))
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, c.ttl)
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(c.bind))
        s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                     socket.inet_aton(c.group) + socket.inet_aton(c.bind))
        s.settimeout(0.1)
        self._udp = s
        return s

    def close(self):
        if self._udp:
            self._udp.close()
            self._udp = None

    def discover(self, timeout=1.0):
        """Ping and collect pongs. Returns {node_id: pong_data}."""
        s = self._open_udp()
        s.sendto(self._msg("ping"), (self.config.group, self.config.port))
        nodes = {}
        end = time.time() + timeout
        while time.time() < end:
            try:
                raw = s.recv(65536)
            except socket.timeout:
                continue
            m = self._parse(raw)
            if m and m["type"] == "pong":
                nodes[m["source"]] = m.get("data", {})
        return nodes

    # ---------------------------------------------------------------- commands

    def run(self, node_id, command, mode=EXEC_STATEMENT, timeout=None):
        """Run `command` on the editor `node_id` and return the command_result data."""
        s = self._open_udp()
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        host, port = self.config.command_endpoint
        listener.bind((host, port))
        host, port = listener.getsockname()
        listener.listen(1)
        listener.settimeout(5.0)
        try:
            s.sendto(self._msg("open_connection", node_id, {"command_ip": host, "command_port": port}),
                     (self.config.group, self.config.port))
            try:
                conn, _ = listener.accept()
            except socket.timeout:
                raise RemoteError("the Unreal editor did not open a command connection")
            conn.settimeout(timeout)
            try:
                conn.sendall(self._msg("command", node_id, {
                    "command": command, "unattended": True, "exec_mode": mode}))
                result = self._recv_json(conn)
            finally:
                try:
                    conn.sendall(self._msg("close_connection", node_id))
                except OSError:
                    pass
                conn.close()
        finally:
            listener.close()
        if result is None or result.get("type") != "command_result":
            raise RemoteError("no result from the Unreal editor")
        return result.get("data", {})

    def _recv_json(self, conn):
        buf = b""
        while True:
            chunk = conn.recv(65536)
            if not chunk:
                return self._parse(buf) if buf else None
            buf += chunk
            m = self._parse(buf)
            if m is not None:
                return m


def output_text(result):
    return "\n".join(o.get("output", "") for o in result.get("output", []))
