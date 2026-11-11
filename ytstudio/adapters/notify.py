"""Events are not sent anywhere: they are written to the log. Failures also show on the Jobs page,
and a channel that needs reconnecting shows a banner."""

import logging

from ytstudio.domain.events import Event
from ytstudio.domain.models import User

log = logging.getLogger("ytstudio.events")


class LogNotifier:
    def send(self, user: User, event: Event) -> None:
        log.info("%s: %s", event.title, event.body, extra={"event": type(event).__name__})
