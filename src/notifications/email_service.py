"""SMTP transport and deterministic, escaped mail rendering."""

from email.message import EmailMessage
from email.headerregistry import Address
from email.utils import formatdate
from html import escape
import json
import smtplib
import ssl

from src.settings import Settings


def render_notification(payload: dict) -> tuple[str, str, str]:
    weekly = payload["mode"] == "weekly_digest"
    subject = f"DMF {payload['dmf_no']} - {'Weekly digest' if weekly else 'Update'}"
    lines = [subject]
    if weekly:
        lines.append(f"Period: {payload['period_start']} to {payload['period_end']} (exclusive)")
    for event in payload["events"]:
        lines.append(f"{event['created_at']} | {event['event_type']} | Event {event['id']}")
        before = event.get("before") or {}
        after = event.get("after") or {}
        if before == after:
            lines.append(f"Current record: {json.dumps(after, ensure_ascii=False)}")
        for name in sorted(before.keys() | after.keys()):
            if before.get(name) != after.get(name):
                lines.append(
                    f"{name}: {json.dumps(before.get(name), ensure_ascii=False)}"
                    f" -> {json.dumps(after.get(name), ensure_ascii=False)}"
                )
    if not payload["events"]:
        lines.append("No recorded changes in this period. This does not guarantee a successful check.")
    if weekly:
        lines.append(f"Last check: {payload.get('last_run_at') or 'Never'}")
        lines.append(f"Consecutive check failures: {payload.get('failure_count', 0)}")
    text = "\n".join(lines)
    html = '<html><body><pre style="white-space:pre-wrap">' + escape(text) + "</pre></body></html>"
    return subject, text, html


class EmailService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def send(self, recipient: str, payload: dict, message_id: str) -> None:
        address = Address(addr_spec=recipient)
        if not address.username or not address.domain:
            raise ValueError("Invalid notification recipient")
        subject, text, html = render_notification(payload)
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = self.settings.smtp_from_email
        message["To"] = recipient
        message["Date"] = formatdate(localtime=False)
        message["Message-ID"] = message_id
        message.set_content(text)
        message.add_alternative(html, subtype="html")
        context = ssl.create_default_context()
        options = {"timeout": self.settings.smtp_timeout_seconds}
        transport = smtplib.SMTP
        if self.settings.smtp_security == "ssl":
            transport = smtplib.SMTP_SSL
            options["context"] = context
        with transport(self.settings.smtp_host, self.settings.smtp_port, **options) as smtp:
            if self.settings.smtp_security == "starttls":
                smtp.ehlo()
                smtp.starttls(context=context)
                smtp.ehlo()
            if self.settings.smtp_user:
                smtp.login(self.settings.smtp_user, self.settings.smtp_password)
            refused = smtp.send_message(
                message, from_addr=self.settings.smtp_from_email, to_addrs=[recipient]
            )
            if refused:
                raise smtplib.SMTPRecipientsRefused(refused)