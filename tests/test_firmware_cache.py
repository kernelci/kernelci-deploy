"""Exercise firmware publication and, optionally, the real nginx cache.

Run with KCI_TEST_NGINX=1 to include Docker-based nginx integration checks.
All downloads use a local HTTP server and small generated archives.
"""

import concurrent.futures
import gzip
import http.server
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.request
import uuid

import yaml


CACHING = Path(__file__).resolve().parents[1] / "kubernetes" / "caching"


def sync_script(filename):
    documents = yaml.safe_load_all((CACHING / filename).read_text())
    cron = next(doc for doc in documents if doc["kind"] == "CronJob")
    return cron["spec"]["jobTemplate"]["spec"]["template"]["spec"][
        "containers"
    ][0]["command"][2]


class Origin(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def reset(self):
        self.body = gzip.compress(b"new firmware" * 100)
        self.etag = '"firmware-v2"'
        self.truncate = False
        self.entered = threading.Event()
        self.release = threading.Event()
        self.release.set()
        self.requests = []


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_GET(self):
        origin = self.server
        body, etag = origin.body, origin.etag
        conditional = self.headers.get("If-None-Match")
        origin.requests.append((self.path, conditional))
        if conditional == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "public, max-age=1")
        self.send_header("ETag", etag)
        self.end_headers()
        halfway = len(body) // 2
        self.wfile.write(body[:halfway])
        self.wfile.flush()
        origin.entered.set()
        if not origin.release.wait(15):
            return
        if not origin.truncate:
            self.wfile.write(body[halfway:])
        self.close_connection = True


class LocalOriginTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.origin = Origin(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.origin.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.origin.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.origin.shutdown()
        cls.origin.server_close()
        cls.thread.join()

    def setUp(self):
        self.origin.reset()


class PublicationTests(LocalOriginTest):
    def setUp(self):
        super().setUp()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name)
        self.dst = self.data / "linux-firmware.tar.gz"
        self.etag = self.data / ".linux-firmware.etag"
        self.old = gzip.compress(b"old firmware")
        self.dst.write_bytes(self.old)
        self.etag.write_text('"firmware-v1"')
        self.script = sync_script("croncache.yaml").replace(
            "/data/", f"{self.data}/"
        ).replace(
            "https://storage.kernelci.org/linux-firmware.tar.gz",
            self.url + "/linux-firmware.tar.gz",
        )
        self.script_path = self.data / "sync.sh"
        self.script_path.write_text(self.script)

    def run_sync(self):
        return subprocess.run(
            ["sh", str(self.script_path)], capture_output=True, text=True,
            timeout=25,
        )

    def assert_preserved(self, result):
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.dst.read_bytes(), self.old)
        self.assertEqual(self.etag.read_text(), '"firmware-v1"')
        self.assertEqual(sorted(p.name for p in self.data.iterdir()), [
            ".linux-firmware.etag", "linux-firmware.tar.gz", "sync.sh"
        ])

    def test_all_manifests_use_same_safe_script(self):
        for filename in ("azurefile-cronjob.yaml", "filestore-pvc.yaml"):
            self.assertEqual(sync_script(filename), sync_script("croncache.yaml"))

    def test_publish_then_revalidate_without_replacing_unchanged_file(self):
        result = self.run_sync()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.dst.read_bytes(), self.origin.body)
        inode = self.dst.stat().st_ino
        result = self.run_sync()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("unchanged", result.stdout)
        self.assertEqual(self.dst.stat().st_ino, inode)
        self.assertEqual(self.origin.requests[-1][1], self.origin.etag)

    def test_interrupted_transfer_preserves_published_file(self):
        self.origin.truncate = True
        self.assert_preserved(self.run_sync())

    def test_corrupt_archive_preserves_published_file(self):
        self.origin.body = self.origin.body[:-8]
        self.assert_preserved(self.run_sync())

    def test_empty_response_preserves_published_file(self):
        self.origin.body = b""
        self.assert_preserved(self.run_sync())

    def test_missing_archive_does_not_reuse_old_etag(self):
        self.dst.unlink()
        self.origin.etag = '"firmware-v1"'
        result = self.run_sync()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.dst.read_bytes(), self.origin.body)
        self.assertIsNone(self.origin.requests[-1][1])

    def test_readers_keep_complete_old_file_during_download_and_rename(self):
        self.origin.release.clear()
        with self.dst.open("rb") as old_reader:
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(self.run_sync)
                try:
                    self.assertTrue(self.origin.entered.wait(5))
                    self.assertEqual(self.dst.read_bytes(), self.old)
                finally:
                    self.origin.release.set()
                result = future.result()
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.dst.read_bytes(), self.origin.body)
            self.assertEqual(old_reader.read(), self.old)


@unittest.skipUnless(os.environ.get("KCI_TEST_NGINX") == "1", "requires Docker")
class NginxTests(LocalOriginTest):
    def setUp(self):
        super().setUp()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        docs = yaml.safe_load_all((CACHING / "tarball-cache.yaml").read_text())
        cm = next(doc for doc in docs if doc["kind"] == "ConfigMap")
        conf = cm["data"]["default.conf"]
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            self.port = sock.getsockname()[1]
        conf = conf.replace("listen 80 default_server", f"listen {self.port}")
        conf = conf.replace("https://files.kernelci.org", self.url)
        conf = conf.replace("https://storage.kernelci.org", self.url)
        path = Path(self.temp.name) / "nginx.conf"
        path.write_text("events {}\nhttp {\n" + conf + "\n}\n")
        self.container = "kci-cache-test-" + uuid.uuid4().hex[:12]
        image = os.environ.get("KCI_NGINX_IMAGE", "nginx:1.28-alpine")
        subprocess.run([
            "docker", "run", "--detach", "--rm", "--name", self.container,
            "--network", "host", "--read-only", "--tmpfs", "/var/cache/nginx",
            "--tmpfs", "/var/run", "--tmpfs", "/var/log/nginx",
            "--mount", f"type=bind,src={path},dst=/etc/nginx/nginx.conf,readonly",
            image, "nginx", "-g", "daemon off;",
        ], check=True, capture_output=True)
        self.addCleanup(subprocess.run, ["docker", "rm", "--force", self.container],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(50):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/healthz"):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            logs = subprocess.run(["docker", "logs", self.container],
                                  capture_output=True, text=True)
            self.fail(logs.stdout + logs.stderr)

    def fetch(self, name):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/{name}") as r:
            return r.read(), r.headers.get("X-Cache")

    def test_cached_tarballs_revalidate_and_retrieve_changed_content(self):
        for name in ("linux-firmware.tar.gz", "source.tar.gz"):
            with self.subTest(name=name):
                body, status = self.fetch(name)
                self.assertEqual(body, self.origin.body)
                self.assertEqual(status, "MISS")
                self.assertEqual(self.fetch(name)[1], "HIT")
                time.sleep(2)
                self.assertEqual(self.fetch(name), (body, "REVALIDATED"))
                self.assertEqual(self.origin.requests[-1], ("/" + name, self.origin.etag))
                self.origin.etag = '"firmware-v3-' + name + '"'
                self.origin.body = gzip.compress(name.encode() * 200)
                time.sleep(2)
                changed, status = self.fetch(name)
                self.assertEqual(changed, self.origin.body)
                self.assertEqual(status, "EXPIRED")

    def test_simultaneous_firmware_requests_share_complete_download(self):
        self.origin.release.clear()
        with concurrent.futures.ThreadPoolExecutor() as pool:
            first = pool.submit(self.fetch, "linux-firmware.tar.gz")
            try:
                self.assertTrue(self.origin.entered.wait(5))
                second = pool.submit(self.fetch, "linux-firmware.tar.gz")
                time.sleep(0.2)
            finally:
                self.origin.release.set()
            results = [first.result(), second.result()]
        self.assertEqual(len(self.origin.requests), 1)
        self.assertEqual([body for body, _status in results], [self.origin.body] * 2)


if __name__ == "__main__":
    unittest.main()
