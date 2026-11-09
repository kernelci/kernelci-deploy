#!/usr/bin/env python3
"""Regression checks for package upgrades, test isolation and rejected reloads.

python3 tests/test_fingerprint_operations.py
python3 tests/test_fingerprint_operations.py --nginx /usr/sbin/nginx
The real nginx checks must run as an unprivileged user.
"""
import argparse
import http.client
import importlib.util
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import tempfile
import time
import unittest
from unittest import mock

FILES = Path(__file__).resolve().parents[1] / "roles/nginx/files"


def load(name):
    spec = importlib.util.spec_from_file_location(name, FILES / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


guard = load("connfp-upgrade-guard")
reload = load("reload-nginx-checked")
runner = load("run-connfp-tests")
NGINX = shutil.which("nginx")


class UpgradeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name, value in (("LOAD_CONFIG", self.root / "load.conf"),
                            ("LOG_CONFIG", self.root / "http.conf"),
                            ("BACKUPS", self.root / "backups")):
            patch = mock.patch.object(guard, name, value)
            patch.start()
            self.addCleanup(patch.stop)
        guard.LOAD_CONFIG.write_text("load_module /modules/ngx_http_connfp_module-1.22.1-9+deb12u10-rev-hash.so;\n")
        guard.LOG_CONFIG.write_text("tcp_save_syn on;\n")

    def package(self, version, name="nginx"):
        directory = self.root / f"{name}-{version}"
        (directory / "DEBIAN").mkdir(parents=True)
        (directory / "DEBIAN/control").write_text(
            f"Package: {name}\nVersion: {version}\nArchitecture: all\n"
            "Maintainer: KernelCI <kernelci@example.invalid>\nDescription: metadata-only test fixture\n")
        archive = directory.with_suffix(".deb")
        subprocess.run(["dpkg-deb", "--build", "--root-owner-group", str(directory), str(archive)],
                       check=True, stdout=subprocess.DEVNULL)
        return str(archive) + "\n"

    def test_security_update_preserves_configuration(self):
        guard.inspect_packages([self.package("1.22.1-9+deb12u11")])
        self.assertTrue(guard.LOAD_CONFIG.exists())
        self.assertTrue(guard.LOG_CONFIG.exists())

    def test_upstream_upgrade_disables_both_fragments_before_install(self):
        guard.inspect_packages([self.package("1:1.26.3-1~bpo12+1")])
        self.assertFalse(guard.LOAD_CONFIG.exists())
        self.assertFalse(guard.LOG_CONFIG.exists())
        self.assertEqual(len(list(guard.BACKUPS.iterdir())), 2)
        # Repeated hook invocations are harmless while capture is disabled.
        guard.inspect_packages(["/does-not-exist.deb\n"])

    def test_unrelated_package_preserves_configuration(self):
        guard.inspect_packages([self.package("2.0-1", "unrelated")])
        self.assertTrue(guard.LOAD_CONFIG.exists())

    def test_unknown_load_format_disables_conservatively(self):
        guard.LOAD_CONFIG.write_text("load_module /unknown-module.so;\n")
        guard.inspect_packages([self.package("1.22.1-9+deb12u11")])
        self.assertFalse(guard.LOAD_CONFIG.exists())
        self.assertFalse(guard.LOG_CONFIG.exists())

    def test_post_hook_disables_installed_version_mismatch(self):
        result = subprocess.CompletedProcess([], 0, stdout="", stderr="nginx version: nginx/1.26.3\n")
        with mock.patch.object(guard.subprocess, "run", return_value=result):
            guard.check_installed()
        self.assertFalse(guard.LOAD_CONFIG.exists())
        self.assertFalse(guard.LOG_CONFIG.exists())

    def test_partial_disable_failure_restores_original_files(self):
        original = Path.rename

        def rename(path, destination):
            if path == guard.LOAD_CONFIG:
                raise PermissionError("simulated failure")
            return original(path, destination)

        with mock.patch.object(Path, "rename", rename):
            with self.assertRaises(PermissionError):
                guard.disable("test")
        self.assertTrue(guard.LOAD_CONFIG.exists())
        self.assertEqual(guard.LOG_CONFIG.read_text(), "tcp_save_syn on;\n")


class IsolationTests(unittest.TestCase):
    def test_root_is_rejected_before_reading_the_test_source(self):
        with mock.patch.object(runner.os, "geteuid", return_value=0):
            with self.assertRaisesRegex(RuntimeError, "as root"):
                runner.validate("/nonexistent")

    def test_missing_temp_path_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "integration.py"
            path.write_text("    {log_format}\n")
            with mock.patch.object(runner.os, "geteuid", return_value=1000):
                with self.assertRaisesRegex(RuntimeError, "private temp path"):
                    runner.validate(path)


class RealReloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if NGINX is None:
            raise unittest.SkipTest("Pass --nginx to exercise real worker reloads")
        if os.geteuid() == 0:
            raise RuntimeError("Real nginx tests must not run as root")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="connfp-reload-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            self.port = sock.getsockname()[1]
        self.conf = self.root / "nginx.conf"
        self.pid = self.root / "nginx.pid"
        self.valid = f"""daemon off;
pid {self.pid};
error_log {self.root}/error.log notice;
events {{ worker_connections 128; }}
http {{
client_body_temp_path {self.root}/body;
proxy_temp_path {self.root}/proxy;
fastcgi_temp_path {self.root}/fastcgi;
uwsgi_temp_path {self.root}/uwsgi;
scgi_temp_path {self.root}/scgi;
access_log off;
server {{ listen 127.0.0.1:{self.port}; return 200 'ok'; }}
}}
"""
        self.conf.write_text(self.valid)
        self.process = subprocess.Popen([NGINX, "-p", str(self.root), "-c", str(self.conf)],
                                        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        self.addCleanup(self.stop)
        for _ in range(100):
            if self.process.poll() is not None:
                self.fail(self.process.stderr.read().decode())
            try:
                self.request()
                return
            except OSError:
                time.sleep(0.05)
        self.fail("isolated nginx did not become ready")

    def request(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=2)
        try:
            connection.request("GET", "/")
            response = connection.getresponse()
            self.assertEqual((response.status, response.read()), (200, b"ok"))
        finally:
            connection.close()

    def stop(self):
        if self.process.poll() is None:
            self.process.send_signal(signal.SIGQUIT)
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        self.process.stderr.close()

    def test_valid_reload_establishes_new_workers(self):
        before = reload.workers(self.process.pid)
        reload.reload_checked(self.pid, 5, send_signal=True)
        self.assertTrue(reload.workers(self.process.pid) - before)
        self.request()

    def test_nginx_t_success_does_not_mask_master_rejection_and_restore(self):
        with socket.socket() as occupied:
            occupied.bind(("127.0.0.1", 0))
            occupied.listen()
            port = occupied.getsockname()[1]
            self.conf.write_text(self.valid.replace("access_log off;", f"access_log off; server {{ listen 127.0.0.1:{port}; }}"))
            subprocess.run([NGINX, "-t", "-p", str(self.root), "-c", str(self.conf)],
                           check=True, capture_output=True)
            with self.assertRaisesRegex(RuntimeError, "stable new worker generation"):
                reload.reload_checked(self.pid, 5, send_signal=True)
            self.assertIn("bind()", (self.root / "error.log").read_text())
            self.request()  # The old generation still serves requests.
        self.conf.write_text(self.valid)
        reload.reload_checked(self.pid, 5, send_signal=True)
        self.request()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--nginx", default=NGINX)
    args, remaining = parser.parse_known_args()
    NGINX = args.nginx
    unittest.main(argv=[__file__, *remaining], verbosity=2)
