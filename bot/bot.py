"""
Vera Bot — magicpin AI Challenge Entry
=======================================
FastAPI server exposing 5 endpoints for the judge harness:
  POST /v1/context   — receive context pushes
  POST /v1/tick      — periodic wake-up; bot initiates conversations
  POST /v1/reply     — receive merchant/customer replies
  GET  /v1/healthz   — liveness probe
  GET  /v1/metadata  — bot identity

Run:  uvicorn bot:app --host 0.0.0.0 --port 8080
"""

import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from pydantic import BaseModel

from context_store import ContextStore
from conversation_tracker import ConversationTracker
from composer import compose_message, compose_reply, detect_intent_signals

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("vera-bot")

START_TIME = time.time()

app = FastAPI(
    title="Vera Bot — magicpin AI Challenge",
    description="Merchant AI assistant for WhatsApp engagement",
    version="1.0.0",
)

store = ContextStore()
tracker = ConversationTracker()

# Track which (merchant, suppression_key) combos we've already sent this session
_sent_suppression_keys: set[str] = set()


@app.get("/")
def read_root():
    return {
        "status": "online",
        "bot": "Vera AI Bot for magicpin Challenge",
        "docs": "Endpoints available: /v1/healthz, /v1/metadata, /v1/context, /v1/tick, /v1/reply"
    }

@app.get("/v1/healthz")
async def healthz():
    return {
        "status": "ok",
        "uptime_seconds": int(time.time() - START_TIME),
        "contexts_loaded": store.counts(),
    }


@app.get("/v1/metadata")
async def metadata():
    return {
        "team_name": "Nahraf",
        "team_members": ["Farhan"],
        "model": "llama-3.3-70b-versatile (via Groq)",
        "approach": "4-context LLM composer with trigger-kind dispatch, "
                    "rule-based intent detection (auto-reply/hostile/commitment), "
                    "and category-voice-matched prompts",
        "contact_email": "farhan.ug23@nsut.ac.in",
        "version": "1.0.0",
        "submitted_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


class ContextPushRequest(BaseModel):
    scope: str
    context_id: str
    version: int
    payload: dict[str, Any]
    delivered_at: str


@app.post("/v1/context")
async def push_context(body: ContextPushRequest):
    accepted, ack_id, reason, current_version = store.upsert(
        scope=body.scope,
        context_id=body.context_id,
        version=body.version,
        payload=body.payload,
    )

    if accepted:
        logger.info(
            f"Context accepted: {body.scope}/{body.context_id} v{body.version}"
        )
        return {
            "accepted": True,
            "ack_id": ack_id,
            "stored_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }

    if reason == "stale_version":
        logger.info(
            f"Context rejected (stale): {body.scope}/{body.context_id} "
            f"v{body.version} < v{current_version}"
        )
        return {
            "accepted": False,
            "reason": "stale_version",
            "current_version": current_version,
        }

    logger.warning(f"Context rejected: {reason}")
    return {
        "accepted": False,
        "reason": reason,
        "details": f"Invalid scope: {body.scope}",
    }


class TickRequest(BaseModel):
    now: str
    available_triggers: list[str] = []


@app.post("/v1/tick")
async def tick(body: TickRequest):
    actions = []

    for trigger_id in body.available_triggers:
        trigger = store.get_trigger(trigger_id)
        if not trigger:
            logger.debug(f"Trigger {trigger_id} not in store, skipping")
            continue

        merchant_id = trigger.get("merchant_id")
        if not merchant_id:
            logger.debug(f"Trigger {trigger_id} has no merchant_id, skipping")
            continue

        if tracker.is_merchant_suppressed(merchant_id):
            logger.info(f"Merchant {merchant_id} suppressed, skipping {trigger_id}")
            continue

        sup_key = trigger.get("suppression_key", "")
        dedup_key = f"{merchant_id}:{sup_key}"
        if dedup_key in _sent_suppression_keys:
            logger.info(f"Already sent {sup_key} to {merchant_id}, skipping")
            continue

        merchant = store.get_merchant(merchant_id)
        if not merchant:
            logger.debug(f"Merchant {merchant_id} not in store, skipping")
            continue

        category_slug = merchant.get("category_slug", "")
        category = store.get_category(category_slug)
        if not category:
            logger.debug(f"Category {category_slug} not in store, skipping")
            continue

        # Resolve customer if customer-scoped trigger
        customer_id = trigger.get("customer_id")
        customer = store.get_customer(customer_id) if customer_id else None

        # Compose the message
        try:
            result = compose_message(category, merchant, trigger, customer)
        except Exception as e:
            logger.error(f"Compose error for {trigger_id}: {e}")
            continue

        if not result.get("body"):
            logger.warning(f"Empty body for {trigger_id}, skipping")
            continue

        # Build conversation ID
        conv_id = f"conv_{merchant_id}_{trigger_id}"

        # Record in tracker
        tracker.create(
            conversation_id=conv_id,
            merchant_id=merchant_id,
            customer_id=customer_id,
            trigger_id=trigger_id,
            send_as=result.get("send_as", "vera"),
            initial_body=result.get("body", ""),
            timestamp=body.now,
        )

        # Mark suppression key as sent
        _sent_suppression_keys.add(dedup_key)

        action = {
            "conversation_id": conv_id,
            "merchant_id": merchant_id,
            "customer_id": customer_id,
            "send_as": result.get("send_as", "vera"),
            "trigger_id": trigger_id,
            "template_name": result.get("template_name", f"vera_{trigger.get('kind', 'generic')}_v1"),
            "template_params": result.get("template_params", []),
            "body": result.get("body", ""),
            "cta": result.get("cta", "open_ended"),
            "suppression_key": result.get("suppression_key", sup_key),
            "rationale": result.get("rationale", ""),
        }
        actions.append(action)
        logger.info(
            f"Composed message for {merchant_id} via {trigger_id} "
            f"({len(result.get('body', ''))} chars)"
        )

    logger.info(f"Tick at {body.now}: {len(actions)} action(s)")
    return {"actions": actions}


class ReplyRequest(BaseModel):
    conversation_id: str
    merchant_id: Optional[str] = None
    customer_id: Optional[str] = None
    from_role: str
    message: str
    received_at: str
    turn_number: int


@app.post("/v1/reply")
async def reply(body: ReplyRequest):
    conv_state = tracker.record_merchant_reply(
        conversation_id=body.conversation_id,
        body=body.message,
        timestamp=body.received_at,
        turn_number=body.turn_number,
    )

    merchant_id = body.merchant_id or (conv_state.merchant_id if conv_state else "")

    auto_count = conv_state.auto_reply_count if conv_state else 0
    signals = detect_intent_signals(body.message, auto_count)

    # Update auto-reply count in tracker
    if signals["is_auto_reply"] and conv_state:
        new_count = tracker.increment_auto_reply(body.conversation_id)
        signals["auto_reply_count"] = new_count

    #  - 3+ auto-replies: end immediately
    if signals["auto_reply_count"] >= 3:
        result = {
            "action": "end",
            "rationale": f"Auto-reply detected {signals['auto_reply_count']}x in a row. "
                         "No real engagement signal; closing conversation.",
        }
        tracker.record_bot_reply(body.conversation_id, "", "end", body.received_at)
        tracker.suppress_conversation(body.conversation_id)
        logger.info(f"Auto-reply exit: {body.conversation_id}")
        return result

    #  - 2 auto-replies: wait 5m (300s)
    if signals["auto_reply_count"] == 2:
        result = {
            "action": "wait",
            "wait_seconds": 300,
            "rationale": "Same auto-reply twice in a row — owner not at phone. "
                         "Wait before retry.",
        }
        tracker.record_bot_reply(body.conversation_id, "", "wait", body.received_at)
        logger.info(f"Auto-reply wait: {body.conversation_id}")
        return result

    #  - Hostile: end with short acknowledgment
    if signals["is_hostile"]:
        result = {
            "action": "send",
            "body": "Apologies — I won't message again. If anything changes, "
                    "you can always restart with 'Hi Vera'. 🙏",
            "cta": "none",
            "rationale": "Merchant frustration explicit; one-line acknowledgment + "
                         "opt-out path. Suppressing future messages.",
        }
        tracker.record_bot_reply(body.conversation_id, result["body"], "send", body.received_at)
        tracker.suppress_conversation(body.conversation_id)
        if merchant_id:
            tracker.suppress_merchant(merchant_id)
        logger.info(f"Hostile exit: {body.conversation_id}")
        return result

    # Full LLM reply for engaged/nuanced conversations
    # Resolve contexts
    merchant = store.get_merchant(merchant_id) or {}
    category_slug = merchant.get("category_slug", "")
    category = store.get_category(category_slug) or {}
    trigger_id = conv_state.trigger_id if conv_state else ""
    trigger = store.get_trigger(trigger_id) or {}
    customer_id = body.customer_id or (conv_state.customer_id if conv_state else None)
    customer = store.get_customer(customer_id) if customer_id else None

    # Build conversation turns for the LLM
    conv_turns = []
    if conv_state:
        for t in conv_state.turns:
            conv_turns.append({
                "turn_number": t.turn_number,
                "from_role": t.from_role,
                "body": t.body,
            })

    try:
        result = compose_reply(
            category=category,
            merchant=merchant,
            trigger=trigger,
            customer=customer,
            conversation_turns=conv_turns,
            merchant_message=body.message,
            intent_signals=signals,
        )
    except Exception as e:
        logger.error(f"Reply compose failed: {e}")
        result = {
            "action": "send",
            "body": "Got it, let me look into that for you and get back shortly.",
            "cta": "open_ended",
            "rationale": f"Fallback reply due to error: {e}",
        }

    # Record the reply
    action = result.get("action", "send")
    bot_body = result.get("body", "")
    tracker.record_bot_reply(body.conversation_id, bot_body, action, body.received_at)

    if action == "end":
        tracker.suppress_conversation(body.conversation_id)

    logger.info(
        f"Reply to {body.conversation_id}: action={action}, "
        f"body_len={len(bot_body)}"
    )

    return result


@app.on_event("startup")
async def on_startup():
    logger.info("Vera bot starting up...")
    logger.info(f"GROQ_API_KEY configured: {'yes' if os.getenv('GROQ_API_KEY') else 'NO'}")
    logger.info(f"Model: {os.getenv('GROQ_MODEL', 'llama-3.3-70b-versatile')}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("bot:app", host="0.0.0.0", port=8080, reload=True)
