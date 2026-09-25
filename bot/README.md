# Vera Bot — magicpin AI Challenge Submission

## Approach

**Architecture**: 4-context LLM composer with rule-based intent detection and category-voice-matched prompts.

**Model**: Llama 3.3 70B via Groq (fast inference, deterministic with temperature=0.3).

### How it works

1. **Context Store** — Thread-safe in-memory store with versioned idempotent upserts. All 4 context types (category, merchant, customer, trigger) are stored and cross-referenced.

2. **Composer** — Single LLM-prompted module that takes the 4 contexts as structured input. Dispatches by trigger kind and scope. Enforces category voice rules, anti-patterns, and factual grounding in the system prompt.

3. **Intent Detection** — Rule-based pre-LLM layer (~1ms) that detects:
   - **Auto-replies**: Pattern-matched against 10+ common WhatsApp Business canned responses. Tracks count — 1st: acknowledge, 2nd: wait 24h, 3rd+: end gracefully.
   - **Hostile/opt-out**: Pattern-matched. Immediate graceful exit with merchant suppression.
   - **Explicit commitment**: "Let's do it", "go ahead", etc. Switches prompt from qualifying to action mode.
   - **Off-topic**: GST, passport, etc. Politely declines, redirects.

4. **Conversation Tracker** — Tracks per-conversation state including turn history, auto-reply counts, and suppression. Prevents duplicate sends via suppression keys.

### Key design decisions

- **Rule-based intent before LLM**: Auto-reply detection and hostile handling are too important to leave to LLM latency. Rule-based detection is <1ms and deterministic. The LLM still gets the signals in its prompt for nuanced handling.

- **No fabrication guardrail**: The system prompt explicitly instructs "ONLY use facts from the context provided" and lists this as a hard anti-pattern. The LLM sees all 4 contexts serialized with real data.

- **Hindi-English code-mix**: Enabled by default when merchant's `identity.languages` includes "hi". The LLM is instructed to mix naturally, not forced.

- **Service+price over discount**: The prompt explicitly prefers "Dental Cleaning @ ₹299" over "30% off" — matching the category offer catalog format.

### What would have helped

- Real conversation logs for fine-tuning voice per category
- A/B test data on which compulsion levers work best per category
- Actual merchant response rates by trigger kind
- More customer visit history for richer personalization

## Running locally

```bash
cd bot
pip install -r requirements.txt
export GROQ_API_KEY=your_key_here
python bot.py
```

Bot runs on `http://localhost:8080`.

## Endpoints

| Endpoint | Method | Purpose |
|---|---|---|
| `/v1/healthz` | GET | Liveness probe |
| `/v1/metadata` | GET | Bot identity |
| `/v1/context` | POST | Receive context pushes |
| `/v1/tick` | POST | Periodic wake-up, bot initiates |
| `/v1/reply` | POST | Receive merchant/customer replies |

## Testing

```bash
export BOT_URL=http://localhost:8080
python judge_simulator.py
```
