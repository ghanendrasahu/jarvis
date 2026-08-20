from __future__ import annotations

import smtplib
from datetime import UTC, datetime
from email.message import EmailMessage
from typing import Any

from app.config import Settings
from app.core.calendar import CalendarStore
from app.core.scheduler import Scheduler, parse_reminder_when
from app.tools.registry import ToolRegistry


def _copy_to_clipboard(text: str) -> str:
    import pyperclip

    pyperclip.copy(text)
    return text


def _read_clipboard() -> str:
    import pyperclip

    try:
        return str(pyperclip.paste() or "").strip()
    except Exception:
        return ""


def _send_smtp_email(settings: Settings, to: str, subject: str, body: str) -> None:
    host = settings.smtp_host
    username = settings.smtp_username
    password = settings.smtp_password
    if not host or not username or not password:
        raise RuntimeError(
            "SMTP is not configured. Set SMTP_HOST, SMTP_USERNAME, SMTP_PASSWORD (and SMTP_FROM) in .env."
        )
    message = EmailMessage()
    message["From"] = settings.smtp_from or username
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)
    with smtplib.SMTP(host, settings.smtp_port, timeout=15) as server:
        server.starttls()
        server.login(username, password)
        server.send_message(message)


def _parse_start_utc(value: str) -> str:
    value = (value or "").strip()
    try:
        return datetime.fromisoformat(value).astimezone(UTC).isoformat()
    except ValueError as exc:
        raise ValueError(f"Invalid start time '{value}'. Use ISO format (e.g. 2026-08-19T14:30).") from exc


def register_productivity_tools(
    registry: ToolRegistry,
    settings: Settings,
    scheduler: Scheduler | None = None,
    calendar: CalendarStore | None = None,
) -> None:
    def send_email(args: dict[str, Any]) -> dict[str, Any]:
        to = str(args.get("to", "")).strip()
        subject = str(args.get("subject", "")).strip() or "(no subject)"
        body = str(args.get("body", "")).strip()
        if not to or "@" not in to:
            raise ValueError("Missing a valid recipient address.")
        if not body:
            raise ValueError("Email body is required.")
        _send_smtp_email(settings, to, subject, body)
        return {"result_text": f"Email sent to {to}.", "to": to, "subject": subject}

    def announce(args: dict[str, Any]) -> dict[str, Any]:
        message = str(args.get("message", "")).strip()
        if not message:
            raise ValueError("Missing message.")
        return {"message": message, "result_text": message}

    if scheduler is not None:
        def set_reminder(args: dict[str, Any]) -> dict[str, Any]:
            message = str(args.get("message", "")).strip()
            when = str(args.get("when", "")).strip()
            if not message:
                raise ValueError("Missing reminder message.")
            at = parse_reminder_when(when)
            job = scheduler.add_once(f"announce {message}", at)
            return {
                "result_text": f"Reminder set for {at.isoformat()} (id={job['id']}).",
                "scheduled_for": at.isoformat(),
                "id": job["id"],
            }

        registry.register(
            name="set_reminder",
            description=(
                "Schedule a one-shot reminder that announces a message. 'when' accepts "
                "'in N minutes', 'in N hours', 'at HH:MM', or 'tomorrow at HH:MM'."
            ),
            risk="low",
            parameters={"message": "string", "when": "string"},
            handler=set_reminder,
        )

    if calendar is not None:
        def add_event(args: dict[str, Any]) -> dict[str, Any]:
            title = str(args.get("title", "")).strip()
            start = str(args.get("start", "")).strip()
            duration = int(args.get("duration_minutes", 60))
            notes = str(args.get("notes", "")).strip()
            event = calendar.add_event(title, _parse_start_utc(start), duration_minutes=duration, notes=notes)
            return {
                "result_text": f"Event '{title}' added for {event['start_utc']} (id={event['id']}).",
                "event": event,
            }

        def list_events(args: dict[str, Any]) -> dict[str, Any]:
            events = calendar.list_events(limit=int(args.get("limit", 50)))
            return {"count": len(events), "events": events}

        def upcoming_events(args: dict[str, Any]) -> dict[str, Any]:
            events = calendar.upcoming_events(hours=float(args.get("hours", 24)), limit=int(args.get("limit", 20)))
            return {"count": len(events), "events": events}

        def delete_event(args: dict[str, Any]) -> dict[str, Any]:
            event_id = str(args.get("id", "")).strip()
            if not calendar.delete_event(event_id):
                raise ValueError(f"No event with id '{event_id}'.")
            return {"result_text": f"Deleted event {event_id}.", "deleted": event_id}

        registry.register(
            name="add_event",
            description="Add a calendar event with an ISO start time (e.g. 2026-08-19T14:30) and optional notes.",
            risk="low",
            parameters={"title": "string", "start": "string", "duration_minutes": "integer", "notes": "string"},
            handler=add_event,
        )
        registry.register(
            name="list_events",
            description="List all calendar events.",
            risk="low",
            parameters={"limit": "integer"},
            handler=list_events,
        )
        registry.register(
            name="upcoming_events",
            description="List calendar events starting within the next N hours.",
            risk="low",
            parameters={"hours": "number", "limit": "integer"},
            handler=upcoming_events,
        )
        registry.register(
            name="delete_event",
            description="Delete a calendar event by its id.",
            risk="low",
            parameters={"id": "string"},
            handler=delete_event,
        )

    def copy_to_clipboard(args: dict[str, Any]) -> dict[str, Any]:
        text = str(args.get("text", ""))
        if not text:
            raise ValueError("Missing text to copy.")
        _copy_to_clipboard(text)
        return {"result_text": f"Copied {len(text)} characters to the clipboard.", "chars": len(text)}

    def read_clipboard(_: dict[str, Any]) -> dict[str, Any]:
        text = _read_clipboard()
        return {"text": text, "result_text": text[:300], "chars": len(text)}

    registry.register(
        name="send_email",
        description="Send an email via the configured SMTP server.",
        risk="high",
        parameters={"to": "string", "subject": "string", "body": "string"},
        handler=send_email,
    )
    registry.register(
        name="announce",
        description="Announce a message back to the user.",
        risk="low",
        parameters={"message": "string"},
        handler=announce,
    )
    registry.register(
        name="copy_to_clipboard",
        description="Copy text to the system clipboard.",
        risk="low",
        parameters={"text": "string"},
        handler=copy_to_clipboard,
    )
    registry.register(
        name="read_clipboard",
        description="Read the current system clipboard text.",
        risk="low",
        parameters={},
        handler=read_clipboard,
    )
