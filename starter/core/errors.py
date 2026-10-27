"""Typed domain errors and HTTP response helpers."""
import json
import logging
import uuid
from django.http import JsonResponse

logger = logging.getLogger(__name__)


class BakaBakaError(Exception):
    """Base for all domain errors."""


class ValidationError(BakaBakaError):
    """Field-level validation failure. HTTP 422."""
    def __init__(self, fields: dict | None = None, message: str = "Ievades kļūda"):
        self.fields = fields or {}
        self.message = message
        super().__init__(message)


class NotFound(BakaBakaError):
    """Object not visible to this actor. HTTP 404."""
    def __init__(self, object_type: str = "", object_id: str = ""):
        self.object_type = object_type
        self.object_id = object_id
        super().__init__(f"{object_type}:{object_id} not found")


class Forbidden(BakaBakaError):
    """Action not permitted for this actor. HTTP 403."""
    def __init__(self, action: str = ""):
        self.action = action
        super().__init__(f"Forbidden: {action}")


class Conflict(BakaBakaError):
    """State/revision/idempotency conflict. HTTP 409."""
    def __init__(self, code: str):
        self.code = code
        super().__init__(f"Conflict: {code}")


class IntegrationUnavailable(BakaBakaError):
    """External service unavailable. HTTP 503."""
    def __init__(self, code: str, retryable: bool = False):
        self.code = code
        self.retryable = retryable
        super().__init__(f"Integration unavailable: {code}")


class UnsupportedDocument(BakaBakaError):
    """Document format/content not supported. HTTP 422."""
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(f"Unsupported document: {reason}")


# Latvian error messages for UI
_LV_MESSAGES = {
    "stale_revision": "Dati ir mainīti citur. Atsvaidziniet lapu un mēģiniet vēlreiz.",
    "duplicate_request": "Šis pieprasījums jau ir aktīvs.",
    "already_decided": "Šis iesniegums jau ir izskatīts.",
    "no_active_assignment": "Nav apstiprināta vadītāja. Vispirms saņemiet vadītāja piekrišanu.",
    "pending_submission": "Gaida vadītāja lēmumu. Jaunu iesniegumu var veidot pēc lēmuma.",
    "missing_key": "AI nav konfigurēts. Varat turpināt darbu un ievadīt tēmu manuāli.",
    "model_unavailable": "Konfigurētais AI modelis nav pieejams. Pārbaudiet OPENAI_MODEL iestatījumu.",
    "rate_or_quota": "AI pakalpojuma limits sasniegts. Mēģiniet vēlāk.",
    "timeout": "AI pieprasījums pārtraukts laika limita dēļ. Dati saglabāti; mēģiniet vēlreiz.",
    "refused": "AI atteica atbildēt uz šo pieprasījumu. Saglabātie dati nav mainīti.",
    "invalid_output": "AI atbildi nevarēja pārbaudīt. Saglabātie dati nav mainīti.",
    "insufficient_grounding": "Nav pietiekami avotu AI tēmas ģenerēšanai. Saglabājiet vismaz vienu avotu.",
    "file_too_large": "Fails pārsniedz 20 MB ierobežojumu.",
    "type_mismatch": "Faila saturs neatbilst paplašinājumam.",
    "invalid_docx": "DOCX fails ir bojāts vai nevalīds.",
    "encrypted_document": "Šifrētus dokumentus nevar apstrādāt.",
    "too_many_pages": "PDF pārsniedz 100 lappušu ierobežojumu.",
    "text_layer_unavailable": "PDF nav teksta slāņa. Augšupielādējiet teksta PDF vai DOCX.",
    "max_attempts_exceeded": "Darbība neizdevās pēc vairākiem mēģinājumiem. Sazinieties ar administratoru.",
}


def lv_message(code: str) -> str:
    return _LV_MESSAGES.get(code, "Darbību neizdevās pabeigt. Saglabātie dati nav zaudēti; mēģiniet vēlreiz.")


def error_response(exc: Exception, request_id: str | None = None) -> JsonResponse:
    """Map a domain exception to a JSON error response."""
    rid = request_id or str(uuid.uuid4())
    if isinstance(exc, ValidationError):
        body = {"error": {"code": "validation", "message_lv": exc.message, "fields": exc.fields, "retryable": False}, "request_id": rid}
        return JsonResponse(body, status=422)
    if isinstance(exc, NotFound):
        body = {"error": {"code": "not_found", "message_lv": "Šis objekts nav pieejams.", "fields": {}, "retryable": False}, "request_id": rid}
        return JsonResponse(body, status=404)
    if isinstance(exc, Forbidden):
        body = {"error": {"code": "forbidden", "message_lv": "Šī darbība nav atļauta.", "fields": {}, "retryable": False}, "request_id": rid}
        return JsonResponse(body, status=403)
    if isinstance(exc, Conflict):
        body = {"error": {"code": exc.code, "message_lv": lv_message(exc.code), "fields": {}, "retryable": False}, "request_id": rid}
        return JsonResponse(body, status=409)
    if isinstance(exc, IntegrationUnavailable):
        body = {"error": {"code": exc.code, "message_lv": lv_message(exc.code), "fields": {}, "retryable": exc.retryable}, "request_id": rid}
        return JsonResponse(body, status=503)
    if isinstance(exc, UnsupportedDocument):
        body = {"error": {"code": exc.reason, "message_lv": lv_message(exc.reason), "fields": {}, "retryable": False}, "request_id": rid}
        return JsonResponse(body, status=422)
    logger.exception("Unhandled error: %s", exc)
    body = {"error": {"code": "internal_error", "message_lv": "Iekšēja servera kļūda.", "fields": {}, "retryable": False}, "request_id": rid}
    return JsonResponse(body, status=500)


def success_response(data, request_id: str | None = None, status: int = 200) -> JsonResponse:
    """Wrap data in standard success envelope."""
    rid = request_id or str(uuid.uuid4())
    return JsonResponse({"data": data, "meta": {"request_id": rid}}, status=status)
