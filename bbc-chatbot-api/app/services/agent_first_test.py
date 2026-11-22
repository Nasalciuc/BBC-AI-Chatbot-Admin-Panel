"""The owner's test of 22 Nov 2026: does a person who writes first get more answers than Alex's card?

On 15-22 Nov 4,122 visitors saw Alex's card and 235 answered it (5.7 %, PostHog). Half the visitors
(AGENT_FIRST_TEST_PERCENT, the owner's 50) are on the agent's side, bucketed by visitor id like every
experiment (app.experiments), so a visitor keeps their side across visits:

- the agent's side is listed in "On the site now". While an agent who may say hello has that page open (bbc-live
  notes them at each read of the list), Alex's card waits AGENT_FIRST_HOLD_S for them (the owner's 2 minutes,
  counted from the visitor's first moment, live:hold:<vid>). Nobody looking, nobody to wait for: an agent on
  another page of the panel would never see the visitor. A hello said meanwhile keeps Alex quiet for the visit (live:said, the hello's 15 minutes),
  and so does the visitor's "Not now" (live:dismissed, an hour, for everyone). Nobody said hello: the card
  comes as it would for anyone, marked after_hold;
- Alex's side is as before, and never listed: no agent greets them, so it stays the comparison.

PROACTIVE_PERCENT stays 100; the tapped note (the visitor asked) is never held. The test runs while the hello
runs (LIVE_VISITORS and AGENT_FIRST_MESSAGE on); 0 in AGENT_FIRST_TEST_PERCENT ends it without a deploy, and
every visitor is listed again. Each side is named on the widget's answers ("test": "agent" | "alex"), on the
chat's metadata (agent_test) and on the widget's PostHog events, so the result reads per side, the whole side
(leads per 100 visitors), not only the visitors an agent chose to greet.

Redis (contracts/live.md): live:hold:<vid> (when Alex's wait began, 15 min, written here) and live:greeters (the
agents who may hold a visitor, by their last read of the list, written by bbc-live). Redis failing: nobody is held
(the card comes as for everyone), and the miss is counted, never silent (rules 2 and 7).
"""

from __future__ import annotations

import logging
import math
import time
from typing import NamedTuple, Optional

from app.flags import flag
from app.infra.redis_client import get_redis, redis_failed

logger = logging.getLogger(__name__)

EXPERIMENT = "agent_first"
AGENT, ALEX = "agent", "alex"
HELD_ARM = "agent_first"          # the arm an offer answer names while it keeps Alex quiet
NOTE = "note"                     # proactive.NOTE: the visitor tapped the note, never held
HOLD_KEY = "live:hold:"           # + visitor id: unix seconds when Alex's wait began
HOLD_KEY_TTL = 900                # a visit; past it the next visit waits again
GREETERS_KEY = "live:greeters"    # bbc-live's sorted set: user id -> unix seconds of their last read of the list
# Looking at the list: a read in the last 30 s. The panel reads it every 5 s while the page is open and visible.
GREETER_WINDOW_S = 30
SAID_KEY = "live:said:"           # written by send.lua with the hello (live_visitors.HELLO_TTL)
DISMISSED_KEY = "live:dismissed:"  # written by bbc-live on "Not now"
MAX_HOLD_S = 600

AGENT_FIRST_HEALTH: dict[str, int] = {
    "held": 0,                 # an offer answered "wait": Alex holds back for an agent
    "quiet_greeted": 0,        # an offer answered "no": an agent said hello first
    "quiet_declined": 0,       # an offer answered "no": the visitor said not now to a hello
    "no_agent": 0,             # the agent's side, nobody who may say hello looking at the list: the card as usual
    "after_hold": 0,           # the wait ran out without a hello: the card as usual
    "hellos_in_hold": 0,       # hellos sent while Alex waited
    "hellos_after_hold": 0,    # hellos sent after the wait ran out (Alex's card may have shown)
    "hellos_no_hold": 0,       # hellos to a visitor Alex never waited for
    "redis_failed": 0,         # the wait, a hello or who is looking not read: nobody held, counted here
}


class Hold(NamedTuple):
    """What the agent's side changes in an offer: the answer to give instead (None: the usual one), and
    whether the usual one comes because the wait ran out."""

    answer: Optional[dict]
    after_hold: bool = False


def percent() -> int:
    try:
        return max(0, min(100, int(flag("AGENT_FIRST_TEST_PERCENT", 50) or 0)))
    except (TypeError, ValueError) as e:
        logger.warning(f"[agent-first] AGENT_FIRST_TEST_PERCENT unreadable ({e}): the test is off")
        return 0


def hold_seconds() -> int:
    try:
        return max(0, min(MAX_HOLD_S, int(flag("AGENT_FIRST_HOLD_S", 120) or 0)))
    except (TypeError, ValueError) as e:
        logger.warning(f"[agent-first] AGENT_FIRST_HOLD_S unreadable ({e}): Alex does not wait")
        return 0


def running() -> bool:
    from app.services import live_visitors

    return percent() > 0 and live_visitors.hello_enabled()


def side(visitor_id: Optional[str]) -> Optional[str]:
    """"agent" or "alex" while the test runs, stable per visitor id; None when it doesn't run or the id is not
    one the live list takes (no side: nothing changes for that visitor)."""
    from app.experiments import in_arm
    from app.services.live_visitors import VISITOR_ID

    if not running() or not VISITOR_ID.fullmatch(visitor_id or ""):
        return None
    return AGENT if in_arm(EXPERIMENT, visitor_id, percent()) else ALEX


def panel_info() -> Optional[dict]:
    """For the panel's live pass (the heartbeat): the test, while it runs."""
    if not running():
        return None
    return {"percent": percent(), "hold_s": hold_seconds()}


def tag(meta: dict, visitor_id: Optional[str]) -> None:
    """The chat's side, on its metadata (agent_test), so leads read per side. Server-made: the widget's own
    metadata never carries it (allowed_widget_metadata)."""
    s = side(visitor_id)
    if s:
        meta["agent_test"] = s


def _quiet(trigger: str, reason: str) -> dict:
    return {"show": False, "arm": HELD_ARM, "trigger": trigger, "message": None, "quick_replies": [],
            "reason": reason, "test": AGENT}


async def greeter_online(r) -> Optional[bool]:
    """Is anyone who may say hello looking at "On the site now"? None when Redis can't say."""
    try:
        return int(await r.zcount(GREETERS_KEY, int(time.time()) - GREETER_WINDOW_S, "+inf")) > 0
    except Exception as e:
        redis_failed("agent-first greeters", e)
        AGENT_FIRST_HEALTH["redis_failed"] += 1
        logger.warning(f"[agent-first] who may say hello not read ({type(e).__name__}: {e}): nobody held")
        return None


async def offer_hold(visitor_id: Optional[str], trigger: str) -> Hold:
    """Before Alex's card: what the agent's side changes for this offer. Hold(None) everywhere else, and for a
    moment whose card would not come anyway (the feature or the moment off, PROACTIVE_PERCENT's control arm):
    nothing to wait for, no wait started."""
    from app.services import proactive

    if trigger == NOTE or side(visitor_id) != AGENT:
        return Hold(None)
    if not (proactive.enabled() and trigger in proactive.active_triggers()
            and proactive.arm_for(visitor_id) == "proactive"):
        return Hold(None)
    hold_s = hold_seconds()
    r = get_redis()
    if r is None:
        AGENT_FIRST_HEALTH["redis_failed"] += 1
        logger.warning("[agent-first] Redis not reachable: nobody held, the card as usual")
        return Hold(None)
    try:
        pipe = r.pipeline(transaction=False)
        pipe.exists(SAID_KEY + visitor_id)
        pipe.exists(DISMISSED_KEY + visitor_id)
        pipe.get(HOLD_KEY + visitor_id)
        said, dismissed, began = await pipe.execute()
    except Exception as e:
        redis_failed("agent-first hold", e)
        AGENT_FIRST_HEALTH["redis_failed"] += 1
        logger.warning(f"[agent-first] the wait not read ({type(e).__name__}: {e}): nobody held")
        return Hold(None)
    if said:
        AGENT_FIRST_HEALTH["quiet_greeted"] += 1
        return Hold(_quiet(trigger, "greeted"))
    if dismissed:
        AGENT_FIRST_HEALTH["quiet_declined"] += 1
        return Hold(_quiet(trigger, "declined"))
    now = time.time()
    started: Optional[float] = None
    try:
        started = float(began) if began is not None else None
    except (TypeError, ValueError) as e:
        logger.warning(f"[agent-first] a wait's start unreadable ({e}): it starts again")
    if started is None:
        if hold_s <= 0:
            return Hold(None)  # AGENT_FIRST_HOLD_S=0: the agent's side is listed, and Alex doesn't wait
        online = await greeter_online(r)
        if not online:
            if online is False:
                AGENT_FIRST_HEALTH["no_agent"] += 1
            return Hold(None)
        try:
            # NX: two tabs asking at once share one wait, the first one's.
            await r.set(HOLD_KEY + visitor_id, str(int(now)), nx=True, ex=HOLD_KEY_TTL)
            began = await r.get(HOLD_KEY + visitor_id)
            started = float(began) if began is not None else now
        except Exception as e:
            redis_failed("agent-first hold start", e)
            AGENT_FIRST_HEALTH["redis_failed"] += 1
            logger.warning(f"[agent-first] the wait not started ({type(e).__name__}: {e}): nobody held")
            return Hold(None)
    left = started + hold_s - now
    if left <= 0:
        AGENT_FIRST_HEALTH["after_hold"] += 1
        return Hold(None, after_hold=True)
    AGENT_FIRST_HEALTH["held"] += 1
    answer = _quiet(trigger, "waiting")
    answer["retry_after_s"] = max(1, math.ceil(left))
    # When Alex comes by this clock: the widget beats it to bbc-live (checked against its own), so the agent's
    # countdown doesn't depend on the visitor's clock.
    answer["alex_at"] = int(math.ceil(started + hold_s))
    return Hold(answer)


async def note_hello(visitor_id: str) -> None:
    """A hello went: was Alex still waiting for it? Counted for the owner's reading of the test."""
    if not running():
        return
    r = get_redis()
    if r is None:
        AGENT_FIRST_HEALTH["redis_failed"] += 1
        return
    try:
        began = await r.get(HOLD_KEY + visitor_id)
    except Exception as e:
        redis_failed("agent-first hello", e)
        AGENT_FIRST_HEALTH["redis_failed"] += 1
        logger.warning(f"[agent-first] a hello's wait not read ({type(e).__name__}: {e})")
        return
    if began is None:
        AGENT_FIRST_HEALTH["hellos_no_hold"] += 1
        return
    try:
        in_hold = time.time() < float(began) + hold_seconds()
    except (TypeError, ValueError) as e:
        logger.warning(f"[agent-first] a hello's wait unreadable ({e})")
        AGENT_FIRST_HEALTH["hellos_no_hold"] += 1
        return
    AGENT_FIRST_HEALTH["hellos_in_hold" if in_hold else "hellos_after_hold"] += 1


def health() -> dict:
    return {"running": running(), "percent": percent(), "hold_s": hold_seconds(), **AGENT_FIRST_HEALTH}
