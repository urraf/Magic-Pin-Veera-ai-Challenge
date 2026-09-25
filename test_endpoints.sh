#!/bin/bash
echo "=== 1. GET /v1/healthz ==="
curl -s http://localhost:8080/v1/healthz | python3 -m json.tool

echo -e "\n=== 2. GET /v1/metadata ==="
curl -s http://localhost:8080/v1/metadata | python3 -m json.tool

echo -e "\n=== 3. POST /v1/context (Category) ==="
curl -s -X POST http://localhost:8080/v1/context -H "Content-Type: application/json" -d '{
  "scope": "category",
  "context_id": "test_cat",
  "version": 1,
  "payload": {"name": "Test"},
  "delivered_at": "2026-09-26T00:00:00Z"
}' | python3 -m json.tool

echo -e "\n=== 4. POST /v1/context (Merchant) ==="
curl -s -X POST http://localhost:8080/v1/context -H "Content-Type: application/json" -d '{
  "scope": "merchant",
  "context_id": "test_merchant",
  "version": 1,
  "payload": {"name": "Test Merchant"},
  "delivered_at": "2026-09-26T00:00:00Z"
}' | python3 -m json.tool

echo -e "\n=== 5. POST /v1/context (Trigger) ==="
curl -s -X POST http://localhost:8080/v1/context -H "Content-Type: application/json" -d '{
  "scope": "trigger",
  "context_id": "test_trigger",
  "version": 1,
  "payload": {"id": "test_trigger", "merchant_id": "test_merchant", "kind": "info"},
  "delivered_at": "2026-09-26T00:00:00Z"
}' | python3 -m json.tool

echo -e "\n=== 6. POST /v1/tick ==="
curl -s -X POST http://localhost:8080/v1/tick -H "Content-Type: application/json" -d '{
  "now": "2026-09-26T00:01:00Z",
  "available_triggers": ["test_trigger"]
}' | python3 -m json.tool

echo -e "\n=== 7. POST /v1/reply ==="
curl -s -X POST http://localhost:8080/v1/reply -H "Content-Type: application/json" -d '{
  "conversation_id": "conv_test_merchant_test_trigger",
  "merchant_id": "test_merchant",
  "from_role": "merchant",
  "message": "Stop messaging me",
  "received_at": "2026-09-26T00:05:00Z",
  "turn_number": 2
}' | python3 -m json.tool
