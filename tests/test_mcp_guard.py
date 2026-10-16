import asyncio
import os
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mcp_guard


def call(app, scope):
    # Runs one ASGI call; returns (messages sent, whether the inner app ran).
    sent, ran = [], []

    async def inner(scope, receive, send):
        ran.append(True)
        await send({"type": "http.response.start", "status": 200, "headers": []})

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(message):
        sent.append(message)

    asyncio.run(mcp_guard.guard(inner, 8001)(scope, receive, send) if app is None else app(scope, receive, send))
    return sent, bool(ran)


def http(host):
    headers = [] if host is None else [(b"host", host.encode())]
    return {"type": "http", "method": "POST", "path": "/servers/time/mcp", "headers": headers}


class GuardTests(unittest.TestCase):
    def test_loopback_hosts_reach_the_app(self):
        for host in ("127.0.0.1:8001", "localhost:8001", "LOCALHOST:8001"):
            sent, ran = call(None, http(host))
            self.assertTrue(ran, host)
            self.assertEqual(sent[0]["status"], 200)

    def test_rebound_host_is_refused(self):
        # A DNS-rebinding page sends its own name as Host.
        for host in ("evil.example:8001", "127.0.0.1:8002", "127.0.0.1", "", None):
            sent, ran = call(None, http(host))
            self.assertFalse(ran, host)
            self.assertEqual(sent[0]["status"], 421, host)

    def test_lifespan_passes_through(self):
        sent, ran = call(None, {"type": "lifespan"})
        self.assertTrue(ran)


class InstallTests(unittest.TestCase):
    def test_every_uvicorn_app_gets_the_guard(self):
        # mcp-proxy builds its Starlette app and hands it to uvicorn.Config; that's the only hook.
        class Config:
            def __init__(self, app, host="127.0.0.1", port=8000, **kw):
                self.app, self.port = app, port

        fake = types.ModuleType("uvicorn")
        fake.Config = Config
        with mock.patch.dict(sys.modules, {"uvicorn": fake}):
            mcp_guard.install()
            config = fake.Config(lambda *a: None, host="127.0.0.1", port=8001)
        sent, ran = call(config.app, http("evil.example:8001"))
        self.assertFalse(ran)
        self.assertEqual(sent[0]["status"], 421)


if __name__ == "__main__":
    unittest.main()
