import hashlib
import json
import logging

from core.utils import make_uuid

logger = logging.getLogger(__name__)


def audit(actor, action, object_type, object_id, result="ok", **metadata):
    """Record an audit event. Never raises — audit failures must not break flows."""
    try:
        from .models import AuditEvent

        AuditEvent.objects.create(
            id=make_uuid(),
            actor=actor if (actor and getattr(actor, "is_authenticated", False)) else None,
            action=action,
            object_type=object_type,
            object_id=str(object_id),
            result=result,
            metadata_json={k: str(v)[:200] for k, v in metadata.items()},
        )
    except Exception as exc:
        logger.warning("audit failed: %s", exc)


def load_ruleset_from_file(path):
    """Load ruleset JSON file and create a RequirementSet if it doesn't already exist."""
    from .models import RequirementSet

    data = json.loads(path.read_text())
    key = data["id"]
    version = data["version"]
    existing = RequirementSet.objects.filter(key=key, version=version).first()
    if existing:
        return existing
    raw = path.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    return RequirementSet.objects.create(
        id=make_uuid(),
        key=key,
        version=version,
        programme=data.get("programme", ""),
        rules_json=data,
        sha256=sha,
    )


def get_active_ruleset():
    """Return the most recently published RequirementSet, or None."""
    from .models import RequirementSet

    return RequirementSet.objects.order_by("-version").first()
