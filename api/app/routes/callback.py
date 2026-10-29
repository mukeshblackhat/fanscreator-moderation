"""Provider callbacks for long videos. Signed with HMAC-SHA256 over the raw body."""
import hashlib
import hmac
import json

from fastapi import APIRouter, Header, HTTPException, Request

from app.config import settings
from app.logging import log

router = APIRouter()


def valid_signature(body: bytes, signature: str | None) -> bool:
    if not signature:
        return False
    expected = hmac.new(settings.provider_webhook_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature.strip().lower())


@router.post("/provider/callback")
async def provider_callback(request: Request, x_signature: str | None = Header(default=None)):
    body = await request.body()  # verify the exact bytes that were signed
    if not valid_signature(body, x_signature):
        log.warning("callback_rejected", reason="bad_signature")
        raise HTTPException(status_code=401, detail="Invalid signature")
    try:
        payload = json.loads(body)
    except ValueError:
        raise HTTPException(status_code=400, detail="Body is not JSON") from None

    if settings.screening_mode == "broken":
        from app.screening import broken_handler

        await broken_handler.process_callback(payload)
    else:
        # Fixed pipeline lands in step 4. Until then the media stays pending (fail closed).
        log.info("callback_received_not_processed", provider_job_id=payload.get("provider_job_id"))
    return {"status": "ok"}
