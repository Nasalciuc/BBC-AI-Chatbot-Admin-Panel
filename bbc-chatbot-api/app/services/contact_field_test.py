"""The owner's test of 22 Nov 2026: the contact field at once after the card's yes, or the choice of channel first?

On 19-22 Nov 74 visitors tapped the Kayak card's yes and 37 of them left a contact. 30 never chose Text message or
Email: they left the site a median 34 s after the tap. Of the 59 who opened the field, 49 left their contact
(PostHog). The cards that know no whole trip did worse: "Get a fare quote" on the ads card, the reader's card and
the tapped note brought 7 contacts from 65 answers, with the route, the dates and the channel asked one after
another.

Half the visitors (CONTACT_FIELD_TEST_PERCENT, the owner's 50) are on the field side, bucketed by visitor id like
every experiment (app.experiments), so a visitor keeps their side across visits:

- the yes on a card that says the whole trip back opens the field at once: SMS on a phone, email on a computer
  (card_words.device_channel). Its words ask for that one contact (card_words.confirm, `field`), and the other
  channel is one tap away;
- "Get a fare quote" on a card that knows no whole trip (proactive.CONTACT_FIRST_TRIGGERS: the ads card, the
  reader's card and the tapped note) is answered at once, contact first, with the same field open
  (card_words.contact_first). The trip is asked once the contact is in (card_words.channel_done, trip_known).

The choice side is as before. Only a widget that opens the field at once (the offer's `opens_field`) takes part: a
widget cached from before is on neither side. 0 in CONTACT_FIELD_TEST_PERCENT ends the test without a deploy.

The side is named on the card's answer (`contact_test`). The widget sends it back at /chat/init, and it is written on
the chat's metadata only when it is this visitor id's side (contact_test, plus contact_field: the field opened at
once), so leads per card answer read per side.
"""

from __future__ import annotations

import logging
from typing import Optional

from app.flags import flag

logger = logging.getLogger(__name__)

EXPERIMENT = "contact_field"
FIELD, CHOICE = "field", "choice"
SIDES: tuple[str, ...] = (FIELD, CHOICE)
CHANNELS: tuple[str, ...] = ("sms", "email")
# The chat's metadata, server-made: the widget's own metadata never carries these (allowed_widget_metadata).
SIDE_KEY = "contact_test"
FIELD_KEY = "contact_field"

CONTACT_FIELD_HEALTH: dict[str, int] = {
    "field": 0,    # card answers on the field side (the field opens with the yes)
    "choice": 0,   # card answers on the choice side (as before)
}


def percent() -> int:
    try:
        return max(0, min(100, int(flag("CONTACT_FIELD_TEST_PERCENT", 50) or 0)))
    except (TypeError, ValueError) as e:
        logger.warning(f"[contact-field] CONTACT_FIELD_TEST_PERCENT unreadable ({e}): the test is off")
        return 0


def side(visitor_id: Optional[str]) -> Optional[str]:
    """"field" or "choice" while the test runs, stable per visitor id; None when it doesn't run or there is no id
    (nothing changes for that visitor)."""
    from app.experiments import in_arm

    p = percent()
    if p <= 0 or not visitor_id:
        return None
    return FIELD if in_arm(EXPERIMENT, visitor_id, p) else CHOICE


def tag(meta: dict, visitor_id: Optional[str], said: Optional[str], field: Optional[str]) -> None:
    """At /chat/init of a chat a card opened: the side the widget was told (`said`), written only when it is this
    visitor id's side now. A widget cached from before says none, and a side the server did not give is not
    believed. On the field side, also the field the yes opened at once."""
    s = side(visitor_id)
    if s is None or said != s:
        return
    meta[SIDE_KEY] = s
    if s == FIELD and field in CHANNELS:
        meta[FIELD_KEY] = field


def field_of(meta: Optional[dict]) -> Optional[str]:
    """The field the chat's yes opened at once ("sms" | "email"), or None: the choice side, no test, or a chat
    whose field was not said."""
    m = meta if isinstance(meta, dict) else {}
    if m.get(SIDE_KEY) != FIELD:
        return None
    f = m.get(FIELD_KEY)
    return f if f in CHANNELS else None


def count(s: Optional[str]) -> None:
    """One more card answer on side `s`."""
    if s in CONTACT_FIELD_HEALTH:
        CONTACT_FIELD_HEALTH[s] += 1


def health() -> dict:
    return {"percent": percent(), **CONTACT_FIELD_HEALTH}
