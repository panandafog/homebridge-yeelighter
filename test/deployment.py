import importlib.util
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
import sys
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("controller", Path(__file__).resolve().parents[1] / "scripts/homelab_remote.py")
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)


class DeploymentTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = self.root / "data"
        self.work = self.root / "work"
        self.release = self.work / "releases/candidate"
        self.release.mkdir(parents=True)
        self.backup = self.work / "backups/baseline"
        c.write(self.data / "config.json", {"bridge": {"username": "test", "pin": "test"}})
        c.write(self.data / "package.json", {"dependencies": {c.PLUGIN: "2.15.7", "other-plugin": "1.0.0"}})
        c.write(self.data / "persist/AccessoryInfo.test.json", {"signSk": "private", "signPk": "public", "pairedClients": {"client": "key"}})
        c.write(self.data / "persist/IdentifierCache.test.json", {"cache": {"light|On": 12, "light|nextIID": 13}})
        c.write(self.data / "accessories/cachedAccessories", [{"UUID": "stable-uuid", "plugin": c.PLUGIN, "platform": "Yeelighter", "context": {"device": {"host": "192.0.2.1"}}}])
        c.write(self.data / "node_modules" / c.PLUGIN / "package.json", {"version": "2.15.7"})
        (self.data / "node_modules" / c.PLUGIN / "patched.js").write_text("working local patch")
        c.snapshot(self.data, self.backup)
        self.candidate = self.work / "staging/test/node_modules" / c.PLUGIN
        c.write(self.candidate / "package.json", {"version": "2.16.1-homelab.1"})
        c.write(self.release / "preflight.json", {"passed": True, "stage": "staging/test", "plugin_hashes": c.hashes(self.candidate)})
        c.write(self.release / "release.json", {"archive": "test.tgz"})
        (self.release / "test.tgz").write_bytes(b"tested archive")

    def test_install_and_rollback_keep_pairing_uuids_and_ids(self):
        original = c.identity(self.data)
        result = c.activate(self.data, self.release, self.backup, self.work)
        self.assertTrue(all(result["identity"].values()))
        self.assertEqual(c.read(self.data / "package.json")["dependencies"]["other-plugin"], "1.0.0")
        (self.data / "persist/temporary-test-state").write_text("new state")
        c.write(self.data / "persist/AccessoryInfo.test.json", {"signSk": "changed", "pairedClients": {}})
        restored = c.restore(self.data, self.backup)
        self.assertEqual(restored["restored_version"], "2.15.7")
        self.assertEqual(c.identity(self.data), original)
        self.assertFalse((self.data / "persist/temporary-test-state").exists())
        self.assertEqual((self.data / "node_modules" / c.PLUGIN / "patched.js").read_text(), "working local patch")

    def test_changed_config_is_not_overwritten(self):
        c.write(self.data / "config.json", {"user": "changed configuration"})
        with self.assertRaisesRegex(AssertionError, "Configuration was edited"):
            c.restore(self.data, self.backup)

    def test_corrupt_backup_is_rejected_before_changes(self):
        original = c.hashes(self.data / "node_modules" / c.PLUGIN)
        (self.backup / "plugin.tar.gz").write_bytes(b"corrupt")
        with self.assertRaisesRegex(AssertionError, "checksum"):
            c.restore(self.data, self.backup)
        self.assertEqual(c.hashes(self.data / "node_modules" / c.PLUGIN), original)

    def test_changed_staging_is_rejected(self):
        (self.candidate / "unverified.js").write_text("changed")
        with self.assertRaisesRegex(AssertionError, "Staged package changed"):
            c.activate(self.data, self.release, self.backup, self.work)

    def test_unexpected_identity_is_detected(self):
        before = c.read(self.backup / "identity.json")
        c.write(self.data / "persist/IdentifierCache.test.json", {"cache": {"light|On": 99}})
        self.assertFalse(c.proof(before, c.identity(self.data))["existing_homekit_ids_unchanged"])

    def test_interrupted_restore_puts_previous_files_back(self):
        c.activate(self.data, self.release, self.backup, self.work)
        original = c.hashes(self.data / "node_modules" / c.PLUGIN)
        package = (self.data / "package.json").read_bytes()
        rename = Path.rename
        def fail_once(path, target):
            if path.name == "persist" and "state" in path.parts:
                raise OSError("simulated filesystem failure")
            return rename(path, target)
        with patch.object(Path, "rename", fail_once):
            with self.assertRaisesRegex(OSError, "simulated"):
                c.restore(self.data, self.backup)
        self.assertEqual(c.hashes(self.data / "node_modules" / c.PLUGIN), original)
        self.assertEqual((self.data / "package.json").read_bytes(), package)

    def test_failed_health_check_automatically_rolls_back(self):
        c.write(self.release / "release.json", {"version": "2.16.1-homelab.1"})
        with patch.object(c, "ROOT", self.work), patch.object(sys, "argv", ["controller", "activate", "candidate", "baseline"]), patch.object(c, "context", return_value=("image", "volume")), patch.object(c, "switch") as switch, patch.object(c, "health", side_effect=[RuntimeError("failed startup"), {"version": "2.15.7"}]):
            with self.assertRaisesRegex(RuntimeError, "failed startup"):
                c.main()
        self.assertEqual(switch.call_args_list[0].args[2:], ("activate", ["candidate", "baseline"]))
        self.assertEqual(switch.call_args_list[1].args[2:], ("restore", ["baseline"]))
        self.assertTrue(c.read(self.work / "active.json")["rolled_back"])


if __name__ == "__main__":
    unittest.main()
