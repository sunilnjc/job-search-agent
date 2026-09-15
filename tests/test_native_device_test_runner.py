"""Local-only safety checks: never invoke Xcode or a physical device."""

import importlib.util
from pathlib import Path
import signal
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("device_runner", ROOT / "scripts/native_device_test_runner.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)
spec_counts = importlib.util.spec_from_file_location("sim_runner", ROOT / "scripts/native_test_runner.py")
counts_runner = importlib.util.module_from_spec(spec_counts)
spec_counts.loader.exec_module(counts_runner)


class NativeDeviceRunnerTests(unittest.TestCase):
    def test_apple_silicon_uses_native_tools_even_from_intel_python(self):
        with patch.object(runner.sys, "platform", "darwin"), patch.object(runner.subprocess, "check_output", return_value=b"1\n"):
            self.assertEqual(runner.native_command(["xcodebuild", "build-for-testing"]),
                             ["/usr/bin/arch", "-arm64", "xcodebuild", "build-for-testing"])

    def test_intel_host_and_non_apple_commands_are_not_rewritten(self):
        with patch.object(runner.sys, "platform", "darwin"), patch.object(runner.subprocess, "check_output", return_value=b"0\n"):
            self.assertEqual(runner.native_command(["xcrun", "xcresulttool"]), ["xcrun", "xcresulttool"])
            self.assertEqual(runner.native_command(["python3", "script.py"]), ["python3", "script.py"])
            self.assertEqual(runner.native_command(["xcodegen", "generate"]), ["xcodegen", "generate"])

    def test_simulator_runner_has_no_create_delete_erase_or_uninstall_commands(self):
        source = (ROOT / "scripts/native_test_runner.py").read_text()
        for action in ("create", "delete", "erase", "uninstall"):
            self.assertNotIn('"simctl", "' + action + '"', source)

    def test_accepts_build_and_single_test_commands(self):
        for action in ("build-for-testing", "test-without-building"):
            runner.validate_command(["xcodebuild", action])

    def test_rejects_provisioning_and_signing_changes(self):
        for arg in ("-allowProvisioningUpdates", "-allowProvisioningDeviceRegistration",
                    "DEVELOPMENT_TEAM=other", "CODE_SIGNING_ALLOWED=NO",
                    "PROVISIONING_PROFILE_SPECIFIER=new", "PRODUCT_BUNDLE_IDENTIFIER=new"):
            with self.subTest(arg=arg), self.assertRaises(ValueError):
                runner.validate_command(["xcodebuild", "build-for-testing", arg])

    def test_rejects_device_mutations_and_retries(self):
        for command in (["xcrun", "devicectl", "device", "process", "launch"],
                        ["xcrun", "simctl", "erase", "all"],
                        ["xcodebuild", "test-without-building", "-retry-tests-on-failure"]):
            with self.subTest(command=command), self.assertRaises(ValueError):
                runner.validate_command(command)

    def test_explicit_provisioning_approval_only_allows_build_updates(self):
        runner.validate_command(["xcodebuild", "build-for-testing", "-allowProvisioningUpdates"],
                                allow_provisioning_updates=True)
        for arg in ("-allowProvisioningDeviceRegistration", "-allowProvisioningUpdates=yes",
                    "DEVELOPMENT_TEAM=other", "CODE_SIGNING_ALLOWED=NO",
                    "PROVISIONING_PROFILE_SPECIFIER=new", "PRODUCT_BUNDLE_IDENTIFIER=new"):
            with self.subTest(arg=arg), self.assertRaises(ValueError):
                runner.validate_command(["xcodebuild", "build-for-testing", arg],
                                        allow_provisioning_updates=True)
        for command in (["xcodebuild", "test-without-building", "-allowProvisioningUpdates"],
                        ["xcodegen", "build-for-testing", "-allowProvisioningUpdates"],
                        ["xcodebuild", "build-for-testing", "test-without-building", "-allowProvisioningUpdates"]):
            with self.subTest(command=command), self.assertRaises(ValueError):
                runner.validate_command(command, allow_provisioning_updates=True)

    def test_cancellation_includes_children_after_parent_exit(self):
        process = Mock(pid=987654)
        process.poll.return_value = 0
        with patch.object(runner, "group_alive", side_effect=[True, False]), \
                patch.object(runner.os, "killpg") as kill:
            self.assertTrue(runner.stop_owned_group(process))
            kill.assert_called_once_with(987654, signal.SIGINT)

    def test_does_not_signal_finished_group(self):
        with patch.object(runner, "group_alive", return_value=False), \
                patch.object(runner.os, "killpg") as kill:
            self.assertTrue(runner.stop_owned_group(Mock(pid=987654)))
            kill.assert_not_called()

    def test_runtime_and_cleanup_are_less_than_four_minutes(self):
        self.assertLess(runner.MAX_RUNTIME + sum(grace for _, grace in runner.GRACES), 240)
        with tempfile.TemporaryDirectory() as folder, self.assertRaises(ValueError):
            runner.bounded_run(["xcodebuild", "build-for-testing"], Path(folder), "build", 240)

    def test_existing_attempt_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            (path / "unit.log").touch()
            with patch.object(runner.subprocess, "Popen") as popen, self.assertRaises(FileExistsError):
                runner.bounded_run(["xcodebuild", "test-without-building"], path, "unit", 170)
            popen.assert_not_called()

    def test_synthetic_system_failure_is_not_a_real_case(self):
        tree = {"testNodes": [{"name": "System Failures", "children": [
            {"nodeType": "Test Case", "result": "Failed"}]},
            {"name": "Suite", "children": [{"nodeType": "Test Case", "result": "Passed"},
                                            {"nodeType": "Test Case", "result": "Skipped"}]}]}
        counts = counts_runner.case_counts(tree)
        self.assertEqual(counts["executed"], 1)
        self.assertEqual(counts["passed"], 1)
        self.assertEqual(counts["systemFailures"], 1)
        self.assertEqual(counts["skipped"], 1)


if __name__ == "__main__":
    unittest.main()
