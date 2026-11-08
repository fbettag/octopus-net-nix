import copy
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import octopus_net as manager


UUID = "00000000-0000-4000-8000-000000000001"
SERIAL = "EXAMPLE-001"
IDENTITY = (
    f"<root><UDN>uuid:{UUID}</UDN><serialNumber>{SERIAL}</serialNumber>"
    "<X_SATIPCAP>DVBS2-2</X_SATIPCAP></root>"
)


def profile():
    return {
        "host": "192.0.2.1",
        "expectedUuid": UUID,
        "expectedSerial": SERIAL,
        "expectedCapability": "DVBS2-2",
        "firmwareVersion": "octonet-pro-2.3.1",
        "username": "admin",
        "tuners": [{"adapter": "0", "frontend": "0", "enable": "true"}],
    }


def tuner_html(enabled=False):
    checked = " checked" if enabled else ""
    return (
        '<form class="settings"><input name="adapter" value="0">'
        '<input name="frontend" value="0">'
        f'<input name="enable" type="checkbox" value="true"{checked}>'
        '<input name="vendor" value="private-unmanaged-setting"></form>'
    )


class FakeSession:
    def __init__(self, identity=IDENTITY, firmware="octonet-pro-2.3.1",
                 enabled=False, persist=True, initial=False):
        self.identity = identity
        self.firmware = firmware
        self.enabled = enabled
        self.persist = persist
        self.initial = initial
        self.requests = []

    def open(self, request, timeout):
        self.requests.append(request)
        path = manager.urllib.parse.urlsplit(request.full_url).path
        if path == "/octoserve/octonet.xml":
            body = self.identity
        elif path == "/":
            body = '<form action="/initialpassword">' if self.initial else "ready"
        elif path == "/initialpassword":
            self.initial = False
            body = "saved"
        elif path == "/login":
            body = "logged in"
        elif path == "/systemsettings":
            body = self.firmware
        elif path == "/tuner":
            body = tuner_html(self.enabled)
        elif path == "/tuner/save":
            if self.persist:
                self.enabled = True
            body = "true"
        else:
            raise AssertionError("Unexpected endpoint")
        return io.BytesIO(body.encode())

    @property
    def writes(self):
        return [request for request in self.requests if request.data is not None]


class Planning(unittest.TestCase):
    def test_identity_fields_are_checked_individually(self):
        for field in ("expectedUuid", "expectedSerial", "expectedCapability"):
            with self.subTest(field=field):
                desired = profile()
                desired[field] = "wrong"
                with self.assertRaises(manager.ConfigurationError):
                    manager.verify_identity(IDENTITY, desired)

    def test_parser_omits_unchecked_and_disabled_fields(self):
        settings = manager.tuner_settings('''
          <form class="settings"><input name="adapter" value="0">
          <input name="frontend" value="0"><input name="enable" type="checkbox" value="true">
          <input name="hidden-unused" value="x" disabled>
          <select name="scif_mode"><option value="1">Unicable</option>
          <option value="0" selected>Disabled</option></select></form>''')
        self.assertEqual(
            settings["tuners"], [{"adapter": "0", "frontend": "0", "scif_mode": "0"}]
        )

    def test_unrecognized_page_is_rejected(self):
        with self.assertRaises(manager.ConfigurationError):
            manager.tuner_settings("<html>login page</html>")

    def test_missing_extra_and_duplicate_tuners_are_rejected(self):
        declared = profile()["tuners"]
        for actual in (
            [],
            declared + [{"adapter": "0", "frontend": "1"}],
            declared + declared,
        ):
            with self.subTest(actual=actual):
                with self.assertRaises(manager.ConfigurationError):
                    manager.plan_tuners({"tuners": actual}, declared)
        with self.assertRaises(manager.ConfigurationError):
            manager.plan_tuners({"tuners": declared}, declared + declared)

    def test_partial_change_preserves_unmanaged_fields_and_original(self):
        current = {
            "max": {"vendor": "preserve"},
            "tuners": [{"adapter": "0", "frontend": "0", "sys": "[5, 6]", "enable": "true"}],
        }
        original = copy.deepcopy(current)
        target, changes = manager.plan_tuners(
            current, [{"adapter": "0", "frontend": "0", "enable": "false"}]
        )
        self.assertEqual(target["max"], current["max"])
        self.assertEqual(target["tuners"][0]["sys"], "[5, 6]")
        self.assertNotIn("enable", target["tuners"][0])
        self.assertEqual(current, original)
        self.assertEqual(changes, ["0:0 enable"])


class Reconciliation(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.config = self.directory / "profile.json"
        self.config.write_text(json.dumps(profile()))
        self.password = self.directory / "password"
        self.password.write_text("synthetic-runtime-password")
        self.state = self.directory / "state"

    def run_manager(self, session, mode="apply"):
        output = io.StringIO()
        with (
            patch.object(manager.urllib.request, "build_opener", return_value=session),
            redirect_stdout(output),
        ):
            status = manager.reconcile(mode, self.config, self.password, self.state)
        return status, output.getvalue()

    def test_wrong_receiver_refuses_before_login_or_password_provisioning(self):
        session = FakeSession(identity=IDENTITY.replace(UUID, "other"), initial=True)
        with self.assertRaises(manager.ConfigurationError):
            self.run_manager(session)
        self.assertEqual(len(session.requests), 1)
        self.assertEqual(session.writes, [])

    def test_wrong_firmware_refuses_tuner_writes(self):
        session = FakeSession(firmware="octonet-pro-9.9.9")
        with self.assertRaises(manager.ConfigurationError):
            self.run_manager(session)
        self.assertEqual([request.full_url for request in session.writes], ["http://192.0.2.1/login"])
        self.assertFalse(self.state.exists())

    def test_matching_configuration_does_not_write_tuners_or_create_backup(self):
        session = FakeSession(enabled=True)
        status, output = self.run_manager(session)
        self.assertEqual(status, 0)
        self.assertIn("matches", output)
        self.assertEqual(len(session.writes), 1)  # Login only.
        self.assertFalse(self.state.exists())

    def test_check_reports_drift_without_tuner_write(self):
        session = FakeSession()
        status, _ = self.run_manager(session, "check")
        self.assertEqual(status, 2)
        self.assertEqual(len(session.writes), 1)
        self.assertFalse(self.state.exists())

    def test_check_does_not_provision_initial_password(self):
        session = FakeSession(initial=True)
        with self.assertRaises(manager.ConfigurationError):
            self.run_manager(session, "check")
        self.assertEqual(session.writes, [])

    def test_apply_provisions_initial_password_before_authenticated_reads(self):
        session = FakeSession(initial=True, enabled=True)
        self.assertEqual(self.run_manager(session)[0], 0)
        self.assertEqual(
            [manager.urllib.parse.urlsplit(request.full_url).path for request in session.writes],
            ["/initialpassword", "/login"],
        )

    def test_apply_backs_up_verifies_and_second_run_is_idempotent(self):
        session = FakeSession()
        status, output = self.run_manager(session)
        self.assertEqual(status, 0)
        self.assertIn("applied and verified", output)
        self.assertNotIn(SERIAL, output)
        self.assertNotIn("synthetic-runtime-password", output)
        self.assertNotIn("private-unmanaged-setting", output)
        backups = list(self.state.iterdir())
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.state.stat().st_mode & 0o777, 0o700)
        before = json.loads(backups[0].read_text())
        self.assertNotIn("enable", before["tuners"][0])
        target = json.loads(session.writes[-1].data)
        self.assertEqual(target["tuners"][0]["vendor"], "private-unmanaged-setting")
        session.requests.clear()
        self.assertEqual(self.run_manager(session)[0], 0)
        self.assertEqual(len(session.writes), 1)
        self.assertEqual(len(list(self.state.iterdir())), 1)

    def test_failed_readback_does_not_report_success(self):
        session = FakeSession(persist=False)
        with self.assertRaisesRegex(manager.ConfigurationError, "did not persist"):
            self.run_manager(session)

    def test_public_backup_directory_is_rejected_before_tuner_write(self):
        self.state.mkdir(mode=0o755)
        self.state.chmod(0o755)
        session = FakeSession()
        with self.assertRaisesRegex(manager.ConfigurationError, "0700"):
            self.run_manager(session)
        self.assertEqual(len(session.writes), 1)

    def test_cli_suppresses_unexpected_exception_details(self):
        output = io.StringIO()
        with (
            patch("sys.argv", ["octopus-net-nix", "check", str(self.config)]),
            patch.object(manager, "reconcile", side_effect=ValueError("synthetic-runtime-password")),
            patch("sys.stderr", output),
        ):
            previous = os.umask(0o077)
            try:
                self.assertEqual(manager.main(), 1)
            finally:
                os.umask(previous)
        self.assertNotIn("synthetic-runtime-password", output.getvalue())
        self.assertIn("ValueError", output.getvalue())


if __name__ == "__main__":
    unittest.main()
