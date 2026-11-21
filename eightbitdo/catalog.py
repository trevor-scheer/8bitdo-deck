"""Public vendor release catalog and bounded, provenance-preserving downloads."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Protocol


CATALOG_URL = "https://support.8bitdo.com/restapi/link/v1/client/firmware/select"
ALLOWED_HOSTS = frozenset({"support.8bitdo.com", "download.8bitdo.com"})
MAX_METADATA_BYTES = 2 * 1024 * 1024
MAX_FIRMWARE_BYTES = 16 * 1024 * 1024


class CatalogError(ValueError):
    """A release or download could not be obtained and validated."""


@dataclass(frozen=True)
class Release:
    catalog_type: int
    key: str
    version: str
    filename: str
    url: str
    size: int
    md5: str
    released_at: str
    notes: str
    beta: bool


@dataclass(frozen=True)
class Artifact:
    path: str
    sha256: str
    release: Release
    vendor_md5_matches: bool
    verification_note: str


def validate_url(url: str) -> str:
    """Restrict every initial request and redirect to HTTPS vendor hosts."""
    if not isinstance(url, str) or len(url) > 2048:
        raise CatalogError("Invalid firmware URL")
    if any(ord(char) < 33 or ord(char) == 127 for char in url) or "\\" in url:
        raise CatalogError("Firmware URL contains whitespace or invalid characters")
    try:
        parsed = urllib.parse.urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise CatalogError("Invalid firmware URL") from exc
    if (
        parsed.scheme != "https"
        or parsed.hostname not in ALLOWED_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
        or parsed.fragment
    ):
        raise CatalogError("Firmware downloads require an approved HTTPS vendor host")
    decoded_path = urllib.parse.unquote(parsed.path)
    if "\\" in decoded_path or any(part in (".", "..") for part in decoded_path.split("/")):
        raise CatalogError("Firmware URL contains a traversal path")
    if any(ord(char) < 32 or ord(char) == 127 for char in decoded_path):
        raise CatalogError("Firmware URL contains invalid path characters")
    return url


class _VendorRedirects(urllib.request.HTTPRedirectHandler):
    max_redirections = 4
    max_repeats = 1

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_url(newurl)
        if req.get_method() != "GET":
            raise CatalogError("Unexpected redirect from the firmware catalog")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class Fetcher(Protocol):
    def request(self, url: str, *, data: bytes | None = None, max_bytes: int) -> bytes: ...


class HTTPSFetcher:
    """Use certificate validation, request deadlines, and response-size limits."""

    def __init__(self, timeout: float = 15.0):
        if not 0 < timeout <= 60:
            raise ValueError("Timeout must be between zero and 60 seconds")
        self.timeout = timeout
        self._opener = urllib.request.build_opener(_VendorRedirects())

    def request(self, url: str, *, data: bytes | None = None, max_bytes: int) -> bytes:
        validate_url(url)
        deadline = time.monotonic() + self.timeout
        request = urllib.request.Request(
            url,
            data=data,
            headers={
                "User-Agent": "8BitDo-Deck/0.1",
                "Content-Type": "application/json",
                "Accept-Encoding": "identity",
                "Accept": "application/json" if data is not None else "application/octet-stream",
            },
        )
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                validate_url(response.geturl())
                length = response.headers.get("Content-Length")
                if length is not None:
                    try:
                        expected_size = int(length)
                    except ValueError as exc:
                        raise CatalogError("Invalid Content-Length from vendor") from exc
                    if not 0 <= expected_size <= max_bytes:
                        raise CatalogError("Vendor response exceeds the size limit")
                if response.headers.get("Content-Encoding", "identity").lower() != "identity":
                    raise CatalogError("Unexpected compressed vendor response")
                chunks = []
                size = 0
                while True:
                    if time.monotonic() >= deadline:
                        raise CatalogError("Firmware request timed out")
                    chunk = response.read(min(64 * 1024, max_bytes + 1 - size))
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > max_bytes:
                        raise CatalogError("Vendor response exceeds the size limit")
                    chunks.append(chunk)
                return b"".join(chunks)
        except CatalogError:
            raise
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise CatalogError(f"Unable to fetch vendor firmware: {exc}") from exc


def _integer(value, name: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise CatalogError(f"Invalid {name} in firmware metadata")
    return value


def _text(value, name: str, limit: int = 256, *, empty: bool = False) -> str:
    if not isinstance(value, str) or len(value) > limit or (not empty and not value.strip()):
        raise CatalogError(f"Invalid {name} in firmware metadata")
    if any(ord(char) < 32 and char not in "\r\n\t" for char in value):
        raise CatalogError(f"Invalid {name} in firmware metadata")
    return value


def _parse_release(item: dict, catalog_type: int) -> Release:
    if not isinstance(item, dict):
        raise CatalogError("Invalid release entry in firmware metadata")
    actual_type = _integer(item.get("firmware_type"), "firmware type", 1, 65535)
    if actual_type != catalog_type:
        raise CatalogError("Vendor returned firmware for a different device type")
    key = _text(item.get("firmware_key"), "release key", 128)
    if not re.fullmatch(r"[A-Za-z0-9_-]+", key):
        raise CatalogError("Invalid release key in firmware metadata")
    version = _text(item.get("file_version"), "version", 64)
    if not re.fullmatch(r"[0-9][A-Za-z0-9._+ -]*", version):
        raise CatalogError("Invalid firmware version")
    filename = _text(item.get("fileName"), "filename", 256)
    if "/" in filename or "\\" in filename or filename in (".", ".."):
        raise CatalogError("Invalid filename in firmware metadata")
    size = _integer(item.get("fileSize"), "file size", 1, MAX_FIRMWARE_BYTES)
    md5 = _text(item.get("md5"), "MD5", 32)
    if not re.fullmatch(r"[a-fA-F0-9]{32}", md5):
        raise CatalogError("Invalid MD5 in firmware metadata")
    released_at = _text(item.get("created_at"), "release date", 10)
    try:
        date.fromisoformat(released_at)
    except ValueError as exc:
        raise CatalogError("Invalid firmware release date") from exc
    beta = _integer(item.get("beta"), "beta flag", 0, 1)
    notes = _text(item.get("readme_en") or item.get("readme") or "", "release notes", 16384, empty=True)
    return Release(
        catalog_type=actual_type,
        key=key,
        version=version,
        filename=filename,
        url=validate_url(item.get("fileURL")),
        size=size,
        md5=md5.lower(),
        released_at=released_at,
        notes=notes.replace("\\r\\n", "\n").replace("\r\n", "\n"),
        beta=bool(beta),
    )


class Catalog:
    def __init__(self, fetcher: Fetcher | None = None):
        self.fetcher = fetcher or HTTPSFetcher()

    def list_releases(self, catalog_type: int, include_beta: bool = False) -> list[Release]:
        _integer(catalog_type, "firmware type", 1, 65535)
        body = json.dumps({"firmware_type": catalog_type, "beta": int(include_beta)}).encode()
        raw = self.fetcher.request(CATALOG_URL, data=body, max_bytes=MAX_METADATA_BYTES)
        if len(raw) > MAX_METADATA_BYTES:
            raise CatalogError("Firmware metadata exceeds the size limit")
        try:
            payload = json.loads(raw)
        except (ValueError, UnicodeError) as exc:
            raise CatalogError("Vendor returned invalid firmware metadata") from exc
        if not isinstance(payload, dict) or payload.get("code") != 200:
            raise CatalogError("Vendor firmware catalog reported an error")
        entries = payload.get("data")
        if not isinstance(entries, list) or len(entries) > 1000:
            raise CatalogError("Invalid firmware release list")
        releases = []
        seen = set()
        for item in entries:
            release = _parse_release(item, catalog_type)
            if release.key in seen:
                raise CatalogError("Duplicate firmware release key")
            seen.add(release.key)
            if item.get("exists") is not True:
                continue
            if release.beta and not include_beta:
                continue
            releases.append(release)
        return releases

    def download(self, release: Release, cache_dir: Path, *, expected_sha256: str | None = None) -> Artifact:
        """Cache complete HTTPS downloads without treating them as flash-approved.

        The vendor's MD5 field has undocumented semantics and does not match
        sampled complete files. Record the comparison without presenting it as
        authenticity evidence. A supplied independently trusted SHA-256 must match.
        """
        _integer(release.catalog_type, "firmware type", 1, 65535)
        _integer(release.size, "file size", 1, MAX_FIRMWARE_BYTES)
        validate_url(release.url)
        if not re.fullmatch(r"[a-fA-F0-9]{32}", release.md5):
            raise CatalogError("Invalid expected firmware checksum")
        if not re.fullmatch(r"[A-Za-z0-9_-]+", release.key):
            raise CatalogError("Invalid release key")
        if expected_sha256 is not None and not re.fullmatch(r"[a-fA-F0-9]{64}", expected_sha256):
            raise CatalogError("Invalid expected SHA-256")
        raw = self.fetcher.request(release.url, max_bytes=release.size)
        if len(raw) != release.size:
            raise CatalogError("Firmware size does not match vendor metadata")
        actual_md5 = hashlib.md5(raw, usedforsecurity=False).hexdigest()
        md5_matches = actual_md5 == release.md5.lower()
        digest = hashlib.sha256(raw).hexdigest()
        if expected_sha256 is not None and digest != expected_sha256.lower():
            raise CatalogError("Firmware SHA-256 does not match the independently supplied digest")
        note = "HTTPS download with matching size; no verified vendor signature."
        if not md5_matches:
            note += " Vendor MD5 field differs from the complete file; its semantics are unverified."
        if expected_sha256 is not None:
            note += " The independently supplied SHA-256 matches."
        cache_dir = Path(cache_dir)
        cache_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = cache_dir / f"{digest}.dat"
        self._atomic_write(path, raw)
        artifact = Artifact(str(path), digest, release, md5_matches, note)
        receipt = asdict(artifact)
        receipt_path = cache_dir / f"{digest}-{release.catalog_type}-{release.key}.json"
        self._atomic_write(receipt_path, (json.dumps(receipt, indent=2) + "\n").encode())
        return artifact

    @staticmethod
    def _atomic_write(path: Path, data: bytes) -> None:
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".download-", delete=False) as output:
                temporary = Path(output.name)
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
        except OSError as exc:
            raise CatalogError(f"Unable to cache firmware: {exc}") from exc
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
