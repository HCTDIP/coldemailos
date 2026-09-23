"""
Brevo API Client — Wrapper for REST API v3
Handles: senders, emails, contacts, lists, webhooks, stats
"""
import os
import logging
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta

import httpx
from pydantic import EmailStr

from src.models import (
    SenderInfo, SenderType, SendRequest, SendResponse,
    Contact, ContactList, WebhookEvent, WebhookPayload,
    QuotaInfo, StatsResponse, EmailTemplate
)

logger = logging.getLogger(__name__)


class BrevoError(Exception):
    def __init__(self, message: str, status_code: int, response: Dict = None):
        self.status_code = status_code
        self.response = response
        super().__init__(message)


class BrevoClient:
    """Thin wrapper around Brevo REST API v3"""

    BASE_URL = "https://api.brevo.com/v3"

    def __init__(self, api_key: Optional[str] = None, timeout: float = 30.0):
        self.api_key = api_key or os.getenv("BREVO_API_KEY")
        if not self.api_key:
            raise ValueError("BREVO_API_KEY required (env or param)")

        self.client = httpx.AsyncClient(
            base_url=self.BASE_URL,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "api-key": self.api_key,
            },
            timeout=timeout,
        )

    async def close(self):
        await self.client.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()

    # ========== Senders ==========
    async def list_senders(self) -> List[SenderInfo]:
        resp = await self._get("/senders")
        return [SenderInfo(**s) for s in resp.get("senders", [])]

    async def create_sender(self, email: EmailStr, name: str) -> SenderInfo:
        resp = await self._post("/senders", {"email": email, "name": name})
        return SenderInfo(**resp)

    async def get_sender(self, email: EmailStr) -> Optional[SenderInfo]:
        senders = await self.list_senders()
        return next((s for s in senders if s.email == email), None)

    # ========== Email Sending ==========
    async def send_email(self, request: SendRequest) -> SendResponse:
        payload = {
            "sender": {"email": request.sender.value, "name": request.sender.value.split("@")[0].title()},
            "to": [{"email": request.to, "name": request.variables.get("first_name", "")}],
            "subject": request.subject,
            "tags": request.tags,
            "headers": request.headers,
        }

        if request.template:
            # Template variables go in "params" for Brevo
            payload["templateId"] = int(request.template) if request.template.isdigit() else None
            payload["params"] = request.variables
        else:
            if request.html:
                payload["htmlContent"] = request.html
            if request.text:
                payload["textContent"] = request.text

        try:
            resp = await self._post("/smtp/email", payload)
            message_id = resp.get("messageId")
            logger.info(f"Email sent: {message_id} to {request.to}")
            return SendResponse(success=True, message_id=message_id)
        except BrevoError as e:
            logger.error(f"Send failed to {request.to}: {e}")
            return SendResponse(success=False, error=str(e))

    async def send_batch(self, requests: List[SendRequest]) -> List[SendResponse]:
        results = []
        for req in requests:
            results.append(await self.send_email(req))
        return results

    # ========== Contacts & Lists ==========
    async def create_contact(self, contact: Contact, list_ids: List[int] = None) -> Contact:
        payload = {
            "email": contact.email,
            "attributes": {
                "FIRSTNAME": contact.first_name or "",
                "LASTNAME": contact.last_name or "",
                "COMPANY": contact.company or "",
                **contact.custom_fields,
            },
            "listIds": list_ids or [],
            "updateEnabled": True,
        }
        resp = await self._post("/contacts", payload)
        contact.id = str(resp.get("id", ""))
        return contact

    async def get_contact(self, email: EmailStr) -> Optional[Contact]:
        try:
            resp = await self._get(f"/contacts/{email}")
            return Contact(
                email=resp["email"],
                first_name=resp.get("attributes", {}).get("FIRSTNAME"),
                last_name=resp.get("attributes", {}).get("LASTNAME"),
                company=resp.get("attributes", {}).get("COMPANY"),
                custom_fields={k: v for k, v in resp.get("attributes", {}).items()
                               if k not in ["FIRSTNAME", "LASTNAME", "COMPANY"]},
                tags=resp.get("tags", []),
            )
        except BrevoError as e:
            if e.status_code == 404:
                return None
            raise

    async def create_list(self, name: str) -> ContactList:
        resp = await self._post("/contacts/lists", {"name": name})
        return ContactList(id=str(resp["id"]), name=name)

    async def add_to_list(self, email: EmailStr, list_id: int) -> bool:
        await self._post(f"/contacts/lists/{list_id}/contacts/add", {"emails": [email]})
        return True

    # ========== Stats ==========
    async def get_stats(self, days: int = 7) -> StatsResponse:
        end_date = datetime.utcnow().date()
        start_date = end_date - timedelta(days=days)

        resp = await self._get(
            "/smtp/statistics/aggregatedReport",
            params={
                "startDate": start_date.isoformat(),
                "endDate": end_date.isoformat(),
            },
        )

        data = resp.get("aggregatedReport", {})
        sent = data.get("requests", 0)
        delivered = data.get("delivered", 0)
        opened = data.get("opens", 0)
        clicked = data.get("clicks", 0)
        bounced = data.get("hardBounces", 0) + data.get("softBounces", 0)
        unsubscribed = data.get("unsubscribes", 0)
        complained = data.get("complaints", 0)

        return StatsResponse(
            period_days=days,
            sent=sent,
            delivered=delivered,
            opened=opened,
            clicked=clicked,
            bounced=bounced,
            unsubscribed=unsubscribed,
            complained=complained,
            open_rate=(opened / delivered * 100) if delivered else 0,
            click_rate=(clicked / delivered * 100) if delivered else 0,
            bounce_rate=(bounced / sent * 100) if sent else 0,
        )

    async def get_quota(self) -> QuotaInfo:
        # Brevo doesn't expose quota via API reliably, estimate from stats
        stats = await self.get_stats(days=1)
        return QuotaInfo(
            daily_limit=300,
            used_today=stats.sent,
            remaining=max(0, 300 - stats.sent),
            reset_at=datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1),
        )

    # ========== Webhooks ==========
    async def create_webhook(self, url: str, events: List[WebhookEvent], description: str = "") -> Dict:
        payload = {
            "url": url,
            "events": [e.value for e in events],
            "description": description or "ColdEmailOS webhook",
            "type": "transactional",
        }
        return await self._post("/webhooks", payload)

    async def list_webhooks(self) -> List[Dict]:
        resp = await self._get("/webhooks")
        return resp.get("webhooks", [])

    async def delete_webhook(self, webhook_id: int) -> bool:
        await self._delete(f"/webhooks/{webhook_id}")
        return True

    # ========== Templates ==========
    async def create_template(self, template: EmailTemplate) -> Dict:
        payload = {
            "name": template.name,
            "subject": template.subject,
            "htmlContent": template.html,
            "textContent": template.text,
            "isActive": True,
        }
        return await self._post("/smtp/templates", payload)

    async def get_template(self, template_id: int) -> EmailTemplate:
        resp = await self._get(f"/smtp/templates/{template_id}")
        return EmailTemplate(
            name=resp["name"],
            subject=resp["subject"],
            html=resp["htmlContent"],
            text=resp["textContent"],
        )

    # ========== Internal ==========
    async def _get(self, path: str, params: Dict = None) -> Dict:
        resp = await self.client.get(path, params=params)
        return self._handle_response(resp)

    async def _post(self, path: str, json: Dict = None) -> Dict:
        resp = await self.client.post(path, json=json)
        return self._handle_response(resp)

    async def _delete(self, path: str) -> Dict:
        resp = await self.client.delete(path)
        return self._handle_response(resp)

    def _handle_response(self, resp: httpx.Response) -> Dict:
        if resp.status_code >= 400:
            try:
                error_data = resp.json()
                message = error_data.get("message", resp.text)
            except Exception:
                message = resp.text
            raise BrevoError(message, resp.status_code, error_data if 'error_data' in locals() else None)
        return resp.json() if resp.content else {}


# Singleton instance for dependency injection
_brevo_client: Optional[BrevoClient] = None


def get_brevo_client() -> BrevoClient:
    global _brevo_client
    if _brevo_client is None:
        _brevo_client = BrevoClient()
    return _brevo_client


async def close_brevo_client():
    global _brevo_client
    if _brevo_client:
        await _brevo_client.close()
        _brevo_client = None