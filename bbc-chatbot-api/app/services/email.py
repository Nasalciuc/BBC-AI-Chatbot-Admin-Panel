"""Email service — invite emails via Postmark HTTP API."""
import logging
import secrets
import string
import httpx
from config.settings import settings

logger = logging.getLogger(__name__)


def generate_temp_password(length: int = 12) -> str:
    alphabet = string.ascii_letters + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))


def generate_invite_token(length: int = 32) -> str:
    """URL-safe one-time token for invite/set-password links."""
    return secrets.token_urlsafe(length)


async def send_invite_email(to_email: str, name: str, invite_url: str, expires_minutes: int) -> bool:
    if not settings.postmark_token:
        logger.warning("POSTMARK_TOKEN not set — invite email skipped")
        return False
    html_body = f"""
    <div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;">
      <div style="background:#0B1829;padding:24px;text-align:center;">
        <h1 style="color:#C9A54E;margin:0;">Buy Business Class</h1>
      </div>
      <div style="padding:32px;">
        <h2 style="color:#0B1829;">Welcome, {name}!</h2>
        <p>You have been invited to the BBC Admin Panel.</p>
        <div style="background:#f5f5f5;border-radius:8px;padding:20px;margin:24px 0;">
                    <p><strong>Set Password Link:</strong> <a href="{invite_url}" style="color:#C9A54E;word-break:break-all;">Activate account</a></p>
          <p><strong>Email:</strong> {to_email}</p>
        </div>
                <p style="color:#888;font-size:14px;">This one-time link expires in {expires_minutes} minutes if not used.</p>
      </div>
    </div>"""
    text_body = (
        f"Welcome {name}!\n\n"
                f"Set your password: {invite_url}\n"
        f"Email: {to_email}\n"
                f"\nThis one-time link expires in {expires_minutes} minutes if not used."
    )
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.post(
                "https://api.postmarkapp.com/email",
                headers={
                    "X-Postmark-Server-Token": settings.postmark_token,
                    "Content-Type": "application/json",
                },
                json={
                    "From": settings.email_from,
                    "To": to_email,
                    "Subject": "You've been invited to BBC Admin Panel",
                    "HtmlBody": html_body,
                    "TextBody": text_body,
                    "MessageStream": "outbound",
                },
            )
            if res.status_code == 200:
                logger.info(f"Invite email sent to {to_email}")
                return True
            logger.error(f"Postmark {res.status_code}: {res.text}")
            return False
    except Exception as e:
        logger.error(f"send_invite_email error: {e}")
        return False
