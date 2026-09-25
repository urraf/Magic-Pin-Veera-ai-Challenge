"""
Composer — LLM-powered message composition engine.

Dispatches by trigger.kind to produce category-fit, merchant-personalized,
trigger-relevant messages using the 4-context framework.
"""

import json
import logging
import os
import re
from typing import Optional

from groq import Groq

logger = logging.getLogger(__name__)

_client: Optional[Groq] = None

MODEL = os.getenv("GROQ_MODEL", "qwen/qwen3.8-27b")
FALLBACK_MODEL = os.getenv("GROQ_FALLBACK_MODEL", "openai/gpt-oss-120b")


def _get_client() -> Groq:
    global _client
    if _client is None:
        api_key = os.getenv("GROQ_API_KEY", "")
        if not api_key:
            raise RuntimeError("GROQ_API_KEY not set")
        _client = Groq(api_key=api_key, max_retries=1)
    return _client


VOICE_RULES = """
VOICE RULES (critical — follow exactly):
- Match the category voice: {tone}
- Taboo words NEVER use: {taboos}
- Use Hindi-English code-mix naturally when merchant languages include "hi"
- Use owner first name when available, not generic "Hi"
- Peer/colleague tone, NOT promotional. NO "AMAZING DEAL!" or hype
- NO long preambles ("I hope you're doing well...")
- NO re-introductions after first message
- Anchor every message on verifiable facts from the data (numbers, dates, sources)
- Service+price beats discount (say "Dental Cleaning @ ₹299" not "30% off")
- Single primary CTA at the END of the message
- Keep it concise — WhatsApp messages, not emails
"""

ANTI_PATTERNS = """
ANTI-PATTERNS (will be penalized):
- Generic offers ("Flat 30% off") when service+price is available
- Multiple CTAs in one message
- Buried call-to-action
- Hallucinated data (citing research/stats NOT in the context)
- Promotional tone for clinical categories
- Re-introducing yourself
- Ignoring language preference
"""


def _build_context_block(
    category: dict,
    merchant: dict,
    trigger: dict,
    customer: Optional[dict] = None,
) -> str:
    """Serialize the 4 contexts into a structured text block for the LLM."""

    # Category
    voice = category.get("voice", {})
    digest_items = category.get("digest", [])
    digest_text = "\n".join(
        f"  - [{d.get('id', '')}] {d.get('title', '')} — source: {d.get('source', 'unknown')}"
        + (f", trial N={d.get('trial_n', '')}" if d.get("trial_n") else "")
        + (f"\n    Summary: {d.get('summary', '')}" if d.get("summary") else "")
        for d in digest_items
    )
    offers_cat = category.get("offer_catalog", [])
    offers_cat_text = ", ".join(o.get("title", "") for o in offers_cat[:5])

    peer = category.get("peer_stats", {})
    seasonal = category.get("seasonal_beats", [])
    seasonal_text = "; ".join(
        f"{s.get('month_range', '')}: {s.get('note', '')}" for s in seasonal
    )
    trend = category.get("trend_signals", [])
    trend_text = "; ".join(
        f"{t.get('query', '')} +{int(t.get('delta_yoy', 0)*100)}% YoY" for t in trend
    )
    patient_content = category.get("patient_content_library", [])
    patient_content_text = "; ".join(
        f"[{pc.get('id', '')}] {pc.get('title', '')}" for pc in patient_content[:3]
    )

    # Merchant
    identity = merchant.get("identity", {})
    perf = merchant.get("performance", {})
    delta_7d = perf.get("delta_7d", {})
    m_offers = merchant.get("offers", [])
    active_offers = [o for o in m_offers if o.get("status") == "active"]
    active_offers_text = ", ".join(o.get("title", "") for o in active_offers) or "none"
    signals = merchant.get("signals", [])
    conv_hist = merchant.get("conversation_history", [])
    conv_hist_text = "\n".join(
        f"  [{c.get('ts', '')}] {c.get('from', '')}: {c.get('body', '')[:100]}"
        for c in conv_hist[-3:]  # last 3 turns
    )
    cust_agg = merchant.get("customer_aggregate", {})
    review_themes = merchant.get("review_themes", [])
    review_text = "; ".join(
        f"{r.get('theme', '')}({r.get('sentiment', '')}): {r.get('occurrences_30d', 0)} mentions"
        for r in review_themes
    )

    # Trigger
    trigger_payload = trigger.get("payload", {})

    # Customer (optional)
    customer_block = ""
    if customer:
        c_identity = customer.get("identity", {})
        c_rel = customer.get("relationship", {})
        c_prefs = customer.get("preferences", {})
        c_consent = customer.get("consent", {})
        customer_block = f"""
CUSTOMER:
  Name: {c_identity.get('name', 'unknown')}
  Language pref: {c_identity.get('language_pref', 'english')}
  Age band: {c_identity.get('age_band', 'unknown')}
  State: {customer.get('state', 'unknown')}
  Last visit: {c_rel.get('last_visit', 'unknown')}
  Total visits: {c_rel.get('visits_total', 0)}
  Services: {c_rel.get('services_received', [])}
  Preferred slots: {c_prefs.get('preferred_slots', 'any')}
  Channel: {c_prefs.get('channel', 'whatsapp')}
  Consent scope: {c_consent.get('scope', [])}
  Wedding date: {c_prefs.get('wedding_date', 'N/A')}
  Training focus: {c_prefs.get('training_focus', 'N/A')}
  Health focus: {c_prefs.get('health_focus', 'N/A')}
  Chronic conditions: {c_rel.get('chronic_conditions', 'N/A')}
  Senior citizen: {c_identity.get('senior_citizen', False)}
  Delivery address saved: {c_prefs.get('delivery_address', 'N/A')}
  Favourite dish: {c_rel.get('favourite_dish', 'N/A')}
  Family size: {c_prefs.get('family_size', 'N/A')}
"""

    return f"""
CATEGORY: {category.get('slug', 'unknown')}
  Voice tone: {voice.get('tone', 'professional')}
  Allowed vocab: {voice.get('vocab_allowed', [])[:8]}
  Taboo words: {voice.get('vocab_taboo', [])}
  Peer stats: avg_rating={peer.get('avg_rating', '?')}, avg_ctr={peer.get('avg_ctr', '?')}, avg_reviews={peer.get('avg_review_count', '?')}
  Category offer catalog: {offers_cat_text}
  Weekly digest:
{digest_text}
  Seasonal beats: {seasonal_text}
  Trend signals: {trend_text}
  Patient content library: {patient_content_text}

MERCHANT:
  Name: {identity.get('name', 'unknown')}
  Owner first name: {identity.get('owner_first_name', 'unknown')}
  City: {identity.get('city', 'unknown')}, Locality: {identity.get('locality', 'unknown')}
  Verified: {identity.get('verified', False)}
  Languages: {identity.get('languages', ['en'])}
  Subscription: {merchant.get('subscription', {}).get('status', 'unknown')} ({merchant.get('subscription', {}).get('plan', '')}, {merchant.get('subscription', {}).get('days_remaining', '?')}d left)
  Performance (30d): views={perf.get('views', '?')}, calls={perf.get('calls', '?')}, directions={perf.get('directions', '?')}, CTR={perf.get('ctr', '?')}
  7d delta: views {delta_7d.get('views_pct', '?')}, calls {delta_7d.get('calls_pct', '?')}
  Active offers: {active_offers_text}
  Signals: {signals}
  Customer aggregate: {json.dumps(cust_agg)}
  Review themes: {review_text}
  Recent conversation:
{conv_hist_text}

TRIGGER:
  ID: {trigger.get('id', 'unknown')}
  Scope: {trigger.get('scope', 'merchant')}
  Kind: {trigger.get('kind', 'unknown')}
  Source: {trigger.get('source', 'unknown')}
  Urgency: {trigger.get('urgency', 1)}/5
  Suppression key: {trigger.get('suppression_key', '')}
  Payload: {json.dumps(trigger_payload, indent=2)}
{customer_block}
"""


def compose_message(
    category: dict,
    merchant: dict,
    trigger: dict,
    customer: Optional[dict] = None,
) -> dict:
    """
    Compose a proactive message using the 4-context framework.
    Returns dict with: body, cta, send_as, template_name, template_params,
                       suppression_key, rationale
    """

    trigger_kind = trigger.get("kind", "generic")
    trigger_scope = trigger.get("scope", "merchant")
    is_customer_facing = trigger_scope == "customer" and customer is not None

    identity = merchant.get("identity", {})
    voice = category.get("voice", {})
    tone = voice.get("tone", "professional")
    taboos = voice.get("vocab_taboo", [])

    context_block = _build_context_block(category, merchant, trigger, customer)

    # Determine send_as
    send_as = "merchant_on_behalf" if is_customer_facing else "vera"

    system_prompt = f"""You are Vera, magicpin's merchant AI assistant. You compose WhatsApp messages
for merchants and their customers. You must produce ONE message that scores
high on: specificity, category fit, merchant fit, trigger relevance,
and engagement compulsion.

{VOICE_RULES.format(tone=tone, taboos=taboos)}

{ANTI_PATTERNS}

CRITICAL RULES:
- ONLY use facts from the context provided. DO NOT fabricate numbers, sources, or competitor names.
- Reference the merchant by owner first name (e.g., "Dr. Meera", "Suresh", "Lakshmi").
- For customer-facing messages: send_as = "merchant_on_behalf". Use the merchant's name as sender attribution.
- For merchant-facing: send_as = "vera".
- Output ONLY valid JSON. No markdown, no explanation, no code blocks.
"""

    user_prompt = f"""Compose a WhatsApp message for this scenario:

{context_block}

OUTPUT REQUIREMENTS:
Return a single JSON object with these exact keys:
{{
  "body": "<the WhatsApp message body — concise, specific, compelling>",
  "cta": "<one of: binary_yes_no | binary_confirm_cancel | open_ended | multi_choice_slot | none>",
  "send_as": "{send_as}",
  "template_name": "<a descriptive template name like vera_research_digest_v1>",
  "template_params": ["<param1>", "<param2>", "<param3>"],
  "suppression_key": "{trigger.get('suppression_key', '')}",
  "rationale": "<2-3 sentences explaining WHY this message, what compulsion levers used, what it should achieve>"
}}

RESPOND WITH ONLY THE JSON OBJECT. No markdown formatting, no code blocks, no explanation.
"""

    try:
        client = _get_client()
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.3,
            max_tokens=350,
            top_p=0.9,
        )

        raw = response.choices[0].message.content.strip()
        result = _parse_json_response(raw)

        # Enforce required fields
        result.setdefault("body", "")
        result.setdefault("cta", "open_ended")
        result.setdefault("send_as", send_as)
        result.setdefault("template_name", f"vera_{trigger_kind}_v1")
        result.setdefault("template_params", [identity.get("owner_first_name", ""), "...", "..."])
        result.setdefault("suppression_key", trigger.get("suppression_key", ""))
        result.setdefault("rationale", "Composed using 4-context framework")

        return result

    except Exception as e:
        logger.error(f"Compose failed for trigger {trigger.get('id', '?')}: {e}")
        return _fallback_compose(category, merchant, trigger, customer)


def compose_reply(
    category: dict,
    merchant: dict,
    trigger: dict,
    customer: Optional[dict],
    conversation_turns: list[dict],
    merchant_message: str,
    intent_signals: dict,
) -> dict:
    """
    Compose a reply to a merchant/customer message.
    Returns dict with: action, body, cta, rationale
    """

    voice = category.get("voice", {})
    tone = voice.get("tone", "professional")
    taboos = voice.get("vocab_taboo", [])

    context_block = _build_context_block(category, merchant, trigger, customer)

    # Build conversation history text
    conv_text = "\n".join(
        f"  Turn {t.get('turn_number', '?')} [{t.get('from_role', '?')}]: {t.get('body', '')[:200]}"
        for t in conversation_turns[-6:]
    )

    system_prompt = f"""You are Vera, magicpin's merchant AI assistant. You are replying to a merchant's
message in an ongoing WhatsApp conversation.

{VOICE_RULES.format(tone=tone, taboos=taboos)}

INTENT SIGNALS DETECTED:
- Auto-reply detected: {intent_signals.get('is_auto_reply', False)}
- Auto-reply count: {intent_signals.get('auto_reply_count', 0)}
- Hostile/opt-out: {intent_signals.get('is_hostile', False)}
- Explicit commitment: {intent_signals.get('is_commitment', False)}
- Off-topic question: {intent_signals.get('is_off_topic', False)}

CONVERSATION RULES:
1. If auto-reply detected (count >= 2): action=wait or action=end. Don't keep messaging an auto-responder.
2. If auto-reply count >= 3: action=end. Give up gracefully.
3. If hostile/opt-out: action=end OR short apology + end. DO NOT continue pitching.
4. If explicit commitment ("let's do it", "ok go ahead", "yes"): SWITCH TO ACTION MODE immediately.
   DO NOT ask more qualifying questions. Describe what you're doing next.
5. If off-topic: Politely decline, redirect to original topic.
6. Otherwise: advance the conversation naturally. Add value. Don't repeat what you said before.

OUTPUT: Return a single JSON object:
{{
  "action": "<send | wait | end>",
  "body": "<your reply — only if action=send>",
  "cta": "<binary_yes_no | open_ended | none — only if action=send>",
  "wait_seconds": <number — only if action=wait>,
  "rationale": "<2-3 sentences explaining your decision>"
}}

RESPOND WITH ONLY THE JSON OBJECT. No markdown, no code blocks.
"""

    user_prompt = f"""CONTEXT:
{context_block}

CONVERSATION SO FAR:
{conv_text}

MERCHANT'S LATEST MESSAGE (Turn {len(conversation_turns)}):
"{merchant_message}"

Compose your reply. Remember: if the merchant committed, DO ACTION. If auto-reply or hostile, exit gracefully.
"""

    try:
        client = _get_client()
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
            max_tokens=350,
            top_p=0.9,
        )

        raw = response.choices[0].message.content.strip()
        result = _parse_json_response(raw)

        # Enforce required fields
        result.setdefault("action", "send")
        result.setdefault("rationale", "Reply composed")

        if result["action"] == "send":
            result.setdefault("body", "")
            result.setdefault("cta", "open_ended")
        elif result["action"] == "wait":
            result.setdefault("wait_seconds", 3600)
        # "end" needs no extra fields

        return result

    except Exception as e:
        logger.error(f"Reply compose failed: {e}")
        return {
            "action": "send",
            "body": "Got it, let me look into that for you.",
            "cta": "open_ended",
            "rationale": f"Fallback reply due to error: {e}",
        }


AUTO_REPLY_PATTERNS = [
    r"thank\s*you\s*for\s*contacting",
    r"our\s*team\s*will\s*respond",
    r"we\s*will\s*get\s*back",
    r"automated\s*(response|reply|message|assistant)",
    r"please\s*leave\s*(your|a)\s*message",
    r"currently\s*(unavailable|busy|out)",
    r"(aapki|apki)\s*jaankari",
    r"business\s*hours",
    r"we\s*are\s*not\s*available",
    r"shukri(ya|yaa)",
]

HOSTILE_PATTERNS = [
    r"stop\s*messag",
    r"not\s*interested",
    r"don.t\s*(contact|message|bother|call)",
    r"unsubscribe",
    r"spam",
    r"useless",
    r"bakwas",
    r"band\s*karo",
    r"mat\s*bhejo",
    r"leave\s*me\s*alone",
]

COMMITMENT_PATTERNS = [
    r"(let.?s|lets)\s*do\s*it",
    r"ok\s*(go|lets|do|proceed|start)",
    r"(yes|ha|haan)\s*(please|go|do|send|proceed|confirm)",
    r"go\s*ahead",
    r"proceed",
    r"confirm",
    r"kar\s*do",
    r"bhej\s*do",
    r"ship\s*it",
    r"what.?s\s*next",
]


def detect_intent_signals(message: str, auto_reply_count: int = 0) -> dict:
    """
    Quick rule-based intent detection. Runs before the LLM to guide prompt.
    """
    msg_lower = message.lower().strip()

    is_auto = any(re.search(p, msg_lower) for p in AUTO_REPLY_PATTERNS)
    is_hostile = any(re.search(p, msg_lower) for p in HOSTILE_PATTERNS)
    is_commit = any(re.search(p, msg_lower) for p in COMMITMENT_PATTERNS)
    is_off_topic = any(
        kw in msg_lower
        for kw in ["gst", "income tax", "passport", "aadhaar", "pan card", "voter"]
    )

    return {
        "is_auto_reply": is_auto,
        "auto_reply_count": auto_reply_count + (1 if is_auto else 0),
        "is_hostile": is_hostile,
        "is_commitment": is_commit,
        "is_off_topic": is_off_topic,
    }


def _parse_json_response(raw: str) -> dict:
    """Extract JSON from LLM output, handling markdown code blocks."""
    # Strip <think> tags from reasoning models
    think_match = re.search(r'<think>.*?</think>', raw, re.DOTALL)
    if think_match:
        raw = raw.replace(think_match.group(0), '')
        
    # Strip markdown code fences
    raw = raw.strip()
    if raw.startswith("```json"):
        raw = raw[7:]
    elif raw.startswith("```"):
        raw = raw[3:]
    if raw.endswith("```"):
        raw = raw[:-3]
    raw = raw.strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Try to find JSON object in the response
        match = re.search(r'\{[\s\S]*\}', raw)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
    return {}


def _fallback_compose(
    category: dict,
    merchant: dict,
    trigger: dict,
    customer: Optional[dict],
) -> dict:
    """Deterministic fallback when LLM fails."""
    identity = merchant.get("identity", {})
    owner = identity.get("owner_first_name", "there")
    name = identity.get("name", "your business")
    trigger_kind = trigger.get("kind", "update")
    is_customer_facing = trigger.get("scope") == "customer" and customer is not None

    if is_customer_facing and customer:
        c_name = customer.get("identity", {}).get("name", "there")
        return {
            "body": f"Hi {c_name}, {name} here. We have an update for you — reply for details.",
            "cta": "open_ended",
            "send_as": "merchant_on_behalf",
            "template_name": f"merchant_{trigger_kind}_fallback_v1",
            "template_params": [c_name, name],
            "suppression_key": trigger.get("suppression_key", ""),
            "rationale": "Fallback message due to LLM error; minimal but safe.",
        }

    return {
        "body": f"Hi {owner}, Vera here with a quick update about {name}. Reply for details.",
        "cta": "open_ended",
        "send_as": "vera",
        "template_name": f"vera_{trigger_kind}_fallback_v1",
        "template_params": [owner, name],
        "suppression_key": trigger.get("suppression_key", ""),
        "rationale": "Fallback message due to LLM error; minimal but safe.",
    }
