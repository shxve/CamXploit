"""
Pure-function tests for CamXploit's target-parsing, protocol-selection, and
credential (Digest / RTSP-auth) logic.

CamXploit is a single ~1.8k-line script with no package boundary, so the module
is loaded by path here. Its thread-safe ``print`` shadow is silenced during the
run: none of the functions under test derive their return value from console
output, so muting it only removes noise.

Run (from the CamXploit repo root):
    python3 -m unittest discover -s tests -v
    # or, since pytest is available: pytest tests -v
"""

import hashlib
import importlib.util
import os
import unittest
from unittest import mock

# --- load CamXploit.py by path (it is a script, not an importable package) ---
_HERE = os.path.dirname(os.path.abspath(__file__))
_MODPATH = os.path.join(_HERE, "..", "CamXploit.py")
_spec = importlib.util.spec_from_file_location("camxploit", _MODPATH)
cx = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cx)
# Silence the threaded print wrapper; return values do not depend on it.
cx.print = lambda *a, **k: None


def _md5(value: str) -> str:
    return hashlib.md5(value.encode()).hexdigest()


class ParseIpPortTests(unittest.TestCase):
    def test_plain_ip(self):
        self.assertEqual(cx.parse_ip_port("1.2.3.4"), ("1.2.3.4", None))

    def test_ip_with_port(self):
        self.assertEqual(cx.parse_ip_port("1.2.3.4:8080"), ("1.2.3.4", 8080))

    def test_surrounding_whitespace_is_stripped(self):
        self.assertEqual(cx.parse_ip_port("  1.2.3.4  "), ("1.2.3.4", None))

    def test_port_out_of_range_is_rejected(self):
        self.assertEqual(cx.parse_ip_port("1.2.3.4:99999"), (None, None))
        self.assertEqual(cx.parse_ip_port("1.2.3.4:0"), (None, None))

    def test_non_numeric_port_is_rejected(self):
        self.assertEqual(cx.parse_ip_port("1.2.3.4:https"), (None, None))

    def test_empty_port_is_rejected(self):
        self.assertEqual(cx.parse_ip_port("1.2.3.4:"), (None, None))

    def test_ipv6_forms_are_rejected(self):
        # Bare and bracketed IPv6 must not be silently mangled by the colon split.
        self.assertEqual(cx.parse_ip_port("fe80::1"), (None, None))
        self.assertEqual(cx.parse_ip_port("[::1]:80"), (None, None))


class ValidateIpTests(unittest.TestCase):
    def test_valid_public_ipv4(self):
        self.assertTrue(cx.validate_ip("8.8.8.8"))

    def test_private_ipv4_is_allowed(self):
        # Private is allowed (with a warning) — the operator owns the network.
        self.assertTrue(cx.validate_ip("10.0.0.1"))

    def test_malformed_ip_is_rejected(self):
        self.assertFalse(cx.validate_ip("999.1.1.1"))
        self.assertFalse(cx.validate_ip("not-an-ip"))

    def test_ipv6_is_rejected(self):
        self.assertFalse(cx.validate_ip("::1"))


class GetProtocolTests(unittest.TestCase):
    def test_known_tls_ports_are_https(self):
        self.assertEqual(cx.get_protocol(443), "https")
        self.assertEqual(cx.get_protocol(8443), "https")

    def test_plain_port_without_ip_is_http(self):
        # ip=None -> no TLS probe -> static behaviour, stays offline/pure.
        self.assertEqual(cx.get_protocol(80, ip=None), "http")
        self.assertEqual(cx.get_protocol(8080, ip=None), "http")


class ParseDigestChallengeTests(unittest.TestCase):
    def test_parses_quoted_params(self):
        params = cx._parse_digest_challenge(
            'Digest realm="IPCamera", nonce="abc123", qop="auth"'
        )
        self.assertEqual(params["realm"], "IPCamera")
        self.assertEqual(params["nonce"], "abc123")
        self.assertEqual(params["qop"], "auth")

    def test_case_insensitive_scheme(self):
        self.assertIsNotNone(cx._parse_digest_challenge('digest realm="x", nonce="y"'))

    def test_non_digest_scheme_returns_none(self):
        self.assertIsNone(cx._parse_digest_challenge('Basic realm="cam"'))

    def test_empty_returns_none(self):
        self.assertIsNone(cx._parse_digest_challenge(""))
        self.assertIsNone(cx._parse_digest_challenge(None))


class DigestResponseTests(unittest.TestCase):
    USER, PW = "admin", "1234"
    METHOD, URI = "DESCRIBE", "rtsp://1.2.3.4:554/"
    REALM, NONCE = "IPCamera", "abc123"

    def test_non_qop_matches_rfc_formula(self):
        challenge = {"realm": self.REALM, "nonce": self.NONCE}
        header = cx._digest_response(self.USER, self.PW, self.METHOD, self.URI, challenge)
        self.assertTrue(header.startswith("Digest "))
        self.assertIn('username="admin"', header)
        self.assertIn('realm="IPCamera"', header)
        # Golden value, computed independently from the RFC 2617 formula.
        self.assertIn('response="facffdbc0f4dc43bcb75975cfd82535b"', header)
        self.assertNotIn("qop=", header)

    def test_qop_auth_with_patched_cnonce(self):
        challenge = {"realm": self.REALM, "nonce": self.NONCE, "qop": "auth"}
        fixed = b"\x00" * 8  # cnonce = md5(os.urandom(8))[:16]
        expected_cnonce = hashlib.md5(fixed).hexdigest()[:16]
        with mock.patch.object(cx.os, "urandom", return_value=fixed):
            header = cx._digest_response(self.USER, self.PW, self.METHOD, self.URI, challenge)
        self.assertIn("qop=auth", header)
        self.assertIn("nc=00000001", header)
        self.assertIn(f'cnonce="{expected_cnonce}"', header)

        ha1 = _md5(f"{self.USER}:{self.REALM}:{self.PW}")
        ha2 = _md5(f"{self.METHOD}:{self.URI}")
        expected = _md5(f"{ha1}:{self.NONCE}:00000001:{expected_cnonce}:auth:{ha2}")
        self.assertIn(f'response="{expected}"', header)

    def test_opaque_is_echoed(self):
        challenge = {"realm": self.REALM, "nonce": self.NONCE, "opaque": "cafe"}
        header = cx._digest_response(self.USER, self.PW, self.METHOD, self.URI, challenge)
        self.assertIn('opaque="cafe"', header)


class RtspResponseParsingTests(unittest.TestCase):
    def test_status_code_extraction(self):
        self.assertEqual(cx._rtsp_status(b"RTSP/1.0 200 OK\r\nCSeq: 1\r\n\r\n"), 200)
        self.assertEqual(cx._rtsp_status(b"RTSP/1.0 401 Unauthorized\r\n\r\n"), 401)

    def test_non_rtsp_status_is_none(self):
        self.assertIsNone(cx._rtsp_status(b"HTTP/1.1 200 OK\r\n\r\n"))
        self.assertIsNone(cx._rtsp_status(b""))
        self.assertIsNone(cx._rtsp_status(None))

    def test_www_authenticate_extraction(self):
        raw = (
            b"RTSP/1.0 401 Unauthorized\r\n"
            b'WWW-Authenticate: Digest realm="IPCamera", nonce="abc"\r\n'
            b"\r\n"
        )
        value = cx._rtsp_www_authenticate(raw)
        self.assertEqual(value, 'Digest realm="IPCamera", nonce="abc"')

    def test_www_authenticate_absent(self):
        self.assertIsNone(cx._rtsp_www_authenticate(b"RTSP/1.0 200 OK\r\n\r\n"))
        self.assertIsNone(cx._rtsp_www_authenticate(None))


if __name__ == "__main__":
    unittest.main()
