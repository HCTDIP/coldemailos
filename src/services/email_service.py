"""
Email Service — Business logic layer for sending, campaigns, suppression
"""
import os
import logging
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from src.models import (
    SendRequest, SendResponse, SenderType, Contact, ContactList,
    Campaign, CampaignCreate, CampaignStep, QuotaInfo, StatsResponse,
    WebhookEvent, WebhookPayload
)
from src.brevo_client import BrevoClient, get_brevo_client, BrevoError

logger = logging.getLogger(__name__)


class EmailService:
    """High-level email operations with templates, rate limiting, suppression"""

    def __init__(self, brevo_client: Optional[BrevoClient] = None):
        self.brevo = brevo_client or get_brevo_client()
        self._quota_cache: Optional[QuotaInfo] = None
        self._quota_cache_time: Optional[datetime] = None

        # Template engine
        template_dir = Path(__file__).parent / "templates"
        self.jinja_env = Environment(
            loader=FileSystemLoader(str(template_dir)),
            autoescape=select_autoescape(["html", "xml"]),
            trim_blocks=True,
            lstrip_blocks=True,
        )

        # In-memory suppression (replace with DB in production)
        self._suppression: Dict[str, WebhookEvent] = {}
        self._sent_today = 0
        self._last_reset = datetime.utcnow().date()

    # ========== Quota Management ==========
    async def get_quota(self) -> QuotaInfo:
        if self._quota_cache and self._quota_cache_time:
            if (datetime.utcnow() - self._quota_cache_time).seconds < 300:
                return self._quota_cache

        try:
            quota = await self.brevo.get_quota()
            self._quota_cache = quota
            self._quota_cache_time = datetime.utcnow()
            return quota
        except Exception as e:
            logger.warning(f"Quota check failed, using local counter: {e}")
            return QuotaInfo(
                daily_limit=300,
                used_today=self._sent_today,
                remaining=max(0, 300 - self._sent_today),
                reset_at=datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1),
            )

    async def check_quota(self, count: int = 1) -> bool:
        quota = await self.get_quota()
        return quota.remaining >= count

    def _increment_sent(self, count: int = 1):
        today = datetime.utcnow().date()
        if today != self._last_reset:
            self._sent_today = 0
            self._last_reset = today
        self._sent_today += count

    # ========== Suppression ==========
    def is_suppressed(self, email: str) -> bool:
        return email.lower() in self._suppression

    def get_suppression_reason(self, email: str) -> Optional[WebhookEvent]:
        return self._suppression.get(email.lower())

    def add_suppression(self, email: str, reason: WebhookEvent):
        self._suppression[email.lower()] = reason
        logger.info(f"Suppressed {email}: {reason.value}")

    def remove_suppression(self, email: str):
        self._suppression.pop(email.lower(), None)

    async def sync_suppression_from_brevo(self):
        """Fetch bounces/complaints from Brevo stats (approximate)"""
        # Brevo doesn't have a direct suppression API, would need webhook
        pass

    # ========== Template Rendering ==========
    def render_template(self, template_name: str, variables: Dict[str, Any]) -> tuple[str, str]:
        """Returns (html, text)"""
        try:
            html_template = self.jinja_env.get_template(f"{template_name}.html.j2")
            text_template = self.jinja_env.get_template(f"{template_name}.txt.j2")
        except Exception as e:
            logger.error(f"Template not found: {template_name}")
            raise ValueError(f"Template {template_name} not found") from e

        html = html_template.render(**variables)
        text = text_template.render(**variables)
        return html, text

    def list_templates(self) -> List[str]:
        template_dir = Path(__file__).parent / "templates"
        return [f.stem.replace(".html.j2", "") for f in template_dir.glob("*.html.j2")]

    # ========== Single Send ==========
    async def send(
        self,
        to: str,
        subject: str,
        template: Optional[str] = None,
        html: Optional[str] = None,
        text: Optional[str] = None,
        variables: Dict[str, Any] = None,
        sender: SenderType = SenderType.LEADFORGE,
        tags: List[str] = None,
        headers: Dict[str, str] = None,
    ) -> SendResponse:
        """Main send method with quota check and suppression"""
        variables = variables or {}
        tags = tags or []
        headers = headers or {}

        # Suppression check
        if self.is_suppressed(to):
            reason = self.get_suppression_reason(to)
            return SendResponse(
                success=False,
                error=f"Suppressed: {reason.value if reason else 'unknown'}"
            )

        # Quota check
        if not await self.check_quota(1):
            return SendResponse(success=False, error="Daily quota exhausted (300/day)")

        # Render template if provided
        if template and not (html or text):
            html, text = self.render_template(template, variables)

        request = SendRequest(
            to=to,
            subject=subject,
            template=template,
            html=html,
            text=text,
            variables=variables,
            sender=sender,
            tags=tags,
            headers=headers,
        )

        result = await self.brevo.send_email(request)

        if result.success:
            self._increment_sent(1)
            logger.info(f"Sent to {to} via {sender.value}: {result.message_id}")
        else:
            logger.error(f"Failed to send to {to}: {result.error}")

        return result

    async def send_batch(self, requests: List[Dict]) -> List[SendResponse]:
        """Send multiple emails with rate limiting"""
        results = []
        for req in requests:
            if not await self.check_quota(1):
                results.append(SendResponse(success=False, error="Quota exhausted"))
                break
            result = await self.send(**req)
            results.append(result)
        return results

    # ========== Campaigns ==========
    def create_campaign(self, campaign: CampaignCreate) -> Campaign:
        camp = Campaign(
            id=f"camp_{datetime.utcnow().timestamp()}",
            name=campaign.name,
            steps=campaign.steps,
            list_id=campaign.list_id,
            sender=campaign.sender,
            created_at=datetime.utcnow(),
            status="draft",
        )
        # In production: persist to DB
        return camp

    async def execute_campaign_step(
        self,
        campaign: Campaign,
        step_index: int,
        contact: Contact,
    ) -> SendResponse:
        if step_index >= len(campaign.steps):
            return SendResponse(success=False, error="No more steps")

        step = campaign.steps[step_index]
        variables = {
            "first_name": contact.first_name or "there",
            "last_name": contact.last_name or "",
            "company": contact.company or "your company",
            **contact.custom_fields,
        }

        # Check conditions
        if step.conditions:
            # Simple condition evaluation (extend as needed)
            pass

        return await self.send(
            to=contact.email,
            subject=f"{{{{subject_{step.template}}}}}",  # Template defines subject
            template=step.template,
            variables=variables,
            sender=campaign.sender,
            tags=[f"campaign:{campaign.id}", f"step:{step_index}"],
        )

    # ========== Webhook Handler ==========
    async def handle_webhook(self, payload: WebhookPayload) -> bool:
        """Process Brevo webhook, update suppression, trigger actions"""
        email = payload.email.lower()

        if payload.event in [WebhookEvent.HARD_BOUNCE, WebhookEvent.SPAM_COMPLAINT, WebhookEvent.UNSUBSCRIBED]:
            self.add_suppression(email, payload.event)
            logger.warning(f"Auto-suppressed {email} due to {payload.event.value}")
            return True

        elif payload.event == WebhookEvent.SOFT_BOUNCE:
            # Soft bounce: maybe retry later, don't suppress immediately
            logger.info(f"Soft bounce for {email}")
            return True

        # Track engagement
        if payload.event in [WebhookEvent.OPENED, WebhookEvent.CLICKED]:
            logger.info(f"Engagement: {payload.event.value} for {email}")
            # Could trigger campaign progression here

        return True

    # ========== Stats ==========
    async def get_stats(self, days: int = 7) -> StatsResponse:
        return await self.brevo.get_stats(days)

    # ========== Utility ==========
    async def verify_sender(self, sender: SenderType) -> bool:
        senders = await self.brevo.list_senders()
        return any(s.email == sender.value and s.active for s in senders)

    async def ensure_senders(self) -> List[SenderType]:
        """Ensure all 6 default senders exist"""
        existing = await self.brevo.list_senders()
        existing_emails = {s.email for s in existing}

        created = []
        for sender in SenderType:
            if sender.value not in existing_emails:
                await self.brevo.create_sender(sender.value, sender.value.split("@")[0].title())
                created.append(sender)
                logger.info(f"Created sender: {sender.value}")

        return created


# Dependency injection
_email_service: Optional[EmailService] = None


def get_email_service() -> EmailService:
    global _email_service
    if _email_service is None:
        _email_service = EmailService()
    return _email_service