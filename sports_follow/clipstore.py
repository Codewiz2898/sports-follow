"""Where clips' videos and poster frames are kept (clips.py): an S3-compatible bucket, Cloudflare R2 in
production, read by fans through a CDN domain in front of it. The server never carries a video itself: it
hands the uploader a short-lived signed URL to put the file straight into the bucket, then checks it's there.

Settings, all from the environment (README):

    SPORTS_FOLLOW_CLIPS_ENDPOINT      https://<account>.r2.cloudflarestorage.com
    SPORTS_FOLLOW_CLIPS_BUCKET        slam-clips
    SPORTS_FOLLOW_CLIPS_REGION        auto (R2's), or an AWS region
    SPORTS_FOLLOW_CLIPS_PUBLIC_BASE   https://clips.example.com (the CDN domain on the bucket)
    SPORTS_FOLLOW_CLIPS_KEY_FILE      ~/.config/sports-follow/clips-key: the access key id, then the secret, a line each

Without all of them there's no store, and publishing says so; reading still works (there's nothing to read).
URLs are signed here (AWS Signature Version 4, query-string form) rather than by an SDK: it's one function.

The CDN domain serves every name in the bucket. A hidden clip's files (clips.set_hidden) are copied, in the bucket, to
a name made with a random token of the clip's own (hidden, Clip.hidden_as), which nobody can work out from the clip's
id and which outlives a change of the bucket's keys, and the public ones deleted; putting it back copies them back. The key needs to read and write objects (R2's Object Read & Write).
"""

from __future__ import annotations

import hashlib
import hmac
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol
from urllib.parse import quote, urlsplit

import httpx

SIGNED_FOR = 15 * 60  # seconds an upload URL works


class Store(Protocol):
    def put_url(self, key: str, content_type: str, length: int | None = None) -> str: ...
    def size(self, key: str) -> int | None: ...
    def delete(self, key: str) -> None: ...
    def copy(self, source: str, key: str) -> None: ...
    def public_url(self, key: str) -> str: ...


def hidden(token: str, key: str) -> str:
    """Where a hidden clip's file is kept (clips.set_hidden): a name only the clip's own random token makes
    (Clip.hidden_as; the CDN serves every name), with the file's extension. Not the bucket's secret: a key rotated
    would leave every clip hidden before it where nothing could find it."""
    extension = key.rsplit(".", 1)[1] if "." in key.rsplit("/", 1)[-1] else ""
    name = hmac.new(token.encode(), key.encode(), hashlib.sha256).hexdigest()[:40]
    return f"hidden/{name}.{extension}" if extension else f"hidden/{name}"


def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


def presign(method: str, host: str, path: str, *, access_key: str, secret: str, region: str, expires: int,
            now: datetime, headers: dict[str, str] | None = None, service: str = "s3") -> str:
    """A SigV4 presigned URL for `method` on https://host/path. `headers` (lower-case names) are signed too, so
    the request must send them as they are: a PUT signed for "video/mp4" can't put anything else."""
    signed = {"host": host, **{k.lower(): v.strip() for k, v in (headers or {}).items()}}
    names = ";".join(sorted(signed))
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    day = now.strftime("%Y%m%d")
    scope = f"{day}/{region}/{service}/aws4_request"
    query = {
        "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
        "X-Amz-Credential": f"{access_key}/{scope}",
        "X-Amz-Date": stamp,
        "X-Amz-Expires": str(expires),
        "X-Amz-SignedHeaders": names,
    }
    canonical_query = "&".join(f"{quote(k, safe='-_.~')}={quote(v, safe='-_.~')}" for k, v in sorted(query.items()))
    canonical_path = quote(path, safe="/-_.~")
    canonical = "\n".join([
        method,
        canonical_path,
        canonical_query,
        "".join(f"{k}:{signed[k]}\n" for k in sorted(signed)),
        names,
        "UNSIGNED-PAYLOAD",
    ])
    to_sign = "\n".join(["AWS4-HMAC-SHA256", stamp, scope, hashlib.sha256(canonical.encode()).hexdigest()])
    key = _hmac(_hmac(_hmac(_hmac(f"AWS4{secret}".encode(), day), region), service), "aws4_request")
    signature = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
    return f"https://{host}{canonical_path}?{canonical_query}&X-Amz-Signature={signature}"


@dataclass
class BucketStore:
    """An S3-compatible bucket, addressed path-style (https://endpoint/bucket/key), as R2 takes it."""

    endpoint: str
    bucket: str
    region: str
    access_key: str
    secret: str
    public_base: str

    @property
    def host(self) -> str:
        return urlsplit(self.endpoint).netloc

    def _url(self, method: str, key: str, headers: dict[str, str] | None = None, expires: int = SIGNED_FOR) -> str:
        return presign(method, self.host, f"/{self.bucket}/{key}", access_key=self.access_key, secret=self.secret,
                       region=self.region, expires=expires, now=datetime.now(timezone.utc), headers=headers)

    def put_url(self, key: str, content_type: str, length: int | None = None) -> str:
        """Signed for its type, and its exact size when given: the PUT must send both as they are."""
        headers = {"content-type": content_type}
        if length is not None:
            headers["content-length"] = str(length)
        return self._url("PUT", key, headers)

    def size(self, key: str) -> int | None:
        """The object's size in bytes, or None when it isn't there."""
        r = httpx.head(self._url("HEAD", key, expires=60), timeout=10)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return int(r.headers.get("content-length", "0"))

    def delete(self, key: str) -> None:
        r = httpx.delete(self._url("DELETE", key, expires=60), timeout=10)
        if r.status_code not in (200, 204, 404):
            r.raise_for_status()

    def copy(self, source: str, key: str) -> None:
        """The object at `source` copied to `key`, in the bucket (S3's CopyObject): the server never carries it."""
        headers = {"x-amz-copy-source": f"/{self.bucket}/{quote(source, safe='/-_.~')}"}
        r = httpx.put(self._url("PUT", key, headers, expires=60), headers=headers, timeout=60)
        r.raise_for_status()

    def public_url(self, key: str) -> str:
        return f"{self.public_base.rstrip('/')}/{key}"


class MemoryStore:
    """A bucket in memory, for tests: `put` stands in for the uploader's PUT."""

    def __init__(self, public_base: str = "https://clips.test") -> None:
        self.objects: dict[str, bytes] = {}
        self.public_base = public_base
        self.secret = os.urandom(16)

    def put_url(self, key: str, content_type: str, length: int | None = None) -> str:
        return f"https://bucket.test/{key}?signed-for={quote(content_type)}" + (f"&length={length}" if length is not None else "")

    def put(self, key: str, data: bytes) -> None:
        self.objects[key] = data

    def size(self, key: str) -> int | None:
        return len(self.objects[key]) if key in self.objects else None

    def delete(self, key: str) -> None:
        self.objects.pop(key, None)

    def copy(self, source: str, key: str) -> None:
        self.objects[key] = self.objects[source]

    def public_url(self, key: str) -> str:
        return f"{self.public_base}/{key}"


def _keys() -> tuple[str, str] | None:
    path = Path(os.environ.get("SPORTS_FOLLOW_CLIPS_KEY_FILE", Path.home() / ".config" / "sports-follow" / "clips-key"))
    try:
        lines = [ln.strip() for ln in path.read_text().splitlines() if ln.strip()]
    except OSError:
        return None
    return (lines[0], lines[1]) if len(lines) >= 2 else None


def from_env() -> Store | None:
    """The bucket the environment names, or None when publishing isn't set up."""
    endpoint = os.environ.get("SPORTS_FOLLOW_CLIPS_ENDPOINT", "").strip()
    bucket = os.environ.get("SPORTS_FOLLOW_CLIPS_BUCKET", "").strip()
    public = os.environ.get("SPORTS_FOLLOW_CLIPS_PUBLIC_BASE", "").strip()
    keys = _keys()
    if not (endpoint and bucket and public and keys):
        return None
    region = os.environ.get("SPORTS_FOLLOW_CLIPS_REGION", "auto").strip() or "auto"
    return BucketStore(endpoint, bucket, region, keys[0], keys[1], public)


STORE: Store | None = from_env()
