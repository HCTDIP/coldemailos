"""
ColdEmailOS Tests
Run: pytest tests/ -v
"""
import pytest
import os
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime

# Set test env
os.environ["BREVO_API_KEY"] = "test-key-xxxx"

from src.models import (
    SendRequest, SenderType, Contact, ContactList,
    CampaignCreate, CampaignStep, WebhookEvent, WebhookPayload,
    QuotaInfo, StatsResponse
)
from src.services.email_service import EmailService
from src.brevo_client import BrevoClient, BrevoError


class TestModels:
    def test_send_request_validation(self):
        req = SendRequest(to="test@example.com", subject="Test", sender=SenderType.LEADFORGE)
        assert req.to == "test@example.com"
        assert req.sender == SenderType.LEADFORGE

    def test_campaign_create(self):
        steps = [CampaignStep(template="cold_outreach_v1", delay_hours=0)]
        camp = CampaignCreate(name="Test", steps=steps, list_id="list_1")
        assert camp.name == "Test"
        assert len(camp.steps) == 1

    def test_webhook_payload(self):
        payload = WebhookPayload(
            event=WebhookEvent.OPENED,
            email="test@example.com",
            timestamp=datetime.utcnow(),
        )
        assert payload.event == WebhookEvent.OPENED


class TestEmailService:
    @pytest.fixture
    def mock_brevo(self):
        return AsyncMock(spec=BrevoClient)

    @pytest.fixture
    def service(self, mock_brevo):
        return EmailService(brevo_client=mock_brevo)

    @pytest.mark.asyncio
    async def test_send_suppressed_email(self, service):
        service.add_suppression("blocked@example.com", WebhookEvent.UNSUBSCRIBED)
        result = await service.send(to="blocked@example.com", subject="Test")
        assert result.success is False
        assert "Suppressed" in result.error

    @pytest.mark.asyncio
    async def test_send_quota_exhausted(self, service):
        # Mock quota to return 0 remaining
        service.get_quota = AsyncMock(return_value=QuotaInfo(
            daily_limit=300, used_today=300, remaining=0,
            reset_at=datetime.utcnow()
        ))
        result = await service.send(to="test@example.com", subject="Test")
        assert result.success is False
        assert "quota" in result.error.lower()

    @pytest.mark.asyncio
    async def test_send_template_render(self, service):
        # Mock quota check
        service.get_quota = AsyncMock(return_value=QuotaInfo(
            daily_limit=300, used_today=0, remaining=300,
            reset_at=datetime.utcnow()
        ))
        service.brevo.send_email = AsyncMock(return_value=MagicMock(success=True, message_id="test-123"))

        result = await service.send(
            to="test@example.com",
            subject="Test",
            template="cold_outreach_v1",
            variables={"first_name": "John", "company": "Acme", "pain_point": "hiring", "proof": "Vercel"}
        )
        assert result.success is True
        service.brevo.send_email.assert_called_once()

    def test_render_template(self, service):
        html, text = service.render_template("cold_outreach_v1", {
            "first_name": "John", "company": "Acme", "pain_point": "hiring", "proof": "Vercel"
        })
        assert "John" in html
        assert "Acme" in text
        assert "hiring" in html

    def test_list_templates(self, service):
        templates = service.list_templates()
        assert "cold_outreach_v1" in templates
        assert "followup_v1" in templates
        assert "breakup_v1" in templates

    @pytest.mark.asyncio
    async def test_handle_webhook_hard_bounce(self, service):
        payload = WebhookPayload(
            event=WebhookEvent.HARD_BOUNCE,
            email="bounce@example.com",
            timestamp=datetime.utcnow(),
        )
        result = await service.handle_webhook(payload)
        assert result is True
        assert service.is_suppressed("bounce@example.com")
        assert service.get_suppression_reason("bounce@example.com") == WebhookEvent.HARD_BOUNCE

    @pytest.mark.asyncio
    async def test_handle_webhook_soft_bounce_no_suppress(self, service):
        payload = WebhookPayload(
            event=WebhookEvent.SOFT_BOUNCE,
            email="soft@example.com",
            timestamp=datetime.utcnow(),
        )
        result = await service.handle_webhook(payload)
        assert result is True
        # Soft bounce should NOT suppress immediately
        assert not service.is_suppressed("soft@example.com")

    def test_suppression_management(self, service):
        service.add_suppression("test@example.com", WebhookEvent.SPAM_COMPLAINT)
        assert service.is_suppressed("test@example.com")
        assert service.get_suppression_reason("test@example.com") == WebhookEvent.SPAM_COMPLAINT

        service.remove_suppression("test@example.com")
        assert not service.is_suppressed("test@example.com")


class TestBrevoClient:
    @pytest.fixture
    def client(self):
        return BrevoClient(api_key="test-key")

    @pytest.mark.asyncio
    async def test_send_email_success(self, client):
        mock_response = {"messageId": "<test-123@brevo.com>"}
        client._post = AsyncMock(return_value=mock_response)

        from src.models import SendRequest
        req = SendRequest(to="test@example.com", subject="Test", sender=SenderType.LEADFORGE)
        result = await client.send_email(req)

        assert result.success is True
        assert result.message_id == "<test-123@brevo.com>"

    @pytest.mark.asyncio
    async def test_send_email_failure(self, client):
        client._post = AsyncMock(side_effect=BrevoError("Invalid API key", 401))

        from src.models import SendRequest
        req = SendRequest(to="test@example.com", subject="Test", sender=SenderType.LEADFORGE)
        result = await client.send_email(req)

        assert result.success is False
        assert "Invalid API key" in result.error


class TestIntegration:
    """Integration tests (require real BREVO_API_KEY)"""
    @pytest.mark.skipif(not os.getenv("BREVO_API_KEY") or os.getenv("BREVO_API_KEY") == "test-key-xxxx", reason="Requires real API key")
    @pytest.mark.asyncio
    async def test_real_send(self):
        svc = EmailService()
        # Ensure senders exist
        await svc.ensure_senders()

        # Send test email
        result = await svc.send(
            to=os.getenv("TEST_EMAIL", "test@example.com"),
            subject="ColdEmailOS Integration Test",
            template="cold_outreach_v1",
            variables={"first_name": "Test", "company": "TestCorp", "pain_point": "testing", "proof": "CI"},
            sender=SenderType.TEST,
        )
        assert result.success is True
        assert result.message_id is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])