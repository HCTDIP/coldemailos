"""
ColdEmailOS CLI — Command-line interface for sending, campaigns, management
"""
import os
import asyncio
import json
from typing import Optional, List
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich import print as rprint

from src.models import SenderType, SendRequest, SendResponse, CampaignCreate, CampaignStep
from src.services.email_service import EmailService, get_email_service
from src.brevo_client import BrevoClient, get_brevo_client, close_brevo_client

app = typer.Typer(
    name="coldemailos",
    help="🚀 ColdEmailOS — Zero-cost cold email infrastructure",
    add_completion=False,
    no_args_is_help=True,
)

console = Console()


def get_service() -> EmailService:
    return get_email_service()


def get_brevo() -> BrevoClient:
    return get_brevo_client()


# ========== Send Commands ==========
@app.command()
def send(
    to: str = typer.Option(..., "--to", "-t", help="Recipient email"),
    subject: str = typer.Option(..., "--subject", "-s", help="Email subject"),
    template: Optional[str] = typer.Option(None, "--template", help="Template name (e.g., cold_outreach_v1)"),
    html: Optional[str] = typer.Option(None, "--html", help="Raw HTML content"),
    text: Optional[str] = typer.Option(None, "--text", help="Raw text content"),
    variables: str = typer.Option("{}", "--vars", "-v", help="JSON variables for template"),
    sender: SenderType = typer.Option(SenderType.LEADFORGE, "--sender", help="Sender identity"),
    tags: str = typer.Option("", "--tags", help="Comma-separated tags"),
):
    """Send a single email"""
    async def _send():
        svc = get_service()
        vars_dict = json.loads(variables) if variables else {}
        tag_list = [t.strip() for t in tags.split(",") if t.strip()]

        with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}")) as progress:
            task = progress.add_task("Sending...", total=None)
            result = await svc.send(
                to=to,
                subject=subject,
                template=template,
                html=html,
                text=text,
                variables=vars_dict,
                sender=sender,
                tags=tag_list,
            )

        if result.success:
            rprint(f"[green]✅ Sent![/green] Message ID: {result.message_id}")
        else:
            rprint(f"[red]❌ Failed:[/red] {result.error}")
            raise typer.Exit(1)

    asyncio.run(_send())


@app.command()
def send_batch(
    file: Path = typer.Argument(..., help="JSON file with array of send requests"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Validate only, don't send"),
):
    """Send multiple emails from JSON file"""
    async def _batch():
        if not file.exists():
            rprint(f"[red]File not found: {file}[/red]")
            raise typer.Exit(1)

        with open(file) as f:
            requests = json.load(f)

        svc = get_service()
        results = []

        with Progress() as progress:
            task = progress.add_task("Sending batch...", total=len(requests))
            for req in requests:
                if not dry_run:
                    result = await svc.send(**req)
                    results.append(result)
                else:
                    results.append(SendResponse(success=True, message_id="dry-run"))
                progress.advance(task)

        success = sum(1 for r in results if r.success)
        rprint(f"[green]Done: {success}/{len(requests)} sent[/green]")

    asyncio.run(_batch())


# ========== Campaign Commands ==========
@app.command()
def campaign_create(
    name: str = typer.Option(..., "--name", "-n"),
    list_id: str = typer.Option(..., "--list", "-l"),
    steps: str = typer.Option(..., "--steps", help="JSON array of steps"),
    sender: SenderType = typer.Option(SenderType.LEADFORGE, "--sender"),
):
    """Create a drip campaign"""
    step_list = json.loads(steps)
    campaign = CampaignCreate(
        name=name,
        steps=[CampaignStep(**s) for s in step_list],
        list_id=list_id,
        sender=sender,
    )
    svc = get_service()
    created = svc.create_campaign(campaign)

    rprint(Panel.fit(
        f"[bold]Campaign Created[/bold]\n"
        f"ID: {created.id}\n"
        f"Name: {created.name}\n"
        f"Steps: {len(created.steps)}\n"
        f"List: {created.list_id}\n"
        f"Sender: {created.sender.value}",
        title="📋 Campaign",
        border_style="green",
    ))


# ========== Quota & Stats ==========
@app.command()
def quota():
    """Check daily quota"""
    async def _quota():
        svc = get_service()
        q = await svc.get_quota()

        table = Table(title="📊 Daily Quota")
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="green")
        table.add_row("Daily Limit", str(q.daily_limit))
        table.add_row("Used Today", str(q.used_today))
        table.add_row("Remaining", str(q.remaining))
        table.add_row("Resets At", q.reset_at.strftime("%Y-%m-%d %H:%M UTC"))
        console.print(table)

    asyncio.run(_quota())


@app.command()
def stats(days: int = typer.Option(7, "--days", "-d")):
    """Show email statistics"""
    async def _stats():
        svc = get_service()
        s = await svc.get_stats(days)

        table = Table(title=f"📈 Stats (Last {days} days)")
        table.add_column("Metric", style="cyan")
        table.add_column("Count", style="green")
        table.add_column("Rate", style="yellow")

        table.add_row("Sent", str(s.sent), "—")
        table.add_row("Delivered", str(s.delivered), f"{(s.delivered/s.sent*100) if s.sent else 0:.1f}%")
        table.add_row("Opened", str(s.opened), f"{s.open_rate:.1f}%")
        table.add_row("Clicked", str(s.clicked), f"{s.click_rate:.1f}%")
        table.add_row("Bounced", str(s.bounced), f"{s.bounce_rate:.1f}%")
        table.add_row("Unsubscribed", str(s.unsubscribed), "—")
        table.add_row("Complained", str(s.complained), "—")

        console.print(table)

    asyncio.run(_stats())


# ========== Sender Management ==========
@app.command()
def senders_list():
    """List all configured senders"""
    async def _list():
        brevo = get_brevo()
        senders = await brevo.list_senders()

        table = Table(title="📧 Configured Senders")
        table.add_column("Email", style="cyan")
        table.add_column("Name", style="green")
        table.add_column("Active", style="yellow")
        table.add_column("ID", style="dim")

        for s in senders:
            table.add_row(s.email, s.name, "✅" if s.active else "❌", str(s.id or "N/A"))

        console.print(table)

    asyncio.run(_list())


@app.command()
def senders_ensure():
    """Ensure all 6 default senders exist"""
    async def _ensure():
        svc = get_service()
        with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}")) as progress:
            task = progress.add_task("Ensuring senders...", total=None)
            created = await svc.ensure_senders()

        if created:
            rprint(f"[green]Created {len(created)} senders:[/green]")
            for s in created:
                rprint(f"  • {s.value}")
        else:
            rprint("[green]All senders already exist[/green]")

    asyncio.run(_ensure())


# ========== Template Management ==========
@app.command()
def templates_list():
    """List available templates"""
    svc = get_service()
    templates = svc.list_templates()

    table = Table(title="📝 Available Templates")
    table.add_column("Template", style="cyan")
    table.add_column("Files", style="green")

    for t in templates:
        html_exists = "✅" if Path(f"src/services/templates/{t}.html.j2").exists() else "❌"
        txt_exists = "✅" if Path(f"src/services/templates/{t}.txt.j2").exists() else "❌"
        table.add_row(t, f"HTML: {html_exists} | Text: {txt_exists}")

    console.print(table)


@app.command()
def template_render(
    template: str = typer.Argument(..., help="Template name"),
    variables: str = typer.Option("{}", "--vars", "-v", help="JSON variables"),
):
    """Render a template with variables (preview)"""
    svc = get_service()
    vars_dict = json.loads(variables) if variables else {}

    try:
        html, text = svc.render_template(template, vars_dict)

        rprint(Panel(html[:2000] + ("..." if len(html) > 2000 else ""), title=f"HTML Preview: {template}", border_style="blue"))
        rprint(Panel(text, title=f"Text Preview: {template}", border_style="green"))
    except Exception as e:
        rprint(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


# ========== Suppression Management ==========
@app.command()
def suppressions_list():
    """List suppressed emails"""
    svc = get_service()
    sups = svc._suppression

    if not sups:
        rprint("[yellow]No suppressions[/yellow]")
        return

    table = Table(title="🚫 Suppressed Emails")
    table.add_column("Email", style="cyan")
    table.add_column("Reason", style="red")

    for email, reason in sups.items():
        table.add_row(email, reason.value)

    console.print(table)


@app.command()
def suppression_remove(email: str = typer.Argument(..., help="Email to remove from suppression")):
    """Remove email from suppression list"""
    svc = get_service()
    svc.remove_suppression(email)
    rprint(f"[green]Removed {email} from suppression list[/green]")


# ========== Lead Capture Test ==========
@app.command()
def test_lead(
    email: str = typer.Argument(..., help="Test email"),
    first_name: str = typer.Option("Test", "--first"),
    company: str = typer.Option("TestCorp", "--company"),
):
    """Test lead capture flow"""
    async def _test():
        svc = get_service()
        contact = await svc.brevo.create_contact(
            Contact(email=email, first_name=first_name, company=company, tags=["test", "cli"])
        )
        rprint(f"[green]Contact created: {contact.id}[/green]")

        result = await svc.send(
            to=email,
            subject="Test from ColdEmailOS CLI",
            template="cold_outreach_v1",
            variables={"first_name": first_name, "company": company, "pain_point": "testing", "proof": "CLI"},
            sender=SenderType.TEST,
        )
        if result.success:
            rprint(f"[green]Test email sent: {result.message_id}[/green]")
        else:
            rprint(f"[red]Failed: {result.error}[/red]")

    asyncio.run(_test())


# ========== Deploy Helpers ==========
@app.command()
def deploy_check():
    """Pre-deployment checks"""
    checks = [
        ("BREVO_API_KEY", bool(os.getenv("BREVO_API_KEY"))),
        ("Templates dir", Path("src/services/templates").exists()),
        ("Requirements", Path("requirements.txt").exists()),
        ("Dockerfile", Path("deploy/Dockerfile").exists()),
        ("Railway config", Path("deploy/railway.toml").exists()),
    ]

    table = Table(title="🚀 Deploy Readiness")
    table.add_column("Check", style="cyan")
    table.add_column("Status", style="green")

    all_pass = True
    for name, passed in checks:
        table.add_row(name, "✅ PASS" if passed else "❌ FAIL")
        if not passed:
            all_pass = False

    console.print(table)

    if all_pass:
        rprint("\n[bold green]✅ Ready to deploy![/bold green]")
    else:
        rprint("\n[bold red]❌ Fix failures before deploy[/bold red]")
        raise typer.Exit(1)


# ========== Entry Point ==========
def main():
    try:
        app()
    finally:
        # Cleanup
        try:
            asyncio.run(close_brevo_client())
        except Exception:
            pass


if __name__ == "__main__":
    main()