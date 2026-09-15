import http.client
from http.server import ThreadingHTTPServer
from pathlib import Path
import socket
import sqlite3
import tempfile
import threading
import unittest
from unittest import mock

from aal.gateway import make_gateway_server
from aal.service import make_server


class ServerShutdownTest(unittest.TestCase):
    def test_idle_connections_expire_during_shutdown(self):
        for kind in ("gateway", "lab"):
            with self.subTest(server=kind), tempfile.TemporaryDirectory() as td:
                db = Path(td) / "state.db"
                server = (make_gateway_server("127.0.0.1", 0, db, Path(td) / "artifacts", {"test-token": "tenant"})
                          if kind == "gateway" else make_server("127.0.0.1", 0, db, Path(td) / "artifacts"))
                accepted, lock = threading.Event(), threading.Lock()
                count = 0
                setup = server.RequestHandlerClass.setup
                def observe_setup(handler):
                    nonlocal count
                    setup(handler)
                    with lock:
                        count += 1
                        if count == 10:
                            accepted.set()
                serve = threading.Thread(target=server.serve_forever, daemon=True)
                closer = threading.Thread(target=server.server_close, daemon=True)
                sockets = []
                with mock.patch.object(server.RequestHandlerClass, "setup", observe_setup), \
                     mock.patch.object(server, "handle_error") as handler_error:
                    serve.start()
                    try:
                        for _ in range(10):
                            sockets.append(socket.create_connection(server.server_address, timeout=5))
                        self.assertTrue(accepted.wait(5))
                        server.shutdown()
                        closer.start()
                        closer.join(15)
                        self.assertFalse(closer.is_alive(), "idle connections prevented shutdown")
                        for conn in sockets:
                            self.assertEqual(conn.recv(1), b"")
                        handler_error.assert_not_called()
                    finally:
                        for conn in sockets:
                            conn.close()
                        server.shutdown()
                        if closer.ident is not None:
                            closer.join(5)
                        else:
                            server.server_close()
                        serve.join(5)

    def test_database_remains_open_until_request_finishes(self):
        for kind in ("gateway", "lab"):
            with self.subTest(server=kind), tempfile.TemporaryDirectory() as td:
                db = Path(td) / "state.db"
                if kind == "gateway":
                    server = make_gateway_server("127.0.0.1", 0, db, Path(td) / "artifacts", {"test-token": "tenant"})
                    store, method = server.gateway_store, "record_event"
                else:
                    server = make_server("127.0.0.1", 0, db, Path(td) / "artifacts")
                    store, method = server.lab.store, "cases"
                entered, release, draining = threading.Event(), threading.Event(), threading.Event()
                errors, responses, open_at_drain = [], [], []
                original_operation = getattr(store, method)
                base_close = ThreadingHTTPServer.server_close

                def blocked_operation(*args, **kwargs):
                    entered.set()
                    if not release.wait(5):
                        raise RuntimeError("test did not release request")
                    return original_operation(*args, **kwargs)

                def observe_drain(instance):
                    open_at_drain.append(store.health_probe())
                    draining.set()
                    base_close(instance)

                def request():
                    conn = http.client.HTTPConnection(*server.server_address, timeout=5)
                    try:
                        if kind == "gateway":
                            conn.request("PATCH", "/v1/jobs", headers={"Authorization": "Bearer test-token"})
                        else:
                            conn.request("GET", "/api/cases")
                        response = conn.getresponse()
                        responses.append((response.status, response.read()))
                    except Exception as exc:
                        errors.append(exc)
                    finally:
                        conn.close()

                serve = threading.Thread(target=server.serve_forever, daemon=True)
                client = threading.Thread(target=request, daemon=True)
                closer = threading.Thread(target=server.server_close, daemon=True)
                with mock.patch.object(store, method, side_effect=blocked_operation), \
                     mock.patch.object(server, "handle_error", side_effect=lambda *args: errors.append("request handler failed")), \
                     mock.patch.object(ThreadingHTTPServer, "server_close", observe_drain):
                    serve.start()
                    client.start()
                    try:
                        self.assertTrue(entered.wait(5), "request never reached database operation")
                        server.shutdown()
                        closer.start()
                        self.assertTrue(draining.wait(5), "close never reached HTTP drain")
                    finally:
                        release.set()
                        server.shutdown()
                        if closer.ident is not None:
                            closer.join(5)
                        else:
                            server.server_close()
                        client.join(5)
                        serve.join(5)
                self.assertFalse(closer.is_alive())
                self.assertFalse(client.is_alive())
                self.assertEqual(open_at_drain, [True], "database closed before HTTP drain")
                self.assertEqual(errors, [])
                self.assertEqual(responses[0][0], 405 if kind == "gateway" else 200)
                self.assertFalse(store.health_probe(), "database must close after draining")
                if kind == "gateway":
                    with sqlite3.connect(db) as readback:
                        self.assertEqual(readback.execute("SELECT COUNT(*) FROM gateway_events WHERE event_type='http_request'").fetchone()[0], 1)
