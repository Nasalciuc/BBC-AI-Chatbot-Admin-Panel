"""Email service — invite emails via Postmark HTTP API."""
import html
import logging
import re
import secrets
import string

import httpx

from app.security.pii import mask_email
from config.settings import settings

logger = logging.getLogger(__name__)

# No address reaches a log line (PII at INFO and above): a recipient is
# written masked, and what Postmark answers — it names the address it
# refused — is written by its error code, with every address taken out.
_ADDRESS = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


def _postmark_said(res) -> str:
    """Postmark's refusal, for a log line: its error code and its message
    without the addresses."""
    try:
        data = res.json()
    except Exception:  # noqa: silent — a body that is not JSON: the caller's line still carries the HTTP status
        data = None
    if not isinstance(data, dict):
        return "(no JSON body)"
    message = _ADDRESS.sub("<address>", str(data.get("Message") or ""))[:200]
    return f"code={data.get('ErrorCode')} {message}".rstrip()


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
                logger.info(f"Invite email sent to {mask_email(to_email)}")
                return True
            logger.error(f"Postmark {res.status_code}: {_postmark_said(res)}")
            return False
    except Exception as e:
        # The kind of error, not its text: an HTTP client's error can quote
        # what was being sent (a header value, a piece of the body).
        logger.error(f"send_invite_email error: {type(e).__name__}")
        return False


async def send_attention_email(
    *,
    tag: str,
    chat_number: int | str | None,
    created_at: str | None,
    customer_name: str | None,
    conversation_id: str,
) -> bool:
    """Supervisor attention email — four fields only, one per chat.

    Fired for attention-worthy cases (returning customer / needs attention),
    never for every AI-handled chat. Fire-and-forget safe.
    """
    if not settings.postmark_token:
        logger.warning("POSTMARK_TOKEN not set — skipping attention email")
        return False

    to_addr = (settings.attention_email_to or settings.super_alert_email or "").strip()
    if not to_addr:
        logger.warning("No attention email recipient configured — skip")
        return False

    chat_link = f"{settings.admin_panel_url.rstrip('/')}/chats?highlight={conversation_id}"
    number_label = f"#{chat_number}" if chat_number is not None else "—"
    tag_label = {
        "fresh": "Fresh",
        "active": "Active",
        "main_queue": "Main Queue",
        "completed": "Completed",
        "abandoned": "Abandoned",
        "no_engagement": "No engagement",
    }.get(tag, tag)
    name = (customer_name or "Anonymous").strip() or "Anonymous"
    created = created_at or "—"

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
                    "To": to_addr,
                    "Subject": f"Chat {number_label} needs attention — {tag_label}",
                    "MessageStream": "outbound",
                    "HtmlBody": (
                        "<h2>Chat needs attention</h2>"
                        f"<p><strong>Tag:</strong> {tag_label}</p>"
                        f"<p><strong>Chat number:</strong> {number_label}</p>"
                        f"<p><strong>Created:</strong> {created}</p>"
                        f"<p><strong>Customer:</strong> {name}</p>"
                        f'<p><a href="{chat_link}">Open conversation in admin panel</a></p>'
                    ),
                    "TextBody": (
                        "Chat needs attention.\n\n"
                        f"Tag: {tag_label}\n"
                        f"Chat number: {number_label}\n"
                        f"Created: {created}\n"
                        f"Customer: {name}\n\n"
                        f"Open: {chat_link}\n"
                    ),
                },
            )
            if res.status_code == 200:
                logger.info(
                    f"Attention email sent for conv {conversation_id} "
                    f"({number_label}) → {mask_email(to_addr)}"
                )
                return True
            logger.error(f"Attention email failed: {res.status_code} {_postmark_said(res)}")
            return False
    except Exception as e:
        logger.error(f"Attention email exception: {type(e).__name__}")
        return False


async def send_super_alert_email(
    *,
    conversation_id: str,
    visitor_name: str | None,
    visitor_phone: str | None,
    visitor_email: str | None,
    tunnel: str,
    last_message: str,
    chat_number: str | int | None = None,
) -> bool:
    """Alert super@ when a client is chatting but no agents are available.
    Fire-and-forget safe: returns False on any failure, never raises.

    The subject is UNIQUE per chat (number, or short conv-id fallback) —
    Gmail threads by subject, and a static subject collapsed alerts from
    DIFFERENT chats into one ever-growing buried thread. The number also
    leads the body so it shows in the Gmail list preview.

    No client PII in the body (owner request, 02-Jul): name/phone/email are
    accepted but intentionally NOT displayed — emails get forwarded. Full
    client details stay in the admin panel behind the link. The chat number
    is not PII."""
    chat_link = f"{settings.admin_panel_url.rstrip('/')}/chats?highlight={conversation_id}"
    safe_message = (last_message or "")[:500]
    num = f"#{chat_number}" if chat_number else f"({conversation_id[:8]})"

    return await _send_super_inbox_email(
        subject=f"Chat {num} waiting — no agents online ({tunnel})",
        html_body=(
            "<h2>A client is chatting with no agents available</h2>"
            f"<p><strong>Chat:</strong> {num}</p>"
            f"<p><strong>Tunnel:</strong> {tunnel}</p>"
            f'<p><strong>Latest message:</strong> "{safe_message}"</p>'
            f'<p><a href="{chat_link}">Open conversation in admin panel</a></p>'
            '<p style="color:#888;font-size:12px;">'
            "Client details are available in the admin panel.</p>"
        ),
        text_body=(
            f"Chat: {num}\n"
            "A client is chatting with no agents available.\n\n"
            f"Tunnel: {tunnel}\n"
            f'Latest message: "{safe_message}"\n\n'
            f"Open: {chat_link}\n\n"
            "Client details are available in the admin panel."
        ),
        what=f"super alert email for conv {conversation_id}",
    )


def _minutes(seconds: int) -> str:
    m = max(1, round((seconds or 0) / 60))
    return f"{m} min" if m < 60 else f"{m // 60} h {m % 60:02d} min"


async def send_queue_alert_email(*, waiting: int = 0, oldest_seconds: int = 0, recovered: bool = False) -> bool:
    """The queue's own e-mail to super@ (cron.run_queue_stall_alert): how many wait for a person and since
    when, or that the line moves again. It names no chat and links to the chats list, where the queue is: it
    used to borrow the chat-waiting e-mail with "-" for a chat id, so its subject read "Chat (-) waiting" and
    its link opened a chat that doesn't exist (GET /api/conversations/- → 502, 20-21 Nov 2026).
    Fire-and-forget safe: False on any failure, never raises."""
    link = f"{settings.admin_panel_url.rstrip('/')}/chats"
    if recovered:
        # Over is "nobody waits past the alert's threshold", not an empty queue: clients may still wait, for less
        # (cron.run_queue_stall_alert). The e-mail says which (CodeRabbit #274).
        subject = "Queue no longer stalled"
        if waiting > 0:
            people = "1 client is" if waiting == 1 else f"{waiting} clients are"
            lead = f"The queue is no longer stalled: {people} waiting, the oldest {_minutes(oldest_seconds)}."
        else:
            lead = "The queue is no longer stalled: nobody is waiting for a person."
        detail = ""
    else:
        people = "1 client" if waiting == 1 else f"{waiting} clients"
        subject = f"Queue stalled — {people} waiting, the oldest {_minutes(oldest_seconds)}"
        lead = ("1 client is waiting for a person and nobody has taken the chat." if waiting == 1
                else f"{waiting} clients are waiting for a person and nobody has taken their chats.")
        detail = f"The oldest has waited {_minutes(oldest_seconds)}."
    return await _send_super_inbox_email(
        subject=subject,
        html_body=(
            f"<h2>{lead}</h2>"
            + (f"<p>{detail}</p>" if detail else "")
            + f'<p><a href="{link}">Open the chats</a></p>'
        ),
        text_body=f"{lead}\n" + (f"{detail}\n" if detail else "") + f"\nOpen the chats: {link}",
        what="queue recovered email" if recovered else "queue stalled email",
    )


async def send_ops_alert_email(subject: str, body: str, *, cc_super: bool = False) -> bool:
    """Infrastructure alerts (saturation, stalls) — NOT a customer event.

    send_super_alert_email builds a fixed 'Chat N waiting — no agents online'
    subject; routing an infrastructure alert through it puts the wrong
    incident in the recipient's inbox list and the real reason only in the
    body. Same transport and recipient, honest subject."""
    return await _send_super_inbox_email(
        subject=subject,
        html_body=f"<pre style=\"font-family:monospace\">{html.escape(body)}</pre>",
        text_body=body,
        what=f"ops alert email ({subject})",
        to_override=settings.ops_alert_email or None,
        cc=settings.super_alert_email if cc_super else None,
    )


async def _send_super_inbox_email(
    *, subject: str, html_body: str, text_body: str, what: str,
    to_override: str | None = None, cc: str | None = None,
) -> bool:
    """The one transport to super@. Extracted so an infrastructure alert can
    have its own subject without duplicating Postmark configuration — two
    copies of the credentials/recipient is how they drift apart.

    Fire-and-forget safe: returns False on any failure, never raises."""
    if not settings.postmark_token:
        logger.warning(f"POSTMARK_TOKEN not set — skipping {what}")
        return False
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
                    "To": to_override or settings.super_alert_email,
                    **({"Cc": cc} if cc and cc != (to_override or settings.super_alert_email) else {}),
                    "Subject": subject,
                    "MessageStream": "outbound",
                    "HtmlBody": html_body,
                    "TextBody": text_body,
                },
            )
            if res.status_code == 200:
                # The address it really went to (an override wins), masked.
                logger.info(f"Sent {what} → {mask_email(to_override or settings.super_alert_email)}")
                return True
            logger.error(f"Failed {what}: {res.status_code} {_postmark_said(res)}")
            return False
    except Exception as e:
        logger.error(f"Exception sending {what}: {type(e).__name__}")
        return False


async def send_qa_sample_email(to: list[str], day: str, lines: list[dict]) -> bool:
    """The morning QA sample (app.services.qa_sample): chat numbers, agents and reasons — never a client's
    name or contact. One e-mail to every active QA person. Never raises; False on any failure."""
    recipients = ", ".join(sorted({t.strip() for t in to if t and t.strip()}))
    if not settings.postmark_token or not recipients:
        logger.warning("POSTMARK_TOKEN or recipients missing — skipping the QA sample email")
        return False
    link = f"{settings.admin_panel_url.rstrip('/')}/qa?day={day}"
    rows_html = "".join(
        f"<tr><td>#{html.escape(str(l.get('chat_number') or '—'))}</td>"
        f"<td>{html.escape(l.get('agent') or '—')}</td>"
        f"<td>{html.escape('; '.join(l.get('reasons') or []))}</td></tr>"
        for l in lines
    )
    rows_text = "\n".join(
        f"#{l.get('chat_number') or '—'}  {l.get('agent') or '—'}  {'; '.join(l.get('reasons') or [])}" for l in lines
    )
    subject = f"QA: {len(lines)} chat{'s' if len(lines) != 1 else ''} to check from {day}"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.post(
                "https://api.postmarkapp.com/email",
                headers={"X-Postmark-Server-Token": settings.postmark_token, "Content-Type": "application/json"},
                json={
                    "From": settings.email_from,
                    "To": recipients,
                    "Subject": subject,
                    "MessageStream": "outbound",
                    "HtmlBody": (
                        f"<h2>Chats to check from {html.escape(day)}</h2>"
                        "<table cellpadding=\"6\"><tr><th align=\"left\">Chat</th><th align=\"left\">Agent</th>"
                        f"<th align=\"left\">Why</th></tr>{rows_html}</table>"
                        f'<p><a href="{link}">Open the QA page</a></p>'
                    ),
                    "TextBody": f"Chats to check from {day}\n\n{rows_text}\n\nOpen: {link}\n",
                },
            )
        if res.status_code == 200:
            logger.info(f"QA sample email sent for {day}: {len(lines)} chats → {len(to)} people")
            return True
        logger.error(f"QA sample email failed: {res.status_code} {_postmark_said(res)}")
        return False
    except Exception as e:
        logger.error(f"QA sample email exception: {type(e).__name__}")
        return False
