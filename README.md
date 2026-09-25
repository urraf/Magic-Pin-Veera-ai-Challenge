# Vera Bot — magicpin AI Challenge

![magicpin](https://magicpin.in/images/logo-1.png)

This repository contains our submission for the **magicpin AI Challenge**. 

**Vera** is an intelligent, context-aware AI assistant designed to engage merchants (and their customers) over WhatsApp. It proactively pushes hyper-personalized insights, deals, and nudges by deeply integrating multi-layered contexts (Category, Merchant, Customer, and Trigger).

## Our Approach: The 4-Context Architecture

To maximize the relevance and "Engagement Compulsion" of every outbound message, Vera relies on a strict **4-Context Architecture**:

1. **Category Context**: Injects industry-specific vocabulary, tone constraints (e.g., "clinical" for dentists, "hype" for gyms), taboo words, peer performance stats, and macro-trends.
2. **Merchant Context**: Anchors the message on the specific business’s identity, past performance (metrics, conversion rates), active campaigns, and language preferences (e.g., Hindi-English code-mixing).
3. **Trigger Context**: Answers the *"why now?"* (e.g., a sudden drop in weekend bookings, a new competitor opening, or a relevant external news event).
4. **Customer Context**: *(Optional)* Used when Vera acts on behalf of the merchant to engage the merchant's end-users, factoring in visit history, churn probability, and explicit consent.

### Fast-Path Intent Detection

Instead of blindly passing every merchant reply to the LLM (which incurs high latency and token costs), our bot leverages a rule-based fast-path intent detector (`detect_intent_signals`). 
- **Hostile Exits**: If a merchant expresses frustration (e.g., "stop messaging me"), the bot instantly intercepts the message, apologizes, suppresses future triggers for that merchant, and returns a `send` action in `< 1ms`.
- **Auto-Replies**: The bot detects automated out-of-office responses and safely goes to sleep (`action: wait`) to avoid endless bot-to-bot loops.

## Trade-offs Made
- **Token Efficiency vs Reasoning**: We relied on a highly prompt-engineered instruction template that strictly mandates JSON formatting from `qwen3.8-27b` (via Groq). We opted out of using `<think>` reasoning paths for every standard reply to maintain the strict <30s latency constraint.
- **In-Memory Store vs Persistent Database**: To keep the submission simple, fast, and easy for the judges to evaluate, the `ContextStore` is completely in-memory. In a production environment, this would be replaced with Redis or PostgreSQL.

## What Additional Context Would Have Helped?
1. **WhatsApp Meta Templates**: Having access to the exact allowed Meta template IDs and parameter shapes would have allowed us to produce perfectly compliant first-touch messages.
2. **Historical Click-Through Rates**: If the merchant contexts included which *types* of triggers (FOMO vs. analytical) a specific merchant engaged with historically, we could dynamically adjust the CTA strategy per merchant.

---
*Built for the magicpin AI Challenge by Farhan.*
