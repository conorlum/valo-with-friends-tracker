"""The site's upload API, using only urllib and streaming multipart file bodies."""

import json
import time
import uuid
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, build_opener

from . import __version__
from .constants import REFUSED_RETRY_S


class Refused(Exception):
    def __init__(self, retry_after_s=REFUSED_RETRY_S):
        self.retry_after_s = retry_after_s
        super().__init__(f"Upload refused; retry after {retry_after_s} seconds")


class Rejected(Exception):
    pass


class BadKey(Exception):
    pass


class Offline(Exception):
    pass


def _retry_after(headers):
    value = headers.get("Retry-After") if headers else None
    if value:
        try:
            seconds = int(value)
            return seconds if seconds >= 0 else REFUSED_RETRY_S
        except ValueError:
            try:
                return max(0, parsedate_to_datetime(value).timestamp() - time.time())
            except (ValueError, TypeError, OverflowError):
                pass
    return REFUSED_RETRY_S


def _http_error(error):
    """All HTTP status mappings live here, including future API-specific errors."""
    if error.code in (429, 503):
        raise Refused(_retry_after(error.headers)) from None
    if error.code == 401:
        raise BadKey("Upload key is missing, unknown or revoked") from None
    if error.code == 400:
        try:
            message = json.load(error).get("error", "Replay rejected")
        except (ValueError, AttributeError, OSError):
            message = "Replay rejected"
        raise Rejected(str(message)) from None
    raise error


class Client:
    def __init__(self, site, key, opener=None):
        self.site = site.rstrip("/")
        self._key = key
        self.opener = build_opener() if opener is None else opener

    def _request(self, route, *, data=None, headers=None):
        request = Request(self.site + route, data=data, headers={
            "Authorization": "Bearer " + self._key,
            "User-Agent": "ValoUploader/" + __version__,
            **(headers or {}),
        })
        try:
            with self.opener.open(request, timeout=60) as response:
                return json.load(response)
        except HTTPError as error:
            try:
                _http_error(error)
            finally:
                error.close()
        except (URLError, TimeoutError, ConnectionError) as error:
            raise Offline("Cannot reach the upload service") from error
        except (ValueError, UnicodeError) as error:
            raise Offline("Invalid response from the upload service") from error

    def check(self, sha) -> bool:
        result = self._request("/api/replays/check", data=json.dumps({"sha256": sha}).encode("utf-8"),
                               headers={"Content-Type": "application/json"})
        return result["known"]

    def upload(self, path) -> str:
        path = Path(path)
        boundary = "ValoUploader" + uuid.uuid4().hex
        filename = path.name.replace("\\", "_").replace('"', "_").replace("\r", "_").replace("\n", "_")
        head = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
                "Content-Type: application/octet-stream\r\n\r\n").encode("utf-8")
        tail = f"\r\n--{boundary}--\r\n".encode("ascii")
        with path.open("rb") as stream:
            length = len(head) + path.stat().st_size + len(tail)

            def body():
                yield head
                while chunk := stream.read(65536):
                    yield chunk
                yield tail

            result = self._request("/api/replays/upload", data=body(), headers={
                "Content-Type": "multipart/form-data; boundary=" + boundary,
                "Content-Length": str(length),
            })
        return result["upload_id"]

    def status(self, upload_id) -> dict:
        return self._request("/api/replays/uploads/" + quote(str(upload_id), safe="") + "/status")

    def key_info(self) -> dict:
        return self._request("/api/replays/key")
