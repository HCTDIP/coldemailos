"""
ColdEmailOS — FastAPI Application
Zero-cost cold email infrastructure for B2B SaaS founders
"""
import os
import logging
from contextlib import asynccontextmanager
from typing import Optional, List

from fastapi import FastAPI, HTTPException, Request, BackgroundTasks, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, HTMLResponse
from pydantic import BaseModel, EmailStr

from src.models import (
    SendRequest, SendResponse, SenderType, Contact, ContactList,
    CampaignCreate, Campaign, WebhookPayload, WebhookEvent,
    QuotaInfo, StatsResponse
)
from src.services.email_service import EmailService, get_email_service
from src.brevo_client import BrevoClient, get_brevo_client, close_brevo_client, BrevoError

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("coldemailos")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("🚀 ColdEmailOS starting up...")
    try:
        brevo = get_brevo_client()
        svc = get_email_service()
        await svc.ensure_senders()
        logger.info("✅ Senders verified")
    except Exception as e:
        logger.warning(f"⚠️ Startup check failed: {e}")
    yield
    # Shutdown
    logger.info("🛑 ColdEmailOS shutting down...")
    await close_brevo_client()


app = FastAPI(
    title="ColdEmailOS API",
    description="Zero-cost cold email infrastructure for B2B SaaS founders",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ========== Health & Meta ==========
@app.get("/health")
async def health():
    return {"status": "healthy", "service": "coldemailos", "version": "1.0.0"}


@app.get("/")
async def root():
    return HTMLResponse("""
    <!DOCTYPE html>
    <html>
    <head><title>ColdEmailOS</title></head>
    <body style="font-family:system-ui;padding:40px;max-width:600px;margin:0 auto">
      <h1>🚀 ColdEmailOS API</h1>
      <p>Zero-cost cold email infrastructure for B2B SaaS founders</p>
      <ul>
        <li><a href="/docs">API Docs (Swagger)</a></li>
        <li><a href="/redoc">API Docs (ReDoc)</a></li>
        <li><a href="/health">Health Check</a></li>
      </ul>
      <p><strong>Status:</strong> <span style="color:green">Live</span></p>
    </body>
    </html>
    """)


# ========== Email Sending ==========
@app.post("/api/send", response_model=SendResponse)
async def send_email(request: SendRequest, svc: EmailService = Depends(get_email_service)):
    """Send a single email"""
    result = await svc.send(
        to=request.to,
        subject=request.subject,
        template=request.template,
        html=request.html,
        text=request.text,
        variables=request.variables,
        sender=request.sender,
        tags=request.tags,
        headers=request.headers,
    )
    if not result.success:
        raise HTTPException(status_code=400, detail=result.error)
    return result


@app.post("/api/send/batch", response_model=List[SendResponse])
async def send_batch(requests: List[SendRequest], svc: EmailService = Depends(get_email_service)):
    """Send multiple emails (respects quota)"""
    req_dicts = [r.model_dump() for r in requests]
    return await svc.send_batch(req_dicts)


# ========== Quota & Stats ==========
@app.get("/api/quota", response_model=QuotaInfo)
async def get_quota(svc: EmailService = Depends(get_email_service)):
    return await svc.get_quota()


@app.get("/api/stats", response_model=StatsResponse)
async def get_stats(days: int = 7, svc: EmailService = Depends(get_email_service)):
    return await svc.get_stats(days)


# ========== Senders ==========
@app.get("/api/senders")
async def list_senders(brevo: BrevoClient = Depends(get_brevo_client)):
    return await brevo.list_senders()


@app.post("/api/senders/ensure")
async def ensure_senders(svc: EmailService = Depends(get_email_service)):
    created = await svc.ensure_senders()
    return {"created": [s.value for s in created]}


# ========== Campaigns ==========
@app.post("/api/campaigns", response_model=Campaign)
async def create_campaign(campaign: CampaignCreate, svc: EmailService = Depends(get_email_service)):
    return svc.create_campaign(campaign)


@app.get("/api/campaigns/{campaign_id}")
async def get_campaign(campaign_id: str):
    # In production: fetch from DB
    raise HTTPException(status_code=404, detail="Campaign not found")


# ========== Contacts ==========
@app.post("/api/contacts", response_model=Contact)
async def create_contact(contact: Contact, list_id: Optional[int] = None, brevo: BrevoClient = Depends(get_brevo_client)):
    return await brevo.create_contact(contact, list_ids=[list_id] if list_id else None)


@app.get("/api/contacts/{email}")
async def get_contact(email: EmailStr, brevo: BrevoClient = Depends(get_brevo_client)):
    contact = await brevo.get_contact(email)
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")
    return contact


# ========== Webhooks ==========
@app.post("/webhooks/brevo")
async def brevo_webhook(
    payload: WebhookPayload,
    background_tasks: BackgroundTasks,
    svc: EmailService = Depends(get_email_service),
):
    """Handle Brevo webhooks (delivered, opened, clicked, bounced, unsubscribed, etc.)"""
    logger.info(f"Webhook received: {payload.event.value} for {payload.email}")
    background_tasks.add_task(svc.handle_webhook, payload)
    return {"status": "accepted"}


@app.post("/webhooks/brevo/sync")
async def sync_webhooks(brevo: BrevoClient = Depends(get_brevo_client)):
    """Register webhooks with Brevo (run once on deploy)"""
    webhook_url = os.getenv("WEBHOOK_URL", "https://your-domain.com/webhooks/brevo")
    events = [
        WebhookEvent.DELIVERED,
        WebhookEvent.OPENED,
        WebhookEvent.CLICKED,
        WebhookEvent.HARD_BOUNCE,
        WebhookEvent.SOFT_BOUNCE,
        WebhookEvent.UNSUBSCRIBED,
        WebhookEvent.SPAM_COMPLAINT,
    ]
    result = await brevo.create_webhook(webhook_url, events, "ColdEmailOS auto-sync")
    return result


# ========== Templates ==========
@app.get("/api/templates")
async def list_templates(svc: EmailService = Depends(get_email_service)):
    return {"templates": svc.list_templates()}


# ========== Lead Capture (Static Form Endpoint) ==========
class LeadCaptureRequest(BaseModel):
    email: EmailStr
    first_name: Optional[str] = None
    company: Optional[str] = None
    source: Optional[str] = "website"
    consent: bool = True


@app.post("/api/leads/capture")
async def capture_lead(
    lead: LeadCaptureRequest,
    background_tasks: BackgroundTasks,
    svc: EmailService = Depends(get_email_service),
):
    """Capture lead from website form, add to list, send welcome sequence"""
    if not lead.consent:
        raise HTTPException(status_code=400, detail="Consent required")

    contact = Contact(
        email=lead.email,
        first_name=lead.first_name,
        company=lead.company,
        tags=[lead.source, "lead_capture"],
    )

    try:
        created = await svc.brevo.create_contact(contact)
        # Trigger welcome email
        background_tasks.add_task(
            svc.send,
            to=lead.email,
            subject="Welcome to ColdEmailOS! 🎯",
            template="newsletter_welcome",
            variables={"first_name": lead.first_name or "there", "download_link": "https://coldemailos.dev/guide"},
            sender=SenderType.LEADFORGE,
            tags=["lead_capture", "welcome"],
        )
        return {"status": "captured", "contact_id": created.id}
    except BrevoError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ========== Suppression Management ==========
@app.get("/api/suppressions")
async def list_suppressions(svc: EmailService = Depends(get_email_service)):
    return {"suppressions": {k: v.value for k, v in svc._suppression.items()}}


@app.delete("/api/suppressions/{email}")
async def remove_suppression(email: EmailStr, svc: EmailService = Depends(get_email_service)):
    svc.remove_suppression(email)
    return {"status": "removed", "email": email}


# ========== Error Handlers ==========
@app.exception_handler(BrevoError)
async def brevo_error_handler(request: Request, exc: BrevoError):
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.args[0], "code": exc.status_code},
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("src.main:app", host="0.0.0.0", port=8000, reload=True)