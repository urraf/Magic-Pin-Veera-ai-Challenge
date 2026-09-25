"""
Conversation Tracker — Tracks per-conversation state for multi-turn handling.
Supports auto-reply detection, intent transitions, and suppression tracking.
"""

import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class Turn:
    """A single turn in a conversation."""
    turn_number: int
    from_role: str        # "vera" | "merchant" | "customer"
    body: str
    timestamp: str
    action: str = "send"  # "send" | "wait" | "end"


@dataclass
class ConversationState:
    """Full state for one conversation."""
    conversation_id: str
    merchant_id: str
    customer_id: Optional[str]
    trigger_id: str
    send_as: str               # "vera" | "merchant_on_behalf"
    turns: list[Turn] = field(default_factory=list)
    status: str = "active"     # "active" | "ended" | "waiting"
    auto_reply_count: int = 0
    ended_reason: str = ""
    suppressed: bool = False

    @property
    def turn_count(self) -> int:
        return len(self.turns)

    @property
    def last_bot_body(self) -> str:
        """Return the last message the bot sent in this conversation."""
        for turn in reversed(self.turns):
            if turn.from_role in ("vera", "bot"):
                return turn.body
        return ""

    def merchant_messages(self) -> list[str]:
        """All messages the merchant/customer sent."""
        return [t.body for t in self.turns if t.from_role in ("merchant", "customer")]


class ConversationTracker:
    """
    Thread-safe conversation store.
    Key: conversation_id -> ConversationState.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._conversations: dict[str, ConversationState] = {}
        # Tracks suppressed conversation_ids (merchant said stop / too many auto-replies)
        self._suppressed_conversations: set[str] = set()
        # Tracks suppressed merchants (merchant explicitly opted out)
        self._suppressed_merchants: set[str] = set()

    # ------------------------------------------------------------------
    # Create / get conversation
    # ------------------------------------------------------------------
    def create(
        self,
        conversation_id: str,
        merchant_id: str,
        customer_id: Optional[str],
        trigger_id: str,
        send_as: str,
        initial_body: str,
        timestamp: str,
    ) -> ConversationState:
        with self._lock:
            state = ConversationState(
                conversation_id=conversation_id,
                merchant_id=merchant_id,
                customer_id=customer_id,
                trigger_id=trigger_id,
                send_as=send_as,
            )
            state.turns.append(Turn(
                turn_number=1,
                from_role="vera" if send_as == "vera" else "merchant_on_behalf",
                body=initial_body,
                timestamp=timestamp,
            ))
            self._conversations[conversation_id] = state
            return state

    def get(self, conversation_id: str) -> Optional[ConversationState]:
        with self._lock:
            return self._conversations.get(conversation_id)

    # ------------------------------------------------------------------
    # Record turns
    # ------------------------------------------------------------------
    def record_merchant_reply(
        self, conversation_id: str, body: str, timestamp: str, turn_number: int
    ) -> Optional[ConversationState]:
        with self._lock:
            state = self._conversations.get(conversation_id)
            if not state:
                # Create a minimal state for unknown conversations
                state = ConversationState(
                    conversation_id=conversation_id,
                    merchant_id="",
                    customer_id=None,
                    trigger_id="",
                    send_as="vera",
                )
                self._conversations[conversation_id] = state

            state.turns.append(Turn(
                turn_number=turn_number,
                from_role="merchant",
                body=body,
                timestamp=timestamp,
            ))
            return state

    def record_bot_reply(
        self, conversation_id: str, body: str, action: str, timestamp: str
    ) -> None:
        with self._lock:
            state = self._conversations.get(conversation_id)
            if state:
                state.turns.append(Turn(
                    turn_number=state.turn_count + 1,
                    from_role="vera",
                    body=body,
                    timestamp=timestamp,
                    action=action,
                ))
                if action == "end":
                    state.status = "ended"
                    state.ended_reason = "bot_ended"
                elif action == "wait":
                    state.status = "waiting"

    # ------------------------------------------------------------------
    # Auto-reply detection helpers
    # ------------------------------------------------------------------
    def increment_auto_reply(self, conversation_id: str) -> int:
        """Returns the new auto_reply_count."""
        with self._lock:
            state = self._conversations.get(conversation_id)
            if state:
                state.auto_reply_count += 1
                return state.auto_reply_count
            return 0

    # ------------------------------------------------------------------
    # Suppression
    # ------------------------------------------------------------------
    def suppress_conversation(self, conversation_id: str) -> None:
        with self._lock:
            self._suppressed_conversations.add(conversation_id)
            state = self._conversations.get(conversation_id)
            if state:
                state.suppressed = True
                state.status = "ended"

    def suppress_merchant(self, merchant_id: str) -> None:
        with self._lock:
            self._suppressed_merchants.add(merchant_id)

    def is_conversation_suppressed(self, conversation_id: str) -> bool:
        with self._lock:
            return conversation_id in self._suppressed_conversations

    def is_merchant_suppressed(self, merchant_id: str) -> bool:
        with self._lock:
            return merchant_id in self._suppressed_merchants

    # ------------------------------------------------------------------
    # Active conversation lookup (for suppression-key dedup at tick time)
    # ------------------------------------------------------------------
    def active_suppression_keys(self) -> set[str]:
        """Return all suppression keys from active/recently-sent conversations."""
        # Not implemented yet — placeholder for dedup
        return set()
