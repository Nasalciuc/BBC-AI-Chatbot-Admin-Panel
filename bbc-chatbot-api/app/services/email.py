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
    html_body = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta name="x-apple-disable-message-reformatting">
  <title>BBC Admin Panel Invitation</title>
</head>
<body style="margin:0;padding:0;background-color:#eef1f5;-webkit-font-smoothing:antialiased;font-family:'Segoe UI',Helvetica,Arial,sans-serif;">
  <!-- Preheader (hidden) -->
  <div style="display:none;max-height:0;overflow:hidden;opacity:0;">
    You've been invited to the Buy Business Class Admin Panel — activate your account.
  </div>

  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:#eef1f5;padding:32px 12px;">
    <tr>
      <td align="center">
        <table role="presentation" width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;background-color:#ffffff;border-radius:14px;overflow:hidden;box-shadow:0 8px 28px rgba(11,24,41,0.10);">

          <!-- Header -->
          <tr>
            <td style="background:linear-gradient(135deg,#0B1829 0%,#15304f 100%);padding:40px 40px 36px;text-align:center;">
              <div style="display:inline-block;border:1px solid rgba(201,165,78,0.45);border-radius:999px;padding:6px 18px;margin-bottom:18px;">
                <span style="color:#C9A54E;font-size:11px;letter-spacing:3px;text-transform:uppercase;font-weight:600;">Admin Panel</span>
              </div>
              <h1 style="margin:0;color:#ffffff;font-size:26px;font-weight:700;letter-spacing:0.5px;text-transform:uppercase;">
                Buy <span style="color:#C9A54E;">Business Class</span>
              </h1>
            </td>
          </tr>

          <!-- Body -->
          <tr>
            <td style="padding:44px 48px 24px;">
              <h2 style="margin:0 0 8px;color:#0B1829;font-size:22px;font-weight:700;">Welcome, {name}</h2>
              <p style="margin:0 0 28px;color:#5b6675;font-size:15px;line-height:1.6;">
                You have been invited to join the <strong style="color:#0B1829;">BBC Admin Panel</strong>.
                Click the button below to set your password and activate your account.
              </p>

              <!-- CTA Button -->
              <table role="presentation" cellpadding="0" cellspacing="0" style="margin:0 auto 32px;">
                <tr>
                  <td align="center" style="border-radius:10px;background:linear-gradient(135deg,#C9A54E 0%,#b8923d 100%);box-shadow:0 4px 14px rgba(201,165,78,0.4);">
                    <a href="{invite_url}" style="display:inline-block;padding:15px 44px;color:#0B1829;font-size:15px;font-weight:700;text-decoration:none;letter-spacing:0.3px;">
                      Activate Account &rarr;
                    </a>
                  </td>
                </tr>
              </table>

              <!-- Account detail card -->
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:#f7f8fa;border:1px solid #e8ebef;border-radius:10px;">
                <tr>
                  <td style="padding:18px 22px;">
                    <p style="margin:0 0 4px;color:#8a94a3;font-size:11px;letter-spacing:1px;text-transform:uppercase;font-weight:600;">Your account email</p>
                    <p style="margin:0;color:#0B1829;font-size:15px;font-weight:600;">{to_email}</p>
                  </td>
                </tr>
              </table>

              <!-- Expiry notice -->
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:20px 0 0;background-color:#fdf6e9;border-left:3px solid #C9A54E;border-radius:6px;">
                <tr>
                  <td style="padding:14px 18px;">
                    <p style="margin:0;color:#8a6d2f;font-size:13px;line-height:1.5;">
                      &#9201; This is a one-time link and expires in <strong>{expires_minutes} minutes</strong> if not used.
                    </p>
                  </td>
                </tr>
              </table>

              <!-- Fallback link -->
              <p style="margin:28px 0 0;color:#9aa3b0;font-size:12px;line-height:1.6;">
                Button not working? Copy and paste this link into your browser:<br>
                <a href="{invite_url}" style="color:#15304f;word-break:break-all;">{invite_url}</a>
              </p>
            </td>
          </tr>

          <!-- Footer -->
          <tr>
            <td style="padding:26px 48px 34px;border-top:1px solid #eef1f5;">
              <p style="margin:0;color:#aab2bd;font-size:12px;line-height:1.6;text-align:center;">
                You received this email because an administrator invited you to the BBC Admin Panel.<br>
                If you weren't expecting this, you can safely ignore it.
              </p>
              <p style="margin:14px 0 0;color:#c2c9d2;font-size:11px;text-align:center;">
                &copy; Buy Business Class &middot; All rights reserved
              </p>
            </td>
          </tr>

        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""
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
