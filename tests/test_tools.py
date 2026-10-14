"""The agent's web tools (tools.py): which addresses fetch_url will connect to."""

from __future__ import annotations

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpcore
import pytest

from sports_follow import tools

REAL_GETADDRINFO = socket.getaddrinfo


def resolving(monkeypatch, *answers: str) -> list[str]:
    """DNS that answers each lookup of a name with the next of `answers` (the last one from then on), as a
    rebinding name would. Addresses typed as addresses resolve as usual. Returns the names looked up."""
    looked: list[str] = []

    def getaddrinfo(host, port, *args, **kwargs):
        try:
            tools.ipaddress.ip_address(host)
        except ValueError:
            looked.append(host)
            ip = answers[min(len(looked), len(answers)) - 1]
            return [(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]
        return REAL_GETADDRINFO(host, port, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    return looked


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.0.0.5", "172.18.0.4", "169.254.169.254", "100.64.0.1", "0.0.0.0", "::1", "::ffff:127.0.0.1", "fd00::1"])
def test_a_name_that_resolves_to_a_private_address_is_refused(monkeypatch, ip):
    resolving(monkeypatch, ip)
    with pytest.raises(tools.ToolError, match="non-public"):
        tools.fetch_url("http://scores.example/live")


def test_an_address_in_the_url_is_checked_too():
    for url in ("http://127.0.0.1:6380/", "http://[::1]:8421/api/health", "http://169.254.169.254/latest/meta-data/"):
        with pytest.raises(tools.ToolError, match="non-public"):
            tools.fetch_url(url)


def test_the_connection_goes_to_the_address_that_was_checked(monkeypatch):
    # A rebinding name: public when looked up first, this machine from then on. The connection must not
    # look it up again (it used to: httpx resolved the name a second time after the check).
    looked = resolving(monkeypatch, "93.184.216.34", "127.0.0.1")
    connected = []

    def connect_tcp(self, host, port, *args, **kwargs):
        connected.append((host, port))
        raise httpcore.ConnectError("stopped here")

    monkeypatch.setattr(httpcore.SyncBackend, "connect_tcp", connect_tcp)
    with pytest.raises(tools.ToolError, match="fetch failed"):
        tools.fetch_url("http://rebind.example/")
    assert looked == ["rebind.example"]
    assert connected == [("93.184.216.34", 80)]


def test_a_page_is_fetched_by_name_from_the_checked_address(monkeypatch):
    """The whole way through the client: connected to the address, asked for by name."""

    class Echo(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps({"host": self.headers["Host"], "path": self.path}).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Echo)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        resolving(monkeypatch, "127.0.0.1")
        monkeypatch.setattr(tools, "_public", lambda ip: True)  # this test's server is on this machine
        port = server.server_address[1]
        out = tools.fetch_url(f"http://scores.example:{port}/live")
    finally:
        server.shutdown()
    assert out.startswith(f"URL: http://scores.example:{port}/live")
    assert json.loads(out.split("\n\n", 1)[1]) == {"host": f"scores.example:{port}", "path": "/live"}
