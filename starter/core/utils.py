import hashlib
import json
import uuid

from django.utils import timezone


def make_uuid() -> str:
    return str(uuid.uuid4())


def utcnow():
    return timezone.now()


def content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fingerprint(*parts) -> str:
    return hashlib.sha256(
        json.dumps(sorted(str(p) for p in parts), ensure_ascii=False).encode()
    ).hexdigest()
