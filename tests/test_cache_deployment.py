"""Offline checks for rendering and rolling the shared HTTP cache."""

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import yaml


CACHING = Path(__file__).resolve().parents[1] / "kubernetes" / "caching"


class CacheDeploymentTests(unittest.TestCase):
    def render(self, context, script=None):
        result = subprocess.run([
            "bash", str(script or CACHING / "deploy-tarball-cache.sh"),
            "render", context,
        ], capture_output=True, text=True, check=True)
        return list(yaml.safe_load_all(result.stdout))

    def test_supported_clusters_render_http_cache_without_legacy_resources(self):
        contexts = {
            "gke_android-kernelci-external_europe-west1-d_kci-eu-west1": "standard-rwo",
            "gke_android-kernelci-external_europe-west4-c_kci-eu-west4": "standard-rwo",
            "gke_android-kernelci-external_us-central1-c_kci-us-central1": "standard-rwo",
            "gke_android-kernelci-external_us-east4-c_kci-big-us-east4": "standard-rwo",
            "gke_android-kernelci-external_us-west1-a_kci-us-west1": "standard-rwo",
            "aks-kbuild-scus-1": "managed-csi",
            "aks-kbuild-medium-1": "managed-csi",
        }
        for context, storage_class in contexts.items():
            with self.subTest(context=context):
                docs = self.render(context)
                self.assertEqual({doc["metadata"]["name"] for doc in docs},
                                 {"tarball-cache", "tarball-cache-nginx"})
                pvc = next(doc for doc in docs if doc["kind"] == "PersistentVolumeClaim")
                self.assertEqual(pvc["spec"]["storageClassName"], storage_class)

    def test_nginx_config_change_changes_pod_rollout_annotation(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            script = directory / "deploy-tarball-cache.sh"
            manifest = directory / "tarball-cache.yaml"
            shutil.copyfile(CACHING / script.name, script)
            shutil.copyfile(CACHING / manifest.name, manifest)

            def annotations():
                docs = self.render("aks-kbuild-scus-1", script)
                deploy = next(doc for doc in docs if doc["kind"] == "Deployment")
                return deploy["spec"]["template"]["metadata"]["annotations"]

            before = annotations()
            manifest.write_text(manifest.read_text().replace(
                "proxy_cache_valid 200 10m", "proxy_cache_valid 200 5m"
            ))
            self.assertNotEqual(before, annotations())

    def test_unknown_cluster_fails_before_any_kubectl_call(self):
        result = subprocess.run([
            "bash", str(CACHING / "deploy-tarball-cache.sh"), "render", "unknown"
        ], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("Unsupported context", result.stderr)

    def test_old_entry_point_cannot_recreate_nfs(self):
        for arguments in ([], ["fetch"], ["update"]):
            with self.subTest(arguments=arguments):
                result = subprocess.run([
                    "bash", str(CACHING / "deploy-cacher.sh"), *arguments
                ], capture_output=True, text=True)
                self.assertEqual(result.returncode, 2)
                self.assertIn("migration" if not arguments else "retired", result.stderr)

    def test_old_entry_point_can_render_new_http_cache(self):
        result = subprocess.run([
            "bash", str(CACHING / "deploy-cacher.sh"), "render", "aks-kbuild-scus-1"
        ], capture_output=True, text=True, check=True)
        self.assertEqual(list(yaml.safe_load_all(result.stdout)),
                         self.render("aks-kbuild-scus-1"))


if __name__ == "__main__":
    unittest.main()
