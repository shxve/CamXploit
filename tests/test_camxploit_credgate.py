"""
Tests for CamXploit's credential-testing safety gates.

Two behaviours are pinned here:

  * ``test_default_passwords`` must refuse to run unless the operator asserted
    authorisation, independent of the main() call site (defense in depth).
  * ``try_default_credentials`` must not report a credential as valid when the
    endpoint enforces no authentication at all (an unauthenticated 200 means
    every password would look correct).

The HTTP layer is mocked, so the suite stays hermetic (no sockets).

Run (from the CamXploit repo root):
    python3 -m unittest discover -s tests -v
"""

import importlib.util
import os
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
_MODPATH = os.path.join(_HERE, "..", "CamXploit.py")
_spec = importlib.util.spec_from_file_location("camxploit", _MODPATH)
cx = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cx)
cx.print = lambda *a, **k: None


class _Resp:
    def __init__(self, status_code):
        self.status_code = status_code


class TestDefaultPasswordsGate(unittest.TestCase):
    def setUp(self):
        self._saved = (cx.CONFIG.authorised, cx.CONFIG.no_brute)

    def tearDown(self):
        cx.CONFIG.authorised, cx.CONFIG.no_brute = self._saved

    def test_unauthorised_run_tests_nothing(self):
        cx.CONFIG.authorised = False
        cx.CONFIG.no_brute = True
        # If the gate were missing, this would open sockets; assert it never
        # reaches the probing helpers.
        with mock.patch.object(cx, "test_rtsp_credentials") as rtsp_probe, \
             mock.patch.object(cx.SESSION, "get") as http_get:
            cx.test_default_passwords("192.0.2.10", [80, 554], rtsp_ports=[554])
        rtsp_probe.assert_not_called()
        http_get.assert_not_called()


class TryDefaultCredentialsTests(unittest.TestCase):
    def setUp(self):
        self._saved = (cx.CONFIG.authorised, cx.CONFIG.no_brute)
        cx.CONFIG.authorised = True
        cx.CONFIG.no_brute = False

    def tearDown(self):
        cx.CONFIG.authorised, cx.CONFIG.no_brute = self._saved

    def test_no_auth_enforced_returns_none(self):
        # Unauthenticated GET already returns 200 -> auth is not enforced, so
        # no credential should be reported (previously the first pair "won").
        with mock.patch.object(cx, "get_protocol", return_value="http"), \
             mock.patch.object(cx, "_capped_get", return_value=(_Resp(200), "")) as capped:
            result = cx.try_default_credentials("192.0.2.10", 80)
        self.assertIsNone(result)
        # Exactly one call: the unauthenticated probe. No credential attempts.
        self.assertEqual(capped.call_count, 1)

    def test_enforced_auth_finds_first_working_credential(self):
        calls = {"n": 0}

        def fake_capped(url, cap=None, **kwargs):
            calls["n"] += 1
            if "auth" not in kwargs:
                return _Resp(401), ""      # unauth probe: auth is enforced
            return _Resp(200), ""          # first credential works

        with mock.patch.object(cx, "get_protocol", return_value="http"), \
             mock.patch.object(cx, "_capped_get", side_effect=fake_capped):
            result = cx.try_default_credentials("192.0.2.10", 80)

        # First username/password in DEFAULT_CREDENTIALS is admin/admin.
        self.assertEqual(result, "admin:admin")

    def test_enforced_auth_but_all_wrong_returns_none(self):
        def fake_capped(url, cap=None, **kwargs):
            if "auth" not in kwargs:
                return _Resp(401), ""
            return _Resp(401), ""          # every credential rejected

        with mock.patch.object(cx, "get_protocol", return_value="http"), \
             mock.patch.object(cx, "_capped_get", side_effect=fake_capped):
            result = cx.try_default_credentials("192.0.2.10", 80)
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
