"""Notifiers: in-app (database), email (SMTP), Telegram. Failures are logged, never raised:
a notification must not fail the job that produced it."""

import logging
import smtplib
from email.message import EmailMessage

import httpx

from ytstudio.config import Settings
from ytstudio.domain.events import Event
from ytstudio.domain.models import User
from ytstudio.ports import Notifier, UowFactory

log = logging.getLogger(__name__)


class InAppNotifier:
    def __init__(self, uow: UowFactory):
        self.uow = uow

    def send(self, user: User, event: Event) -> None:
        with self.uow() as uow:
            uow.notifications.add(user.id, type(event).__name__, event.title, event.body, event.link)
            uow.commit()


class EmailNotifier:
    def __init__(self, s: Settings):
        self.s = s

    def send(self, user: User, event: Event) -> None:
        if not (user.notify_email and self.s.smtp_host):
            return
        msg = EmailMessage()
        msg["Subject"], msg["From"], msg["To"] = event.title, self.s.smtp_from or self.s.smtp_user, user.email
        msg.set_content(f"{event.body}\n\n{self.s.base_url}{event.link}")
        with smtplib.SMTP(self.s.smtp_host, self.s.smtp_port, timeout=20) as smtp:
            smtp.starttls()
            if self.s.smtp_user:
                smtp.login(self.s.smtp_user, self.s.smtp_password)
            smtp.send_message(msg)


class TelegramNotifier:
    def __init__(self, bot_token: str, base_url: str):
        self.token, self.base_url = bot_token, base_url

    def send(self, user: User, event: Event) -> None:
        if not (self.token and user.telegram_chat_id):
            return
        httpx.post(
            f"https://api.telegram.org/bot{self.token}/sendMessage",
            timeout=15,
            data={
                "chat_id": user.telegram_chat_id,
                "text": f"{event.title}\n{event.body}\n{self.base_url}{event.link}",
            },
        )


class FanOutNotifier:
    def __init__(self, notifiers: list[Notifier]):
        self.notifiers = notifiers

    def send(self, user: User, event: Event) -> None:
        for n in self.notifiers:
            try:
                n.send(user, event)
            except Exception:
                log.exception("notifier %s failed for %s", type(n).__name__, type(event).__name__)
