#!/usr/bin/env python3
"""
Quick end-to-end test of the Vera bot.
Pushes context, triggers a tick, sends replies, tests edge cases.
"""

import json
import sys
import time
from urllib import request as urlrequest, error as urlerror

BOT_URL = "http://localhost:8080"
DATASET_DIR = "../dataset"


def req(method, path, body=None, timeout=30):
    url = f"{BOT_URL}{path}"
    data = json.dumps(body).encode("utf-8") if body else None
    headers = {"Content-Type": "application/json"}
    r = urlrequest.Request(url, data=data, method=method, headers=headers)
    try:
        resp = urlrequest.urlopen(r, timeout=timeout)
        return json.loads(resp.read().decode("utf-8")), resp.status
    except urlerror.HTTPError as e:
        return json.loads(e.read().decode("utf-8")), e.code
    except Exception as e:
        return {"error": str(e)}, 0


def test(name, result, status, check_fn=None):
    passed = status == 200
    if check_fn:
        passed = passed and check_fn(result)
    icon = "✅" if passed else "❌"
    print(f"{icon} {name}")
    if not passed:
        print(f"   Status: {status}")
        print(f"   Result: {json.dumps(result, indent=2)[:300]}")
    return passed


def main():
    print("\n🧪 VERA BOT — END-TO-END TEST\n" + "=" * 50)

    # 1. Healthz
    r, s = req("GET", "/v1/healthz")
    test("GET /v1/healthz", r, s, lambda r: r.get("status") == "ok")

    # 2. Metadata
    r, s = req("GET", "/v1/metadata")
    test("GET /v1/metadata", r, s, lambda r: r.get("team_name") == "magicpin")

    # 3. Push category context
    with open(f"{DATASET_DIR}/categories/dentists.json") as f:
        dentists = json.load(f)
    r, s = req("POST", "/v1/context", {
        "scope": "category", "context_id": "dentists", "version": 1,
        "payload": dentists, "delivered_at": "2026-04-26T09:45:00Z"
    })
    test("POST /v1/context (category/dentists)", r, s, lambda r: r.get("accepted") == True)

    # 4. Push merchant context
    with open(f"{DATASET_DIR}/merchants_seed.json") as f:
        merchants = json.load(f)["merchants"]
    m001 = merchants[0]  # Dr. Meera
    r, s = req("POST", "/v1/context", {
        "scope": "merchant", "context_id": m001["merchant_id"], "version": 1,
        "payload": m001, "delivered_at": "2026-04-26T09:45:30Z"
    })
    test("POST /v1/context (merchant/drmeera)", r, s, lambda r: r.get("accepted") == True)

    # 5. Idempotency test (same version)
    r, s = req("POST", "/v1/context", {
        "scope": "merchant", "context_id": m001["merchant_id"], "version": 1,
        "payload": m001, "delivered_at": "2026-04-26T09:46:00Z"
    })
    test("POST /v1/context (idempotency — stale)", r, s, lambda r: r.get("reason") == "stale_version")

    # 6. Push customer context
    with open(f"{DATASET_DIR}/customers_seed.json") as f:
        customers = json.load(f)["customers"]
    c001 = customers[0]  # Priya
    r, s = req("POST", "/v1/context", {
        "scope": "customer", "context_id": c001["customer_id"], "version": 1,
        "payload": c001, "delivered_at": "2026-04-26T09:47:00Z"
    })
    test("POST /v1/context (customer/priya)", r, s, lambda r: r.get("accepted") == True)

    # 7. Push trigger context
    with open(f"{DATASET_DIR}/triggers_seed.json") as f:
        triggers = json.load(f)["triggers"]
    trg001 = triggers[0]  # research digest
    r, s = req("POST", "/v1/context", {
        "scope": "trigger", "context_id": trg001["id"], "version": 1,
        "payload": trg001, "delivered_at": "2026-04-26T10:32:00Z"
    })
    test("POST /v1/context (trigger/research_digest)", r, s, lambda r: r.get("accepted") == True)

    # 8. Healthz with counts
    r, s = req("GET", "/v1/healthz")
    counts = r.get("contexts_loaded", {})
    test("GET /v1/healthz (contexts loaded)", r, s,
         lambda r: r["contexts_loaded"]["category"] >= 1 and r["contexts_loaded"]["merchant"] >= 1)
    print(f"   Contexts: {counts}")

    # 9. Tick — compose a message
    print("\n--- TICK TEST (LLM composition) ---")
    r, s = req("POST", "/v1/tick", {
        "now": "2026-04-26T10:35:00Z",
        "available_triggers": [trg001["id"]]
    })
    actions = r.get("actions", [])
    test("POST /v1/tick (compose message)", r, s, lambda r: len(r.get("actions", [])) > 0)
    if actions:
        a = actions[0]
        print(f"\n   📩 COMPOSED MESSAGE:")
        print(f"   Body: {a.get('body', '?')[:200]}")
        print(f"   CTA: {a.get('cta', '?')}")
        print(f"   Send as: {a.get('send_as', '?')}")
        print(f"   Rationale: {a.get('rationale', '?')[:150]}")

        # 10. Reply — engaged merchant
        print("\n--- REPLY TEST (engaged merchant) ---")
        conv_id = a.get("conversation_id", "conv_test")
        r, s = req("POST", "/v1/reply", {
            "conversation_id": conv_id,
            "merchant_id": m001["merchant_id"],
            "customer_id": None,
            "from_role": "merchant",
            "message": "Yes please, send me the abstract.",
            "received_at": "2026-04-26T10:42:00Z",
            "turn_number": 2
        })
        test("POST /v1/reply (engaged)", r, s, lambda r: r.get("action") in ("send", "wait", "end"))
        if r.get("body"):
            print(f"   📩 REPLY: {r.get('body', '?')[:200]}")
            print(f"   Action: {r.get('action', '?')}")

    # 11. Auto-reply detection
    print("\n--- AUTO-REPLY DETECTION ---")
    auto_msg = "Thank you for contacting Dr. Meera's Dental Clinic! Our team will respond shortly."
    for i in range(1, 4):
        r, s = req("POST", "/v1/reply", {
            "conversation_id": "conv_auto_test",
            "merchant_id": m001["merchant_id"],
            "from_role": "merchant",
            "message": auto_msg,
            "received_at": f"2026-04-26T10:5{i}:00Z",
            "turn_number": i + 1
        })
        action = r.get("action", "?")
        print(f"   Turn {i}: action={action}, wait_s={r.get('wait_seconds', '-')}")
        if action == "end":
            print("   ✅ Bot correctly ended on repeated auto-reply")
            break

    # 12. Hostile handling
    print("\n--- HOSTILE HANDLING ---")
    r, s = req("POST", "/v1/reply", {
        "conversation_id": "conv_hostile_test",
        "merchant_id": m001["merchant_id"],
        "from_role": "merchant",
        "message": "Stop messaging me. This is useless spam.",
        "received_at": "2026-04-26T11:00:00Z",
        "turn_number": 2
    })
    action = r.get("action", "?")
    test("Hostile handling", r, s,
         lambda r: r.get("action") in ("end", "send"))
    print(f"   Action: {action}")
    if r.get("body"):
        print(f"   Body: {r.get('body', '?')[:200]}")

    # 13. Intent transition
    print("\n--- INTENT TRANSITION ---")
    r, s = req("POST", "/v1/reply", {
        "conversation_id": "conv_intent_test",
        "merchant_id": m001["merchant_id"],
        "from_role": "merchant",
        "message": "Ok let's do it. What's next?",
        "received_at": "2026-04-26T11:10:00Z",
        "turn_number": 3
    })
    test("Intent transition", r, s, lambda r: r.get("action") == "send")
    if r.get("body"):
        print(f"   Body: {r.get('body', '?')[:200]}")

    print("\n" + "=" * 50)
    print("✅ ALL TESTS COMPLETE\n")


if __name__ == "__main__":
    main()
