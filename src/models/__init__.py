from pydantic import BaseModel, EmailStr, Field
from typing import Optional, List, Dict, Any
from datetime import datetime
from enum import Enum


class SenderType(str, Enum):
    LEADFORGE = "leadforge@sendinblue.com"
    NOREPLY = "noreply@sendinblue.com"
    ALERTS = "alerts@sendinblue.com"
    BOUNTY = "bounty@sendinblue.com"
    JEV = "jev@sendinblue.com"
    TEST = "test@sendinblue.com"


class SenderInfo(BaseModel):
    email: EmailStr
    name: str
    id: Optional[int] = None
    active: bool = True


class EmailTemplate(BaseModel):
    name: str
    subject: str
    html: str
    text: str
    variables: List[str] = []


class SendRequest(BaseModel):
    to: EmailStr
    subject: str
    template: Optional[str] = None
    html: Optional[str] = None
    text: Optional[str] = None
    variables: Dict[str, Any] = {}
    sender: SenderType = SenderType.LEADFORGE
    tags: List[str] = []
    headers: Dict[str, str] = {}


class SendResponse(BaseModel):
    success: bool
    message_id: Optional[str] = None
    error: Optional[str] = None


class CampaignStep(BaseModel):
    template: str
    delay_hours: int = 0
    conditions: Dict[str, Any] = {}


class CampaignCreate(BaseModel):
    name: str
    steps: List[CampaignStep]
    list_id: str
    sender: SenderType = SenderType.LEADFORGE


class Campaign(BaseModel):
    id: str
    name: str
    steps: List[CampaignStep]
    list_id: str
    sender: SenderType
    created_at: datetime
    status: str = "draft"  # draft, active, paused, completed


class Contact(BaseModel):
    email: EmailStr
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    company: Optional[str] = None
    custom_fields: Dict[str, Any] = {}
    tags: List[str] = []
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ContactList(BaseModel):
    id: str
    name: str
    contacts: List[Contact] = []
    created_at: datetime = Field(default_factory=datetime.utcnow)


class WebhookEvent(str, Enum):
    DELIVERED = "delivered"
    OPENED = "opened"
    CLICKED = "clicked"
    HARD_BOUNCE = "hard_bounce"
    SOFT_BOUNCE = "soft_bounce"
    UNSUBSCRIBED = "unsubscribed"
    SPAM_COMPLAINT = "spam_complaint"
    BLOCKED = "blocked"


class WebhookPayload(BaseModel):
    event: WebhookEvent
    email: EmailStr
    message_id: Optional[str] = None
    timestamp: datetime
    tags: List[str] = []
    metadata: Dict[str, Any] = {}


class QuotaInfo(BaseModel):
    daily_limit: int = 300
    used_today: int
    remaining: int
    reset_at: datetime


class StatsResponse(BaseModel):
    period_days: int
    sent: int
    delivered: int
    opened: int
    clicked: int
    bounced: int
    unsubscribed: int
    complained: int
    open_rate: float
    click_rate: float
    bounce_rate: float