"""Exercise real TLS validation on loopback with temporary, test-only certificates."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
import os
from pathlib import Path
import shutil
import ssl
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

from bountyos.connectors import ConnectorError, HTTP, RateLimited


class TLSHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b'{"ok":true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class TLSVerificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.openssl = shutil.which("openssl")
        if not cls.openssl:
            raise unittest.SkipTest("OpenSSL CLI is required to generate temporary TLS test certificates.")
        cls.temp = tempfile.TemporaryDirectory(prefix="bountyos-tls-tests-")
        cls.addClassCleanup(cls.temp.cleanup)
        cls.root = Path(cls.temp.name)
        cls.empty_ca_dir = cls.root / "empty-ca-dir"
        cls.empty_ca_dir.mkdir()
        for name in ("certifi", "system", "unknown"):
            cls.make_ca(name)
            cls.make_leaf(name, name, "DNS:localhost,IP:127.0.0.1")
        cls.make_leaf("wrong-host", "certifi", "DNS:wrong-host.invalid")

    @classmethod
    def openssl_run(cls, *args):
        result = subprocess.run(
            [cls.openssl, *map(str, args)], cwd=cls.root,
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode:
            raise AssertionError(f"Could not generate TLS test certificate: {result.stderr}")

    @classmethod
    def make_ca(cls, name):
        config = cls.root / f"{name}-ca.cnf"
        config.write_text(
            "[req]\nprompt = no\ndistinguished_name = dn\nx509_extensions = ca\n"
            f"[dn]\nCN = BountyOS temporary {name} test CA\n"
            "[ca]\nbasicConstraints = critical,CA:TRUE\n"
            "keyUsage = critical,keyCertSign,cRLSign\n"
            "subjectKeyIdentifier = hash\n",
            encoding="ascii",
        )
        cls.openssl_run(
            "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-sha256", "-days", "2",
            "-keyout", f"{name}-ca.key", "-out", f"{name}-ca.pem", "-config", config,
        )

    @classmethod
    def make_leaf(cls, name, issuer, san):
        config = cls.root / f"{name}-leaf.cnf"
        config.write_text(
            "[req]\nprompt = no\ndistinguished_name = dn\n"
            "[dn]\nCN = localhost\n"
            "[server]\nbasicConstraints = critical,CA:FALSE\n"
            "keyUsage = critical,digitalSignature,keyEncipherment\n"
            "extendedKeyUsage = serverAuth\n"
            "subjectKeyIdentifier = hash\nauthorityKeyIdentifier = keyid,issuer\n"
            f"subjectAltName = {san}\n",
            encoding="ascii",
        )
        cls.openssl_run(
            "req", "-new", "-newkey", "rsa:2048", "-nodes", "-sha256",
            "-keyout", f"{name}-leaf.key", "-out", f"{name}-leaf.csr", "-config", config,
        )
        cls.openssl_run(
            "x509", "-req", "-in", f"{name}-leaf.csr", "-CA", f"{issuer}-ca.pem",
            "-CAkey", f"{issuer}-ca.key", "-set_serial", "101" if name == "wrong-host" else "100",
            "-days", "2", "-sha256", "-extfile", config, "-extensions", "server",
            "-out", f"{name}-leaf.pem",
        )

    @contextmanager
    def serve(self, certificate):
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(
            self.root / f"{certificate}-leaf.pem", self.root / f"{certificate}-leaf.key",
        )
        server = HTTPServer(("127.0.0.1", 0), TLSHandler)
        server.socket = context.wrap_socket(server.socket, server_side=True)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()
        try:
            yield f"https://127.0.0.1:{server.server_port}/"
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    @contextmanager
    def transport(self):
        # Keep two distinct trust sources: the default SSL context loads the
        # system test root, and certifi contributes a separate test root.
        with patch.dict(os.environ, {
            "SSL_CERT_FILE": str(self.root / "system-ca.pem"),
            "SSL_CERT_DIR": str(self.empty_ca_dir),
        }), patch("certifi.where", return_value=str(self.root / "certifi-ca.pem")):
            yield HTTP(local=True, min_interval=0)

    def test_accepts_root_added_from_certifi(self):
        with self.transport() as transport, self.serve("certifi") as url:
            self.assertEqual(transport.json(url, timeout=3), {"ok": True})

    def test_preserves_system_trust_when_certifi_root_is_added(self):
        with self.transport() as transport, self.serve("system") as url:
            self.assertEqual(transport.json(url, timeout=3), {"ok": True})

    def test_rejects_unknown_issuer_and_retains_reason_during_cooldown(self):
        with self.transport() as transport, self.serve("unknown") as url:
            with self.assertRaises(ConnectorError) as failed:
                transport.get(url, timeout=3)
            self.assertIn("certifikat", str(failed.exception).casefold())
            with self.assertRaises(RateLimited) as paused:
                transport.get(url, timeout=3)
            self.assertIn(str(failed.exception), str(paused.exception))

    def test_rejects_wrong_hostname_despite_trusted_issuer(self):
        with self.transport() as transport, self.serve("wrong-host") as url:
            with self.assertRaises(ConnectorError) as failed:
                transport.get(url, timeout=3)
            self.assertIn("certifikat", str(failed.exception).casefold())


if __name__ == "__main__":
    unittest.main()
