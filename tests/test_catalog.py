import hashlib
import io
import json
import os
import ssl
import tempfile
import unittest
import urllib.request
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, call, patch

from eightbitdo.catalog import (
    CATALOG_URL,
    MAX_FIRMWARE_BYTES,
    Catalog,
    CatalogError,
    HTTPSFetcher,
    _VendorRedirects,
    validate_url,
)


def metadata(**changes):
    # Shape observed from the vendor API; file content is a synthetic fixture.
    result = {
        "created_at": "2026-05-13",
        "fileName": "Ultimate 2 wireless",
        "firmware_type": 94,
        "firmware_key": "177866130319695977",
        "file_version": "1.10",
        "fileSize": 32,
        "fileURL": "https://support.8bitdo.com/bd-uploads/files/example.dat",
        "exists": True,
        "readme_en": "A change.\\r\\nAnother change.",
        "beta": 0,
        "md5": hashlib.md5(b"x" * 32).hexdigest(),
    }
    result.update(changes)
    return result


class FakeFetcher:
    def __init__(self, entries=None, firmware=b"x" * 32):
        self.payload = {"code": 200, "data": entries if entries is not None else [metadata()]}
        self.firmware = firmware
        self.calls = []

    def request(self, url, *, data=None, max_bytes):
        self.calls.append((url, data, max_bytes))
        return json.dumps(self.payload).encode() if data is not None else self.firmware


class CatalogTests(unittest.TestCase):
    def test_official_request_and_release_fields(self):
        fetcher = FakeFetcher()
        releases = Catalog(fetcher).list_releases(94)
        self.assertEqual(fetcher.calls[0][0], CATALOG_URL)
        self.assertEqual(json.loads(fetcher.calls[0][1]), {"firmware_type": 94, "beta": 0})
        self.assertEqual(releases[0].version, "1.10")
        self.assertEqual(releases[0].catalog_type, 94)
        self.assertEqual(releases[0].notes, "A change.\nAnother change.")

    def test_filters_unavailable_and_beta_releases(self):
        fetcher = FakeFetcher([
            metadata(),
            metadata(firmware_key="2", beta=1),
            metadata(firmware_key="3", exists=False),
        ])
        catalog = Catalog(fetcher)
        self.assertEqual(len(catalog.list_releases(94)), 1)
        self.assertEqual(len(catalog.list_releases(94, include_beta=True)), 2)

    def test_rejects_wrong_target_even_if_version_matches(self):
        with self.assertRaisesRegex(CatalogError, "different device"):
            Catalog(FakeFetcher([metadata(firmware_type=95)])).list_releases(94)

    def test_rejects_invalid_metadata(self):
        cases = [
            {"fileSize": -1}, {"fileSize": MAX_FIRMWARE_BYTES + 1}, {"fileSize": True},
            {"fileSize": "32"}, {"fileName": "../../firmware"}, {"fileName": "a\\b"},
            {"file_version": "../1.0"}, {"firmware_key": "../key"}, {"md5": "bad"},
            {"firmware_type": True}, {"created_at": "2026-99-99"}, {"beta": 2},
            {"fileURL": "http://support.8bitdo.com/a.dat"},
        ]
        for changes in cases:
            with self.subTest(changes=changes), self.assertRaises(CatalogError):
                Catalog(FakeFetcher([metadata(**changes)])).list_releases(94)

    def test_rejects_duplicate_keys_and_broken_envelopes(self):
        with self.assertRaisesRegex(CatalogError, "Duplicate"):
            Catalog(FakeFetcher([metadata(), metadata()])).list_releases(94)
        for payload in [[], {"code": 500, "data": []}, {"code": 200, "data": {}}]:
            fetcher = FakeFetcher()
            fetcher.payload = payload
            with self.subTest(payload=payload), self.assertRaises(CatalogError):
                Catalog(fetcher).list_releases(94)

    def test_download_has_content_addressed_filename_and_receipt(self):
        catalog = Catalog(FakeFetcher())
        release = catalog.list_releases(94)[0]
        with tempfile.TemporaryDirectory() as temporary:
            artifact = catalog.download(release, Path(temporary))
            self.assertEqual(Path(artifact.path).read_bytes(), b"x" * 32)
            self.assertEqual(Path(artifact.path).name, artifact.sha256 + ".dat")
            self.assertTrue(artifact.vendor_md5_matches)
            receipt = json.loads(next(Path(temporary).glob("*.json")).read_text())
            self.assertEqual(receipt["release"]["catalog_type"], 94)
            self.assertEqual(receipt["sha256"], hashlib.sha256(b"x" * 32).hexdigest())
            self.assertIn("no verified vendor signature", artifact.verification_note)
            self.assertFalse(list(Path(temporary).glob(".download-*")))

    def test_vendor_md5_mismatch_is_explicit_not_an_authenticity_claim(self):
        catalog = Catalog(FakeFetcher([metadata(md5="0" * 32)]))
        with tempfile.TemporaryDirectory() as temporary:
            artifact = catalog.download(catalog.list_releases(94)[0], Path(temporary))
            self.assertFalse(artifact.vendor_md5_matches)
            self.assertIn("semantics are unverified", artifact.verification_note)

    def test_independent_sha256_mismatch_writes_nothing(self):
        catalog = Catalog(FakeFetcher())
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(CatalogError, "SHA-256 does not match"):
                catalog.download(catalog.list_releases(94)[0], Path(temporary), expected_sha256="0" * 64)
            self.assertEqual(list(Path(temporary).iterdir()), [])

    def test_download_rechecks_size_and_key_before_writing(self):
        for content in [b"x" * 31, b"x" * 33]:
            catalog = Catalog(FakeFetcher(firmware=content))
            with tempfile.TemporaryDirectory() as temporary:
                with self.assertRaisesRegex(CatalogError, "size"):
                    catalog.download(catalog.list_releases(94)[0], Path(temporary))
                self.assertEqual(list(Path(temporary).iterdir()), [])
        catalog = Catalog(FakeFetcher())
        with tempfile.TemporaryDirectory() as temporary:
            forged = replace(catalog.list_releases(94)[0], key="../../elsewhere")
            with self.assertRaises(CatalogError):
                catalog.download(forged, Path(temporary))
            self.assertEqual(list(Path(temporary).iterdir()), [])


class NetworkPolicyTests(unittest.TestCase):
    def test_url_policy(self):
        for url in [
            "http://support.8bitdo.com/firmware.dat",
            "https://support.8bitdo.com.evil.test/firmware.dat",
            "https://127.0.0.1/firmware.dat",
            "https://user:secret@support.8bitdo.com/firmware.dat",
            "https://support.8bitdo.com:444/firmware.dat",
            "https://support.8bitdo.com/../firmware.dat",
            "https://support.8bitdo.com/%2e%2e/firmware.dat",
            "https://support.8bitdo.com/a%00b",
            "https://support.8bitdo.com/a\\b",
            "https://support.8bitdo.com/a.dat#fragment",
        ]:
            with self.subTest(url=url), self.assertRaises(CatalogError):
                validate_url(url)
        self.assertEqual(validate_url("https://download.8bitdo.com/a.dat"), "https://download.8bitdo.com/a.dat")

    def test_redirects_cannot_escape_vendor_hosts_or_redirect_post(self):
        handler = _VendorRedirects()
        request = urllib.request.Request("https://support.8bitdo.com/a.dat")
        for destination in ["http://support.8bitdo.com/a.dat", "https://evil.test/a.dat"]:
            with self.assertRaises(CatalogError):
                handler.redirect_request(request, None, 302, "Found", {}, destination)
        post = urllib.request.Request(CATALOG_URL, data=b"{}")
        with self.assertRaisesRegex(CatalogError, "Unexpected redirect"):
            handler.redirect_request(post, None, 302, "Found", {}, "https://support.8bitdo.com/other")

    def test_oversized_chunked_response_is_bounded(self):
        response = io.BytesIO(b"x" * 40)
        response.headers = {}
        response.geturl = lambda: "https://support.8bitdo.com/a.dat"
        fetcher = HTTPSFetcher()
        fetcher._opener = Mock()
        fetcher._opener.open.return_value = response
        with self.assertRaisesRegex(CatalogError, "size limit"):
            fetcher.request("https://support.8bitdo.com/a.dat", max_bytes=32)

    def test_declared_oversize_is_rejected_before_read(self):
        response = Mock()
        response.headers = {"Content-Length": "999999"}
        response.geturl.return_value = "https://support.8bitdo.com/a.dat"
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        fetcher = HTTPSFetcher()
        fetcher._opener = Mock()
        fetcher._opener.open.return_value = response
        with self.assertRaisesRegex(CatalogError, "size limit"):
            fetcher.request("https://support.8bitdo.com/a.dat", max_bytes=32)
        response.read.assert_not_called()


class TrustStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        certificates = ssl.create_default_context().get_ca_certs(binary_form=True)
        if not certificates:
            self.skipTest("A system CA is needed to exercise real OpenSSL trust loading")
        self.bundle = self.root / "system-ca.pem"
        self.bundle.write_text(ssl.DER_cert_to_PEM_cert(certificates[0]))
        self.context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        self.original_flags = self.context.verify_flags

    def make_fetcher(self, *, platform="linux", environment=None, bundles=None):
        with patch.dict(os.environ, environment or {}, clear=True), \
             patch("sys.platform", platform), \
             patch("ssl.create_default_context", return_value=self.context), \
             patch("ssl._create_default_https_context", return_value=self.context), \
             patch("eightbitdo.catalog._SYSTEM_CA_FILES", bundles or (str(self.bundle),), create=True):
            fetcher = HTTPSFetcher()
        handlers = [handler for handler in fetcher._opener.handlers
                    if isinstance(handler, urllib.request.HTTPSHandler)]
        self.assertEqual(len(handlers), 1)
        self.assertIs(handlers[0]._context, self.context)
        self.assertEqual(self.context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(self.context.check_hostname)
        self.assertEqual(self.context.verify_flags, self.original_flags)
        return fetcher

    def test_empty_linux_default_store_loads_real_system_ca(self):
        self.assertEqual(self.context.cert_store_stats()["x509_ca"], 0)
        self.make_fetcher()
        self.assertEqual(self.context.cert_store_stats()["x509_ca"], 1)

    def test_populated_default_store_is_not_extended(self):
        self.context.load_verify_locations(cafile=str(self.bundle))
        with patch.object(self.context, "load_verify_locations", wraps=self.context.load_verify_locations) as loader:
            self.make_fetcher()
            loader.assert_not_called()

    def test_explicit_trust_overrides_prevent_fallback_even_when_empty(self):
        # capath loads lazily; an empty count cannot establish that it is invalid.
        for key, value in [("SSL_CERT_FILE", str(self.bundle)),
                           ("SSL_CERT_DIR", str(self.root)),
                           ("SSL_CERT_FILE", str(self.root / "missing.pem")),
                           ("SSL_CERT_DIR", "")]:
            with self.subTest(key=key, value=value), \
                 patch.object(self.context, "load_verify_locations") as loader:
                self.make_fetcher(environment={key: value})
                loader.assert_not_called()
                self.assertEqual(self.context.cert_store_stats()["x509_ca"], 0)

    def test_unusable_bundle_is_skipped_before_valid_system_bundle(self):
        invalid = self.root / "invalid.pem"
        invalid.write_text("not a certificate")
        missing = self.root / "missing.pem"
        bundles = tuple(map(str, (missing, invalid, self.bundle)))
        with patch.object(self.context, "load_verify_locations", wraps=self.context.load_verify_locations) as loader:
            self.make_fetcher(bundles=bundles)
            self.assertEqual(loader.call_args_list, [call(cafile=path) for path in bundles])
        self.assertEqual(self.context.cert_store_stats()["x509_ca"], 1)

    def test_absent_system_bundle_keeps_verification_required(self):
        self.make_fetcher(bundles=(str(self.root / "missing.pem"),))
        self.assertEqual(self.context.cert_store_stats()["x509_ca"], 0)

    def test_other_platforms_preserve_their_default_context(self):
        for platform in ("darwin", "win32"):
            with self.subTest(platform=platform), patch.object(self.context, "load_verify_locations") as loader:
                self.make_fetcher(platform=platform)
                loader.assert_not_called()

    def test_certificate_failure_does_not_retry_or_change_trust(self):
        fetcher = self.make_fetcher()
        fetcher._opener = Mock()
        fetcher._opener.open.side_effect = urllib.error.URLError(
            ssl.SSLCertVerificationError("certificate verification failed"))
        with patch.object(self.context, "load_verify_locations") as loader:
            with self.assertRaisesRegex(CatalogError, "certificate verification failed"):
                fetcher.request(CATALOG_URL, max_bytes=128)
            loader.assert_not_called()
        fetcher._opener.open.assert_called_once()


if __name__ == "__main__":
    unittest.main()
