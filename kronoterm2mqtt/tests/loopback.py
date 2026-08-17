"""Loopback networking for tests.

The test suite calls deny_any_real_request(), which replaces socket.create_connection()
so nothing can talk to the outside world. Tests that start a server of their own and
connect to it on 127.0.0.1 put this implementation back in place while they run.
"""

import socket


def loopback_connection(address, timeout=None, source_address=None):
    host, _port = address
    assert host == '127.0.0.1', f'Only loopback connections are allowed in tests, got {host!r}'
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if timeout is not None:
        sock.settimeout(timeout)
    sock.connect(address)
    return sock
