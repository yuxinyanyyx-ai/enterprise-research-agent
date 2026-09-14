from unittest.mock import MagicMock

import pytest

from src.notifications.email_service import EmailService, render_notification
from src.settings import ConfigurationError, load_settings


def email_settings(**overrides):
    env = {
        "DMF_HISTORY_ENABLED": "true", "DMF_WATCHLIST_ENABLED": "true",
        "DMF_NOTIFICATION_ENABLED": "true", "SMTP_HOST": "smtp.example.com",
        "SMTP_FROM_EMAIL": "dmf@example.com", "SMTP_USER": "dmf",
        "SMTP_PASSWORD": "test-secret",
    }
    return load_settings(env | overrides, require_token=False)


def test_transport_and_escaping(monkeypatch):
    transport = MagicMock()
    smtp = transport.return_value.__enter__.return_value
    smtp.send_message.return_value = {}
    monkeypatch.setattr("src.notifications.email_service.smtplib.SMTP", transport)
    payload = {"dmf_no": "001", "mode": "immediate", "events": [
        {"id": "event-1", "created_at": "2026-09-07", "event_type": "field_changed",
         "before": {"name": "A"}, "after": {"name": "<script>B</script>"}}
    ]}
    EmailService(email_settings()).send("user@example.com", payload, "<job-1@dmf.local>")
    smtp.starttls.assert_called_once()
    smtp.login.assert_called_once_with("dmf", "test-secret")
    message = smtp.send_message.call_args.args[0]
    assert message["Message-ID"] == "<job-1@dmf.local>"
    assert smtp.send_message.call_args.kwargs["to_addrs"] == ["user@example.com"]
    assert "&lt;script&gt;" in message.get_body(preferencelist=("html",)).get_content()
    assert "test-secret" not in repr(email_settings())


@pytest.mark.parametrize("overrides", [
    {"SMTP_HOST": ""}, {"SMTP_FROM_EMAIL": "invalid"}, {"SMTP_PORT": "65536"},
    {"SMTP_SECURITY": "none"}, {"DMF_NOTIFICATION_WEEKLY_DAY": "7"},
    {"DMF_NOTIFICATION_WEEKLY_HOUR": "24"}, {"DMF_NOTIFICATION_TIMEZONE": "invalid"},
    {"SMTP_PASSWORD": ""}, {"DMF_WATCHLIST_ENABLED": "false"},
])
def test_configuration_rejects_invalid_values(overrides):
    with pytest.raises(ConfigurationError):
        email_settings(**overrides)


def test_ssl_and_empty_digest(monkeypatch):
    transport = MagicMock()
    smtp = transport.return_value.__enter__.return_value
    smtp.send_message.return_value = {}
    monkeypatch.setattr("src.notifications.email_service.smtplib.SMTP_SSL", transport)
    payload = {"dmf_no": "001", "mode": "weekly_digest", "events": [],
               "period_start": "start", "period_end": "end"}
    EmailService(email_settings(SMTP_SECURITY="ssl")).send("user@example.com", payload, "<job@dmf.local>")
    assert transport.call_args.args[1] == 465
    smtp.starttls.assert_not_called()
    assert "does not guarantee" in render_notification(payload)[1]


def test_refused_recipient_is_a_delivery_failure(monkeypatch):
    import smtplib

    transport = MagicMock()
    smtp = transport.return_value.__enter__.return_value
    smtp.send_message.return_value = {"user@example.com": (450, b"Try later")}
    monkeypatch.setattr("src.notifications.email_service.smtplib.SMTP", transport)
    payload = {"dmf_no": "001", "mode": "immediate", "events": []}
    with pytest.raises(smtplib.SMTPRecipientsRefused):
        EmailService(email_settings()).send("user@example.com", payload, "<job@dmf.local>")


def test_expiration_mail_includes_unchanged_valid_date():
    payload = {"dmf_no": "001", "mode": "immediate", "events": [
        {"id": "event", "created_at": "2026-09-07", "event_type": "valid_date_expired",
         "before": {"valid_date": "2026-09-06"}, "after": {"valid_date": "2026-09-06"}}
    ]}
    assert "2026-09-06" in render_notification(payload)[1]