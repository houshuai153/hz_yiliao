"""评测期间禁止访问外部网络（回环地址除外），保证评测完全离线。"""
import socket
from contextlib import contextmanager

_ALLOWED = {"127.0.0.1", "::1", "localhost"}


class ExternalNetworkBlocked(OSError):
    pass


@contextmanager
def block_external_network():
    orig_connect, orig_connect_ex = socket.socket.connect, socket.socket.connect_ex

    def _check(address):
        host = address[0] if isinstance(address, tuple) else address
        if host not in _ALLOWED:
            raise ExternalNetworkBlocked(f"评测期间禁止访问外部网络：{host}")

    def connect(self, address):
        _check(address)
        return orig_connect(self, address)

    def connect_ex(self, address):
        _check(address)
        return orig_connect_ex(self, address)

    socket.socket.connect, socket.socket.connect_ex = connect, connect_ex
    try:
        yield
    finally:
        socket.socket.connect, socket.socket.connect_ex = orig_connect, orig_connect_ex
