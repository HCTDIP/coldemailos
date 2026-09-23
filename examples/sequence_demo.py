#!/usr/bin/env python3
"""
ColdEmailOS — Sequence Demo
Demonstrates a 3-step drip campaign
"""
import asyncio
import json
from datetime import datetime

from src.services.email_service import EmailService, get_email_service
from src.models import CampaignCreate, CampaignStep, SenderType


async def main():
    print("=" * 60)
    print("🎯 ColdEmailOS — Drip Campaign Demo")
    print("=" * 60)

    svc = get_email_service()

    # 1. Ensure senders exist
    print("\n📧 Ensuring senders...")
    created = await svc.ensure_senders()
    if created:
        print(f"  Created: {[s.value for s in created]}")
    else:
        print("  All senders already exist ✅")

    # 2. Check quota
    print("\n📊 Checking quota...")
    quota = await svc.get_quota()
    print(f"  Daily limit: {quota.daily_limit}")
    print(f"  Used today:  {quota.used_today}")
    print(f"  Remaining:   {quota.remaining}")

    if quota.remaining < 3:
        print("  ⚠️ Not enough quota for demo")
        return

    # 3. Create campaign
    print("\n📋 Creating campaign...")
    campaign = CampaignCreate(
        name="Demo Campaign - DevTools Outreach",
        steps=[
            CampaignStep(template="cold_outreach_v1", delay_hours=0),
            CampaignStep(template="followup_v1", delay_hours=72),   # 3 days
            CampaignStep(template="breakup_v1", delay_hours=168),   # 7 days
        ],
        list_id="demo_devtools_2024",
        sender=SenderType.LEADFORGE,
    )
    created_campaign = svc.create_campaign(campaign)
    print(f"  Campaign ID: {created_campaign.id}")
    print(f"  Steps: {len(created_campaign.steps)}")

    # 4. Demo contacts (would come from your ICP list)
    demo_contacts = [
        {"email": "test1@example.com", "first_name": "Sarah", "company": "Vercel", "pain_point": "scaling interviews", "proof": "Linear, Railway"},
        {"email": "test2@example.com", "first_name": "Marcus", "company": "Supabase", "pain_point": "hiring velocity", "proof": "Vercel, PlanetScale"},
    ]

    # 5. Execute Step 1 for all contacts (instant)
    print("\n🚀 Executing Step 1 (Instant)...")
    for contact in demo_contacts:
        variables = {
            "first_name": contact["first_name"],
            "company": contact["company"],
            "pain_point": contact["pain_point"],
            "proof": contact["proof"],
        }

        result = await svc.send(
            to=contact["email"],
            subject=f"Quick question about {contact['company']}'s hiring",
            template="cold_outreach_v1",
            variables=variables,
            sender=SenderType.LEADFORGE,
            tags=["demo", "step:1", "campaign:devtools_outreach"],
        )

        if result.success:
            print(f"  ✅ Sent to {contact['email']} — {result.message_id}")
        else:
            print(f"  ❌ Failed for {contact['email']}: {result.error}")

    # 6. Show final quota
    print("\n📊 Final quota...")
    quota = await svc.get_quota()
    print(f"  Remaining: {quota.remaining}")

    # 7. Show templates available
    print("\n📝 Available templates:")
    for t in svc.list_templates():
        print(f"  • {t}")

    print("\n" + "=" * 60)
    print("✅ Demo complete!")
    print("=" * 60)
    print("\nNext steps:")
    print("  1. Add real contacts to your ICP list")
    print("  2. Set up webhook: POST /webhooks/brevo")
    print("  3. Deploy to Railway/Fly.io/Render")
    print("  4. Schedule steps 2 & 3 with cron or background worker")


if __name__ == "__main__":
    asyncio.run(main())