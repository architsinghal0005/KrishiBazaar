"""
Booking and Approval Service for KrishiBazaar.
Manages option selection, pending proposal creation with expiry, explicit farmer approval validation,
simulated booking record generation, and status lookups.
Strictly ensures no real payment, external carrier dispatch, or commercial contract is executed.
"""

from __future__ import annotations
from datetime import datetime, timezone, timedelta
from enum import Enum
import re
from uuid import uuid4
from typing import Any

from krishibazaar.models import (
    BookingProposal,
    BookingRecord,
    NetReturnBreakdown,
)


class BookingActionType(str, Enum):
    """Recognized booking interaction actions."""

    APPROVE = "APPROVE"
    CANCEL = "CANCEL"
    SELECT_OPTION = "SELECT_OPTION"
    STATUS_LOOKUP = "STATUS_LOOKUP"
    PAYMENT_STATUS_LOOKUP = "PAYMENT_STATUS_LOOKUP"
    PAYMENT_READINESS_AUDIT = "PAYMENT_READINESS_AUDIT"
    REQUEST_PAYMENT_REVIEW = "REQUEST_PAYMENT_REVIEW"
    AUTHORIZE_PAYMENT = "AUTHORIZE_PAYMENT"
    REJECT_PAYMENT = "REJECT_PAYMENT"
    NONE = "NONE"


def strip_agent_mention(text: str) -> str:
    """
    Strip leading Agentverse / Chat mention prefixes such as:
    - @agent1qw928734h...
    - <@agent1qw928734h...>
    - @orchestrator
    - <@orchestrator>
    - @agent_name:
    """
    if not text or not isinstance(text, str):
        return ""
    pattern = r"^\s*(?:<@?[a-zA-Z0-9_\-\.]+>|@[a-zA-Z0-9_\-\.]+[:\s]*)\s*"
    cleaned = re.sub(pattern, "", text.strip())
    while True:
        subbed = re.sub(pattern, "", cleaned.strip())
        if subbed == cleaned:
            break
        cleaned = subbed
    return cleaned.strip()


def is_casual_acknowledgement(text: str) -> bool:
    """
    Check if the user message is a casual greeting, acknowledgement, or affirmative phrase
    that does NOT constitute unambiguous explicit approval for a booking proposal.
    Examples: 'ok', 'okay', 'yes', 'yep', 'sounds good', 'thanks', 'cool', 'sure', 'fine', 'looks good'.
    """
    if not text or not isinstance(text, str):
        return False
    cleaned = strip_agent_mention(text).strip().lower()
    norm = re.sub(r"[^\w\s]", "", cleaned).strip()
    if not norm:
        return False

    exact_matches = {
        "ok", "okay", "k", "kk", "okk", "okie", "okey",
        "yes", "yeah", "yep", "yup", "ya", "yea", "aye",
        "sure", "fine", "cool", "done", "alright", "all right",
        "good", "great", "perfect", "nice", "awesome", "super",
        "sounds good", "sounds great", "looks good", "looks great",
        "sounds good to me", "looks good to me", "looks fine", "looks ok",
        "all looks good", "all good", "looks fine to me",
        "thanks", "thank you", "thx", "many thanks", "ty", "cheers",
        "agree", "agreed", "understood", "got it", "i see",
        "hi", "hello", "hey", "greetings", "namaste",
        "ok sounds good", "ok thanks", "yes thanks", "ok cool", "ok done",
        "yes sure", "yes please", "sure thing", "go ahead",
        "sounds good thanks", "looks good thanks", "ok got it",
        "ok confirm", "confirm ok", "looks good confirm", "ok proceed",
        "proceed", "let's go", "lets go", "go with this", "ok deal",
        "sounds fine", "sounds ok",
    }
    if norm in exact_matches:
        return True

    words = norm.split()
    ack_words = {
        "ok", "okay", "k", "yes", "yeah", "yep", "yup", "sure", "fine", "cool",
        "done", "alright", "good", "great", "perfect", "nice", "awesome",
        "sounds", "looks", "thanks", "thank", "you", "thx", "agree", "agreed",
        "understood", "got", "it", "hi", "hello", "hey", "to", "me", "please",
    }
    if len(words) <= 6 and all(w in ack_words for w in words):
        return True

    return False


def is_informational_query(text: str) -> bool:
    """
    Check if the user message is an introductory, informational, or explanatory request
    about KrishiBazaar, its capabilities, or how it helps farmers.
    Examples:
    - 'I am a farmer growing onions in Nashik. Please introduce KrishiBazaar and explain how it helps farmers.'
    - 'Please introduce KrishiBazaar'
    - 'What is KrishiBazaar?'
    - 'Explain how KrishiBazaar works'
    - 'How does KrishiBazaar help farmers?'
    - 'Who are you and what do you do?'
    """
    if not text or not isinstance(text, str):
        return False
    cleaned = strip_agent_mention(text).strip().lower()

    # Informational keywords and substrings
    info_substrings = [
        "introduce krishibazaar",
        "introduction to krishibazaar",
        "please introduce",
        "introduce yourself",
        "what is krishibazaar",
        "about krishibazaar",
        "explain krishibazaar",
        "explain how krishibazaar",
        "explain how it helps",
        "explain how you help",
        "how does krishibazaar help",
        "how does it help farmers",
        "how do you help farmers",
        "how it helps farmers",
        "how krishibazaar works",
        "how does krishibazaar work",
        "how does this platform work",
        "how does this work",
        "what does krishibazaar do",
        "who are you",
        "what can you do",
        "what do you do",
        "what is this platform",
        "what is this bot",
        "what is this service",
        "what is this agent",
        "tell me about krishibazaar",
        "overview of krishibazaar",
        "help me understand krishibazaar",
        "can you help me",
        "help me",
        "help",
        "what services do you offer",
        "what services do you provide",
        "what crops do you support",
        "which crops do you support",
        "what crops can i sell",
        "how do i use this",
        "how to use",
    ]
    if any(sub in cleaned for sub in info_substrings):
        return True

    # Regex patterns
    patterns = [
        r"\b(?:introduce|introduction)\b",
        r"\bexplain\s+(?:how\s+)?(?:krishibazaar|it|this\s+(?:agent|platform|service)|you)\s+(?:helps?|works?)\b",
        r"\bhow\s+(?:does\s+)?(?:krishibazaar|it|this\s+platform|you)\s+(?:help|work)\b",
        r"\bwhat\s+(?:is|does)\s+(?:krishibazaar|this\s+(?:platform|agent|service|bot))\b",
        r"\b(?:tell\s+me\s+about|learn\s+about)\s+(?:krishibazaar|this\s+platform)\b",
        r"\bwho\s+are\s+you\b",
        r"^(?:help|\?|help\s+me|info|information)$",
    ]
    for pattern in patterns:
        if re.search(pattern, cleaned, re.IGNORECASE):
            return True

    return False


def is_greeting(text: str) -> bool:
    """
    Check if a message is a greeting or introductory pleasantry without produce inquiry details.
    Examples:
    - 'hi', 'hello', 'hey', 'namaste', 'good morning', 'hello there', 'namaste ji'
    """
    if not text or not isinstance(text, str):
        return False
    cleaned = strip_agent_mention(text).strip().lower()
    # Remove basic punctuation
    norm = re.sub(r"[^\w\s]", "", cleaned).strip()
    if not norm:
        return False

    greeting_exact = {
        "hi", "hello", "hey", "namaste", "namaskar", "namaskaram",
        "greetings", "good morning", "good afternoon", "good evening",
        "good day", "howdy", "pranam", "ram ram", "radhe radhe",
        "jai shri ram", "jai kisan", "kem cho", "vanakkam", "sat sri akal",
        "adaab", "hello there", "hi there", "namaste ji", "hey there",
        "hello krishibazaar", "hi krishibazaar", "namaste krishibazaar",
    }
    if norm in greeting_exact:
        return True

    words = norm.split()
    greeting_vocab = {
        "hi", "hello", "hey", "namaste", "namaskar", "namaskaram",
        "greetings", "morning", "afternoon", "evening", "day", "howdy",
        "pranam", "ram", "radhe", "kisan", "vanakkam", "adaab",
        "there", "ji", "krishibazaar", "sir", "bhai", "assistant",
        "bot", "agent", "good", "all",
    }
    if len(words) <= 4 and all(w in greeting_vocab for w in words):
        return True

    return False


def is_diagnostic_text(text: str) -> bool:
    """
    Check if a message is a diagnostic, test, ping, or healthcheck string
    rather than a farmer crop inquiry or booking action.
    Examples:
    - 'NEW MESSAGE RECEIVED'
    - 'ping', 'pong', 'test', 'status check', 'diagnostic ping'
    """
    if not text or not isinstance(text, str):
        return False
    cleaned = strip_agent_mention(text).strip().lower()
    norm = re.sub(r"[^\w\s]", "", cleaned).strip()

    if "new message received" in cleaned:
        return True

    diagnostic_exact = {
        "ping", "pong", "test", "echo", "diagnostic", "diagnostics",
        "healthcheck", "health check", "status check", "heartbeat",
        "smoke test", "connectivity check", "system check",
    }
    if norm in diagnostic_exact:
        return True

    diagnostic_patterns = [
        r"^test(?:\s+(?:run|message|ping|envelope|query))?(?:\s*#?\d+)?$",
        r"^diagnostic(?:\s+(?:message|query|ping|run))?(?:\s*#?\d+)?$",
        r"^(?:ping|pong|echo)\b",
    ]
    for pattern in diagnostic_patterns:
        if re.search(pattern, norm):
            return True

    return False


def has_produce_signals(intent: Any, text: str) -> bool:
    """
    Determines whether a user message contains explicit agricultural produce,
    quantity, location, or trade parameters indicating a crop inquiry refinement.
    Prevents greetings, diagnostics, general questions, and unrelated text
    from accidentally inheriting past session state or triggering specialists.
    """
    if intent is not None:
        if getattr(intent, "crop", None):
            return True
        if getattr(intent, "quantity_quintals", None) is not None:
            return True
        if getattr(intent, "origin_location", None):
            return True
        if getattr(intent, "selling_deadline", None):
            return True
        if getattr(intent, "minimum_net_return_inr", None) is not None:
            return True
        if getattr(intent, "storage_preference", None):
            return True

    if not text or not isinstance(text, str):
        return False

    cleaned = strip_agent_mention(text).strip().lower()

    # Known agricultural crop keywords
    from krishibazaar.services.extraction import KNOWN_CROPS
    for alias in KNOWN_CROPS:
        if re.search(rf"\b{alias}\b", cleaned):
            return True

    # Weight / volume keywords
    if re.search(r"\b(?:\d+(?:\.\d+)?\s*)?(?:quintals?|qtl|qtls?|tonnes?|metric\s+tons?|tons?|kg|kilograms?|bags?|crates?)\b", cleaned):
        return True

    # Location indicator with candidate name (e.g. "in Nashik", "from Pune", "near Indore")
    if re.search(r"\b(?:in|at|near|from)\s+[a-z]{3,}\b", cleaned):
        # Exclude common false positives
        if not re.search(r"\b(?:in|at|near|from)\s+(?:days?|hours?|bulk|advance|cash|transit|english|hindi|marathi|general|short|brief)\b", cleaned):
            return True

    # Produce selling keywords
    produce_keywords = (
        "want to sell", "selling", "sell produce", "sell crop",
        "mandi price", "mandi prices", "modal price", "apmc price",
        "crop rate", "market rate", "fresh quote", "new quote",
    )
    if any(kw in cleaned for kw in produce_keywords):
        return True

    return False


def extract_booking_reference(text: str) -> str | None:
    """Extract a target booking reference ID (e.g. KB-BOOK-20261023-93DB4D) from text."""
    if not text or not isinstance(text, str):
        return None
    cleaned = text.strip()

    # If there are booking-pay patterns, they capture the explicit target booking
    booking_pay_patterns = [
        r"(?:check|track|status|records?|details|lookup|audit|is\s+there\s+(?:a\s+|any\s+)?|no\s+).*?payment.*?(?:for|linked\s+(?:specifically\s+)?to|to|of|on|towards)\s+(?:booking\s+)?(kb-book-[a-z0-9\-]+)",
        r"payment.*?(?:linked\s+(?:specifically\s+)?to|for|of|towards|on)\s+(?:booking\s+)?(kb-book-[a-z0-9\-]+)",
        r"(?:is\s+there\s+(?:a\s+|any\s+)?payment|no\s+payment\s+linked\s+to|any\s+payment\s+(?:for|to))\s+(?:booking\s+)?(kb-book-[a-z0-9\-]+)",
        r"(kb-book-[a-z0-9\-]+).*?(?:payment\s+status|payment\s+records?|check\s+payment|payment\s+readiness|audit\s+payment|is\s+payment\s+enabled|have\s+(?:a\s+)?payment\s+record)",
        r"(?:payment\s+status|check\s+payment|audit\s+payment|payment\s+readiness|is\s+payment\s+enabled)\s+(?:for|of)?\s*(kb-book-[a-z0-9\-]+)",
    ]
    for pattern in booking_pay_patterns:
        m = re.search(pattern, cleaned, re.IGNORECASE)
        if m:
            return m.group(1).upper()

    matches = re.findall(r"\b(kb-book-[a-z0-9\-]+)\b", cleaned, re.IGNORECASE)
    if not matches:
        return None
    for b in matches:
        is_negated = re.search(
            r"(?:was\s+for|previous\s+(?:booking|transaction)|older\s+(?:booking|transaction)|ignore|not\s+for)\s+.*?" + re.escape(b),
            cleaned,
            re.IGNORECASE,
        )
        if not is_negated:
            return b.upper()
    return matches[-1].upper()


def extract_proposal_reference(text: str) -> str | None:
    """Extract a proposal reference ID (e.g. PROP-20261023-2FCD0E or KB-PROP-...) from text."""
    if not text or not isinstance(text, str):
        return None
    m = re.search(r"\b((?:kb-)?prop-[a-z0-9\-]+)\b", text, re.IGNORECASE)
    return m.group(1).upper() if m else None


def extract_payment_reference(text: str) -> str | None:
    """Extract a payment reference ID (e.g. KB-PAY-27A4A2EC1F3E) from text."""
    if not text or not isinstance(text, str):
        return None
    m = re.search(r"\b(kb-pay-[a-z0-9\-]+)\b", text, re.IGNORECASE)
    return m.group(1).upper() if m else None


def is_payment_readiness_query(text: str) -> bool:
    """
    Check if the user message is asking about payment readiness, payment agent operational status,
    payment execution configuration, booking payment audit, or proposal-specific payment readiness.
    Examples:
    - 'Check payment readiness for proposal PROP-20261023-2FCD0E.'
    - 'Audit payment readiness for KB-BOOK-20261023-93DB4D'
    - 'Is payment enabled for booking KB-BOOK-20261023-93DB4D?'
    - 'Does booking KB-BOOK-20261023-93DB4D have a payment record?'
    - 'Is the payment agent operational?'
    - 'Is payment execution enabled?'
    - 'Can payment readiness be checked for this proposal without confirming a booking?'
    - 'Payment readiness for PROP-20261023-2FCD0E'
    - 'Check payment readiness'
    - 'Audit payment readiness'
    - 'Is payment ready for proposal PROP-20261023-2FCD0E?'
    - 'Is payment protocol active?'
    """
    if not text or not isinstance(text, str):
        return False
    cleaned = strip_agent_mention(text).strip().lower()

    # Substrings indicating readiness / payment agent audit
    readiness_substrings = [
        "payment readiness",
        "readiness audit",
        "readiness check",
        "check readiness",
        "audit readiness",
        "verify readiness",
        "is payment ready",
        "ready for payment",
        "payment agent operational",
        "payment service operational",
        "payment agent running",
        "payment execution enabled",
        "payment protocol enabled",
        "payment protocol active",
        "payment protocol operational",
        "payment agent status",
        "payment service status",
        "payment agent health",
        "can payment readiness be checked",
        "audit payment",
        "payment audit",
        "is payment enabled",
        "payment enabled",
        "have a payment record",
        "has a payment record",
    ]
    if any(sub in cleaned for sub in readiness_substrings):
        return True

    # Regex patterns
    patterns = [
        r"\b(?:check|audit|verify|status|lookup)\b.*?(?:payment\s+readiness|readiness)",
        r"\b(?:payment\s+readiness|readiness\s+audit|readiness\s+check)\b",
        r"\b(?:is\s+payment\s+ready|ready\s+for\s+payment)\b",
        r"\b(?:is\s+(?:the\s+)?payment\s+(?:agent|service|subsystem)\s+(?:operational|active|running|online|responding|ready|enabled|up))\b",
        r"\b(?:is\s+(?:the\s+)?payment\s+(?:protocol|execution)\s+(?:enabled|active|turned\s+on|live|disabled|operational|running))\b",
        r"\b(?:is\s+payment\s+enabled)\b",
        r"\b(?:payment\s+(?:agent|service|protocol)\s+(?:status|readiness|health|operational))\b",
        r"\bcan\s+(?:we\s+|i\s+)?(?:check\s+)?payment\s+readiness\b",
        r"\breadiness\s+(?:for|of)\s+(?:proposal\s+)?((?:kb-)?prop-[a-z0-9\-]+)",
        r"((?:kb-)?prop-[a-z0-9\-]+).*?(?:payment\s+readiness|readiness|is\s+payment\s+ready)",
        r"(?:audit\s+payment\s+readiness|check\s+payment\s+readiness|payment\s+readiness).*?(kb-book-[a-z0-9\-]+)",
        r"(kb-book-[a-z0-9\-]+).*?(?:audit\s+payment|payment\s+readiness|is\s+payment\s+enabled|have\s+(?:a\s+)?payment\s+record)",
        r"\b(?:does\s+booking\s+.*?have\s+(?:a\s+)?payment\s+record)\b",
    ]
    for pattern in patterns:
        if re.search(pattern, cleaned, re.IGNORECASE):
            return True

    return False


def parse_booking_action(text: str) -> tuple[BookingActionType, str | None]:
    """
    Parse farmer's natural-language message to detect explicit booking commands:
    - Status lookup: queries about booking references (e.g. 'Status of KB-BOOK-...', 'Track KB-BOOK-...')
    - Payment status lookup: queries about payment references (e.g. 'Status of KB-PAY-...', 'Track KB-PAY-...')
    - Payment readiness audit: queries about payment readiness, agent operational status, or proposals (e.g. 'Check payment readiness for PROP-...')
    - Explicit approval: unambiguous affirmative confirmation (e.g. 'Confirm booking', 'Approve', 'Yes confirm')
    - Cancellation: explicit abort (e.g. 'Cancel', 'Reject', 'Do not book')
    - Option selection: choosing a ranked option (e.g. 'Select option 1', 'Choose rank 2', 'Book option 1')
    - None: regular enquiry, casual greeting, or unrelated message.
    """
    if not text or not isinstance(text, str):
        return BookingActionType.NONE, None

    # Priority Safety Gate: Casual greetings, ambiguous acknowledgements, or informal affirmative phrases
    # must NEVER be parsed as explicit booking approval
    if is_casual_acknowledgement(text):
        return BookingActionType.NONE, None

    cleaned = strip_agent_mention(text).strip().lower()

    # 0. JSON String Check (Web/Chat CTA selection payload fallback)
    if cleaned.startswith("{") and cleaned.endswith("}"):
        import json
        try:
            data = json.loads(cleaned)
            act = data.get("action")
            if act in ("select_channel", "select_option"):
                param = data.get("rank") or data.get("option_id")
                return BookingActionType.SELECT_OPTION, str(param) if param is not None else "1"
            elif act in ("approve_booking", "approve"):
                prop_id = data.get("proposal_id")
                return BookingActionType.APPROVE, str(prop_id) if prop_id else None
            elif act in ("reject_booking", "cancel", "decline"):
                prop_id = data.get("proposal_id")
                return BookingActionType.CANCEL, str(prop_id) if prop_id else None
            elif act in ("payment_readiness", "check_payment_readiness", "payment_readiness_audit"):
                b_id = data.get("booking_id")
                prop_id = data.get("proposal_id") or data.get("reference")
                if b_id and str(b_id).upper().startswith("KB-BOOK-"):
                    return BookingActionType.PAYMENT_STATUS_LOOKUP, str(b_id).upper()
                if prop_id and str(prop_id).upper().startswith("KB-BOOK-"):
                    return BookingActionType.PAYMENT_STATUS_LOOKUP, str(prop_id).upper()
                return BookingActionType.PAYMENT_READINESS_AUDIT, str(prop_id).upper() if prop_id else None
            elif act in ("payment_status_lookup", "check_payment", "payment_status"):
                p_ref = data.get("reference") or data.get("payment_reference") or data.get("booking_id")
                return BookingActionType.PAYMENT_STATUS_LOOKUP, str(p_ref) if p_ref else None
            elif act in ("status_lookup", "check_status"):
                b_id = data.get("booking_id") or data.get("reference")
                if b_id and str(b_id).upper().startswith("KB-PAY-"):
                    return BookingActionType.PAYMENT_STATUS_LOOKUP, str(b_id).upper()
                return BookingActionType.STATUS_LOOKUP, str(b_id) if b_id else None
        except Exception:
            pass

    # 0.4 Booking-targeted Payment Status / Readiness Check (Priority: Booking-ID takes precedence)
    book_id_match = extract_booking_reference(cleaned)
    if book_id_match:
        # Check if the query is asking about payment, payment readiness, or payment records for this booking
        is_pay_query = (
            is_payment_readiness_query(cleaned)
            or bool(re.search(r"\b(?:payment|pay|records?|paid|transaction)\b", cleaned, re.IGNORECASE))
        )
        if is_pay_query:
            return BookingActionType.PAYMENT_STATUS_LOOKUP, book_id_match

    # 0.5 Proposal Payment Readiness Audit Check
    # If the user supplies PROP-..., route to proposal payment-readiness audit
    prop_id_match = extract_proposal_reference(cleaned)
    if prop_id_match and (is_payment_readiness_query(cleaned) or bool(re.search(r"\b(?:payment|pay|readiness)\b", cleaned, re.IGNORECASE))):
        return BookingActionType.PAYMENT_READINESS_AUDIT, prop_id_match

    # 0.6 General Payment Readiness Audit Check (No explicit booking or proposal reference)
    if is_payment_readiness_query(cleaned):
        return BookingActionType.PAYMENT_READINESS_AUDIT, None

    # 0.65 Payment Review Request Check (e.g. 'Proceed to Payment Review', 'Proceed to Payment', 'Pay advisory fee')
    pay_review_patterns = [
        r"(?:proceed\s+to\s+payment(?:\s+review)?|request\s+payment(?:\s+review)?|pay\s+advisory(?:\s+fee)?|review\s+payment)\s+(?:for\s+)?(kb-book-[a-z0-9\-]+)",
        r"(kb-book-[a-z0-9\-]+).*?(?:proceed\s+to\s+payment(?:\s+review)?|request\s+payment|pay\s+advisory|review\s+payment)",
        r"^(?:proceed\s+to\s+payment(?:\s+review)?|request\s+payment(?:\s+review)?|pay\s+advisory(?:\s+fee)?|review\s+payment|payment\s+review|testnet\s+payment|pay\s+fee)$",
        r"\b(?:proceed\s+to\s+payment(?:\s+review)?|pay\s+advisory\s+fee)\b",
    ]
    for pattern in pay_review_patterns:
        match = re.search(pattern, cleaned, re.IGNORECASE)
        if match:
            target_id = match.group(1).upper() if match.groups() and match.group(1) else None
            return BookingActionType.REQUEST_PAYMENT_REVIEW, target_id

    # 0.7 Payment Status Lookup Check (Additional patterns for payments without booking ID)
    # Case B & C: Booking-targeted payment inquiries (e.g. 'Check payment records linked specifically to booking KB-BOOK-...')
    booking_pay_patterns = [
        r"(?:check|track|status|records?|details|lookup|is\s+there\s+(?:a\s+|any\s+)?|no\s+).*?payment.*?(?:for|linked\s+(?:specifically\s+)?to|to|of|on|towards)\s+(?:booking\s+)?(kb-book-[a-z0-9\-]+)",
        r"payment.*?(?:linked\s+(?:specifically\s+)?to|for|of|towards|on)\s+(?:booking\s+)?(kb-book-[a-z0-9\-]+)",
        r"(?:is\s+there\s+(?:a\s+|any\s+)?payment|no\s+payment\s+linked\s+to|any\s+payment\s+(?:for|to))\s+(?:booking\s+)?(kb-book-[a-z0-9\-]+)",
        r"(kb-book-[a-z0-9\-]+).*?(?:payment\s+status|payment\s+records?|check\s+payment|payment\s+readiness|audit\s+payment|is\s+payment\s+enabled|have\s+(?:a\s+)?payment\s+record)",
        r"(?:payment\s+status|check\s+payment|audit\s+payment|payment\s+readiness|is\s+payment\s+enabled)\s+(?:for|of)?\s*(kb-book-[a-z0-9\-]+)",
    ]
    for pattern in booking_pay_patterns:
        match = re.search(pattern, cleaned, re.IGNORECASE)
        if match:
            return BookingActionType.PAYMENT_STATUS_LOOKUP, match.group(1).upper()

    # Case C: Check if this booking's payment is queried without ID (e.g. 'check this booking's payment and do not use the previous transaction')
    if re.search(r"(?:this\s+booking[\'s]*\s+payment|payment\s+for\s+this\s+booking|check\s+(?:this\s+booking|my\s+booking)[\'s]*\s+payment)", cleaned, re.IGNORECASE):
        return BookingActionType.PAYMENT_STATUS_LOOKUP, None

    # Case A: Explicit Payment-reference targeted lookup (e.g., 'Status of KB-PAY-...', 'KB-PAY-...')
    pay_matches = re.findall(r"\b(kb-pay-[a-z0-9\-]+)\b", cleaned, re.IGNORECASE)
    if pay_matches:
        target_pay = None
        for p in pay_matches:
            is_negated = re.search(
                r"(?:do\s+not\s+use|don\'t\s+use|not\s+use|ignore|was\s+for|previous\s+transaction|older\s+transaction)\s+.*?" + re.escape(p),
                cleaned,
                re.IGNORECASE,
            )
            if not is_negated:
                target_pay = p.upper()
                break
        if target_pay:
            return BookingActionType.PAYMENT_STATUS_LOOKUP, target_pay
        else:
            return BookingActionType.PAYMENT_STATUS_LOOKUP, None

    # Case D: Generic payment status inquiries
    generic_pay_patterns = [
        r"\b(?:payment\s+status|status\s+of\s+(?:the\s+|my\s+)?payment|check\s+(?:the\s+|my\s+)?payment|track\s+(?:the\s+|my\s+)?payment|is\s+payment\s+(?:done|complete|settled|received|verified))\b",
        r"^(?:payment\s+status|check\s+payment|track\s+payment|payment\s+details)$",
    ]
    for pattern in generic_pay_patterns:
        if re.search(pattern, cleaned, re.IGNORECASE):
            return BookingActionType.PAYMENT_STATUS_LOOKUP, None

    # 1. Booking Status Lookup Check
    status_patterns = [
        r"\b(?:status|track|check|lookup|where\s+is)\b.*?(kb-book-[a-z0-9\-]+)",
        r"\b(kb-book-[a-z0-9\-]+)\b",
        r"\b(?:booking\s+status|status\s+of\s+(?:the\s+|my\s+)?booking|check\s+(?:the\s+|my\s+)?booking|track\s+(?:the\s+|my\s+)?booking|where\s+is\s+(?:the\s+|my\s+)?booking|booking\s+details|my\s+booking)\b",
        r"^(?:status|track|booking\s+status|check\s+status|booking\s+details)$",
    ]
    for pattern in status_patterns:
        match = re.search(pattern, cleaned, re.IGNORECASE)
        if match:
            # If a specific booking ID was captured, return it
            booking_id = match.group(1).upper() if match.groups() and match.group(1) else None
            return BookingActionType.STATUS_LOOKUP, booking_id

    # 2. Explicit Cancellation Check
    cancellation_phrases = [
        r"^\s*(?:cancel|reject|abort|discard)\s*(?:booking|proposal)?\s*$",
        r"^\s*do\s+not\s+book\b",
        r"^\s*no\s*,\s*cancel\b",
    ]
    for pattern in cancellation_phrases:
        if re.search(pattern, cleaned, re.IGNORECASE):
            return BookingActionType.CANCEL, None

    # 3. Explicit Approval Check
    # Must be unambiguous affirmative approval. Casual greetings like 'hi', 'ok', 'looks good', 'thanks' must NOT match.
    approval_patterns = [
        r"^\s*(?:confirm\s+booking|approve\s+booking|proceed\s+with\s+booking|accept\s+proposal)\s*$",
        r"^\s*(?:yes\s*,?\s*approve|yes\s*,?\s*confirm)\s*$",
        r"^\s*(?:i\s+approve|i\s+confirm)\s*$",
        r"^\s*approve\s+(?:proposal\s+)?((?:kb-)?prop-[a-z0-9\-]+)\s*$",
        r"^\s*confirm\s+(?:proposal\s+)?((?:kb-)?prop-[a-z0-9\-]+)\s*$",
        r"^\s*book\s+this\s+now\s*$",
        r"^\s*(?:approve|confirm)\s*$",
    ]
    for pattern in approval_patterns:
        match = re.search(pattern, cleaned, re.IGNORECASE)
        if match:
            proposal_id = match.group(1).upper() if match.groups() and match.group(1) else None
            return BookingActionType.APPROVE, proposal_id

    # 4. Option Selection Check
    # e.g., 'select option 1', 'choose rank 2', 'option 1', 'book option 1', 'select top option', '1', '#1'
    if re.search(r"\b(?:select|choose|pick|book)\s+(?:top\s+option|recommendation)\b", cleaned, re.IGNORECASE):
        return BookingActionType.SELECT_OPTION, "1"

    rank_match = re.search(r"\b(?:select|choose|pick|book|opt\s+for)\s+(?:option|rank|channel)?\s*#?([1-9]\d*)\b", cleaned, re.IGNORECASE)
    if rank_match:
        return BookingActionType.SELECT_OPTION, rank_match.group(1)

    direct_rank_match = re.search(r"^(?:(?:option|rank|channel)\s*)?#?([1-9]\d*)$", cleaned, re.IGNORECASE)
    if direct_rank_match:
        return BookingActionType.SELECT_OPTION, direct_rank_match.group(1)

    if cleaned.startswith("opt_"):
        return BookingActionType.SELECT_OPTION, cleaned

    # If user explicitly wrote 'book <destination>' or 'select <destination>'
    dest_match = re.search(r"^\s*(?:select|choose|book)\s+([a-z0-9\s\(\)]+)\s*$", cleaned, re.IGNORECASE)
    if dest_match:
        target = dest_match.group(1).strip()
        if target not in ("booking", "this", "now", "it"):
            return BookingActionType.SELECT_OPTION, target

    return BookingActionType.NONE, None


def create_booking_proposal(
    session_id: str,
    farmer_id: str,
    selected_option: NetReturnBreakdown,
    crop: str,
    quantity_quintals: float,
    origin_location: str,
    transit_date: str,
    validity_minutes: int = 15,
    allow_past_date: bool = False,
    base_date: datetime | None = None,
) -> BookingProposal:
    """
    Construct a time-bounded pending booking proposal awaiting explicit farmer confirmation.
    Default validity is 15 minutes to account for market price and transport availability volatility.

    Enforces:
    - Rejects pickup dates in the past (must be valid future date >= today in IST).
    - Reconciles financial calculations: gross value minus total deductions equals net payout.
    """
    ist = timezone(timedelta(hours=5, minutes=30))
    ref_dt = base_date.astimezone(ist) if (base_date and base_date.tzinfo) else (base_date or datetime.now(ist))
    today_str = ref_dt.strftime("%Y-%m-%d")

    # 1. Date Validation: Reject pickup dates in the past
    if not allow_past_date and transit_date < today_str:
        raise ValueError(
            f"Cannot generate booking proposal with pickup date in the past: '{transit_date}' is before '{today_str}'. "
            "Pickup must be scheduled on a valid future date."
        )

    # 2. Financial Reconciliation Validation
    total_deductions = round(
        selected_option.transport_cost
        + selected_option.applicable_market_charges
        + selected_option.storage_cost
        + selected_option.estimated_loss_cost,
        2,
    )
    expected_net = round(selected_option.gross_value - total_deductions, 2)
    if abs(expected_net - selected_option.expected_net_return) > 0.05:
        raise ValueError(
            f"Financial reconciliation mismatch: Gross (INR {selected_option.gross_value:,.2f}) "
            f"- Total Deductions (INR {total_deductions:,.2f}) = INR {expected_net:,.2f}, "
            f"does not match expected net return (INR {selected_option.expected_net_return:,.2f})."
        )

    now = datetime.now(timezone.utc)
    expires = now + timedelta(minutes=validity_minutes)
    date_tag = now.strftime("%Y%m%d")
    unique_suffix = uuid4().hex[:6].upper()
    proposal_id = f"PROP-{date_tag}-{unique_suffix}"

    return BookingProposal(
        proposal_id=proposal_id,
        session_id=session_id,
        farmer_id=farmer_id,
        selected_option=selected_option,
        crop=crop.title(),
        quantity_quintals=quantity_quintals,
        origin_location=origin_location,
        destination_name=selected_option.destination_name,
        channel_type=selected_option.channel_type,
        transit_date=transit_date,
        estimated_gross_value=selected_option.gross_value,
        itemized_deductions=selected_option.total_deductions,
        estimated_net_payout=selected_option.expected_net_return,
        created_at_utc=now.isoformat(),
        expires_at_utc=expires.isoformat(),
        is_consumed=False,
    )


def format_proposal_summary(proposal: BookingProposal) -> list[str]:
    """
    Format a transparent, detailed summary of a proposed booking for the farmer.
    Itemizes all charges, distinguishes assumptions, and asks for unambiguous approval.
    """
    lines: list[str] = []
    lines.append("📋 **KrishiBazaar: Booking Proposal & Confirmation Request (SIMULATED — NOT A REAL BOOKING)** 📋")
    lines.append(f"*Proposal ID:* `{proposal.proposal_id}` *(Valid for 15 minutes)*")
    lines.append("⚠️ **SIMULATED — NOT A REAL BOOKING:** Advisory negotiation estimate only. No real commercial buyer contract, physical crop sale, carrier dispatch, or payment transfer has occurred.")
    lines.append("")

    is_mandi = proposal.channel_type == "MANDI"
    channel_desc = (
        "APMC Wholesale Mandi (Official Benchmark Modal Price — no buyer contract exists)"
        if is_mandi
        else "Direct Commercial Buyer (Simulated Bid)"
    )

    lines.append(f"• **Destination Channel:** **{proposal.destination_name}** [{channel_desc}]")
    lines.append(f"• **Produce Details:** {proposal.quantity_quintals:g} quintals of {proposal.crop}")
    lines.append(f"• **Route:** Pickup at {proposal.origin_location} ➔ Delivery at {proposal.destination_name}")
    lines.append(f"• **Scheduled Transit Date:** {proposal.transit_date}")
    lines.append("")

    # Financial Breakdown with Provenance
    top = proposal.selected_option
    lines.append("💰 **Financial Breakdown & Cost Deductions:**")
    lines.append(
        f"• **Estimated Gross Value:** INR {proposal.estimated_gross_value:,.2f} "
        f"({top.quantity_quintals:g} qtl @ INR {top.offered_price_per_quintal:,.2f}/qtl) [{top.price_provenance}]"
    )
    total_deductions = round(
        top.transport_cost + top.applicable_market_charges + top.storage_cost + top.estimated_loss_cost, 2
    )
    lines.append(f"• **Itemized Deductions (Total Estimated Deductions: INR {total_deductions:,.2f}):**")
    lines.append(f"  - Transport Freight: INR {top.transport_cost:,.2f} [{top.transport_provenance}]")
    if getattr(top, "is_apmc_exempt", False) or (top.channel_type == "DIRECT_BUYER" and top.mandi_cess_apmc == 0.0):
        lines.append(
            f"  - APMC Statutory Cess: INR 0.00 [Assumed direct-buyer trade exemption outside APMC yard; unverified assumption, not legally verified]"
        )
        lines.append(
            f"  - Hamali/Labor: INR 0.00 [Assumed direct farmgate/buyer handling without APMC market labor charge; unverified assumption]"
        )
    else:
        lines.append(f"  - APMC Statutory Cess: INR {top.mandi_cess_apmc:,.2f} [Assumed statutory tariff @ 1.5%]")
        lines.append(f"  - Hamali/Labor: INR {top.loading_unloading_cost:,.2f} [Assumed standard labor charge @ INR 25/qtl]")
    if top.storage_cost > 0:
        lines.append(f"  - Storage: INR {top.storage_cost:,.2f} [Assumed warehouse holding charge]")
    weather_expl = getattr(top, "weather_loss_note", "") or "Estimated transit risk deduction"
    lines.append(f"  - Weather Risk Spoilage: INR {top.estimated_loss_cost:,.2f} [{weather_expl}]")
    lines.append("")

    lines.append(
        f"• **Estimated Net In-Pocket Return:** **INR {proposal.estimated_net_payout:,.2f}** "
        f"(INR {top.net_return_per_quintal:,.2f}/qtl) *(Estimated return derived from stated assumptions; NOT a guaranteed payout or confirmed sale price)*"
    )
    lines.append(
        f"• **Deductions Reconciliation:** Gross Value (INR {proposal.estimated_gross_value:,.2f}) "
        f"- Total Deductions (INR {total_deductions:,.2f}) = Net Return (INR {proposal.estimated_net_payout:,.2f})"
    )
    lines.append(
        f"• **Input Provenance & Deductions Breakdown:** {top.charges_provenance}. "
        f"{'Note: This option is based on an official mandi modal-price benchmark from AGMARKNET. No confirmed buyer offer exists; APMC prices depend on daily physical auction bidding.' if is_mandi else 'Note: Direct buyer bid is a simulated benchmark offer.'}"
    )
    lines.append("")

    # Explicit Approval Gate
    lines.append("🔒 **Explicit Approval Required:**")
    lines.append(
        f"To confirm this simulated booking, reply with:\n"
        f"👉 *\"Confirm booking\"* or *\"Approve proposal {proposal.proposal_id}\"*"
    )
    lines.append("To cancel or change details, reply with *\"Cancel\"*.")
    lines.append("*(Safety Notice: This will create a simulated test booking in our local ledger. No real payment or carrier dispatch will occur.)*")

    return lines


def execute_simulated_booking(proposal: BookingProposal) -> BookingRecord:
    """
    Generate an immutable persistent BookingRecord from an explicitly approved BookingProposal.
    Strictly flags is_simulated_booking = True.
    Enforces that Gross Revenue - Total Deductions (Transport + Other) equals Net Payout.
    """
    now = datetime.now(timezone.utc)
    date_tag = now.strftime("%Y%m%d")
    unique_suffix = uuid4().hex[:6].upper()
    booking_id = f"KB-BOOK-{date_tag}-{unique_suffix}"
    tracking_token = f"KB-TRK-{uuid4().hex[:12].upper()}"

    top = proposal.selected_option
    total_deductions = round(
        top.transport_cost + top.applicable_market_charges + top.storage_cost + top.estimated_loss_cost, 2
    )
    other_deductions = round(
        top.applicable_market_charges + top.storage_cost + top.estimated_loss_cost, 2
    )

    # Reconciliation integrity validation
    expected_net = round(proposal.estimated_gross_value - total_deductions, 2)
    if abs(expected_net - proposal.estimated_net_payout) > 0.05:
        raise ValueError(
            f"Financial reconciliation mismatch in proposal {proposal.proposal_id}: "
            f"Gross ({proposal.estimated_gross_value}) - Total Deductions ({total_deductions}) = {expected_net}, "
            f"does not match estimated net payout ({proposal.estimated_net_payout})."
        )

    return BookingRecord(
        booking_id=booking_id,
        timestamp_utc=now.isoformat(),
        status="CONFIRMED",
        farmer_id=proposal.farmer_id,
        offer_id=top.scenario_id,
        quote_id=f"QUOTE-{top.destination_name}",
        channel_name=proposal.destination_name,
        transporter_name=top.transport_provenance,
        crop=proposal.crop,
        quantity_quintals=proposal.quantity_quintals,
        gross_revenue=proposal.estimated_gross_value,
        transport_cost=top.transport_cost,
        other_deductions=round(other_deductions, 2),
        net_payout=proposal.estimated_net_payout,
        pickup_date=proposal.transit_date,
        tracking_token=tracking_token,
        is_simulated_booking=True,
        notes=(
            f"Simulated test booking created from proposal {proposal.proposal_id}. "
            f"Channel: {proposal.destination_name} ({proposal.channel_type}). "
            f"Advisory estimate only; not a commercial contract."
        ),
    )


def format_booking_confirmation(
    booking: BookingRecord,
    payment_enabled: bool | None = None,
    payee_wallet_address: str | None = None,
    advisory_fee_base_units: int | None = None,
    currency: str | None = None,
    network: str | None = None,
    estimated_fee: str | None = None,
) -> list[str]:
    """
    Format a verifiable simulated booking confirmation with booking ID, tracking token,
    transparency disclaimers, and post-booking payment step.
    Reconciles gross value minus total deductions (transport + statutory/other) equals net payout.
    Includes explicit payment status and clearly separates crop proceeds from testnet micro-fee.
    """
    from krishibazaar.config import settings

    total_deductions = round(booking.transport_cost + booking.other_deductions, 2)
    lines: list[str] = []
    lines.append("🎉 **Simulated Booking Confirmed! — SIMULATED — NOT A REAL BOOKING** 🎉")
    lines.append("⚠️ **SIMULATED — NOT A REAL BOOKING:** This record is a simulated MVP negotiation demonstration stored in your local ledger. No real commercial buyer contract, physical crop sale, carrier dispatch, or payment transfer has occurred.")
    lines.append("")
    lines.append(f"• **Booking Reference ID:** `{booking.booking_id}`")
    lines.append(f"• **Verification Token:** `{booking.tracking_token}`")
    lines.append(f"• **Status:** **{booking.status} (SIMULATED — NOT A REAL BOOKING)**")
    lines.append(f"• **Channel / Destination:** **{booking.channel_name}**")
    lines.append(f"• **Produce Booked:** {booking.quantity_quintals:g} quintals of {booking.crop}")
    lines.append(f"• **Scheduled Pickup Date:** {booking.pickup_date}")
    lines.append(
        f"• **Estimated Net Payout:** **INR {booking.net_payout:,.2f}** "
        f"*(Advisory estimate only; NOT a guaranteed payout or confirmed sale price)*"
    )
    lines.append(
        f"• **Financial Summary & Deductions Reconciliation:**\n"
        f"  - Gross Produce Value: INR {booking.gross_revenue:,.2f}\n"
        f"  - Total Deductions: INR {total_deductions:,.2f} "
        f"(Freight Transport: INR {booking.transport_cost:,.2f} + Statutory/Labor/Risk: INR {booking.other_deductions:,.2f})\n"
        f"  - Net Return (Gross - Total Deductions): INR {booking.net_payout:,.2f}"
    )
    lines.append("")
    lines.append(
        "ℹ️ **Simulation Notice:** All bookings in KrishiBazaar Phase 6 are simulated MVP demonstrations "
        "stored in your local SQLite ledger. No real commercial buyer contract, carrier dispatch, payment transfer, "
        "or physical transaction has occurred."
    )
    lines.append(
        f"👉 **Status Lookup:** To verify status anytime, reply with:\n"
        f"*\"Status of {booking.booking_id}\"* or *\"Track {booking.booking_id}\"*"
    )

    # Post-booking payment step
    is_pay_enabled = settings.enable_payment_protocol if payment_enabled is None else payment_enabled

    if not is_pay_enabled:
        lines.append("")
        lines.append("💳 **Payment Status:**")
        lines.append("• **Payment Status:** Payment is currently disabled. No transfer has been initiated.")
        lines.append(f"• **Booking Reference:** `{booking.booking_id}`")
        lines.append("*(Fetch.ai Agent Payment Protocol settlement remains deactivated by default for safety)*")
    else:
        from krishibazaar.services.payment_service import (
            format_token_amount_display,
            mask_address,
        )

        payee_wallet = (
            payee_wallet_address
            or settings.payment_payee_wallet_address
            or "fetch1e9748raqwnvqyjvsjzwpcs7crd8asp383dshck"
        )
        masked_payee = mask_address(payee_wallet)
        fee_base = (
            advisory_fee_base_units
            if advisory_fee_base_units is not None
            else settings.payment_advisory_fee_base_units
        )
        denom = currency or settings.payment_testnet_denom
        net = (
            network
            or f"Fetch.ai {settings.payment_testnet_chain_id.title()} Testnet ({settings.payment_testnet_chain_id})"
        )
        fee = estimated_fee or "~0.0004 atestfet (Cosmos standard gas fee)"
        amount_display = format_token_amount_display(fee_base, denom)

        lines.append("")
        lines.append("💳 **Payment Option (Optional Testnet Advisory Payment):**")
        lines.append(f"• **Booking Reference:** `{booking.booking_id}`")
        lines.append("• **Payment Status:** READY FOR OPTIONAL TEST PAYMENT")
        lines.append("• **Service Purpose:** KrishiBazaar Platform Advisory Service Fee (Optional Testnet Verification)")
        lines.append(f"• **Amount & Currency:** **{amount_display}**")
        lines.append(f"• **Recipient Address:** `{masked_payee}`")
        lines.append(f"• **Network / Environment:** {net}")
        lines.append(f"• **Applicable Estimated Network Fee:** {fee}")
        lines.append("")
        lines.append(
            "👉 **Pay / Test Payment Action:** Reply *\"Pay advisory fee\"* or click **\"Proceed to Payment Review\"** to review payment terms before authorizing."
        )
        lines.append(
            f"*(Note: Estimated produce sale proceeds of INR {booking.net_payout:,.2f} represent simulated mandi crop sales, "
            f"not funds already received by the farmer. The optional test payment has an exact amount of {amount_display} for ledger verification.)*"
        )

    return lines


def format_booking_status(booking: BookingRecord) -> list[str]:
    """
    Format status verification for an existing simulated booking record with full deductions reconciliation.
    """
    total_deductions = round(booking.transport_cost + booking.other_deductions, 2)
    lines: list[str] = []
    lines.append("📦 **KrishiBazaar: Simulated Booking Status** 📦")
    lines.append("")
    lines.append(f"• **Booking Reference ID:** `{booking.booking_id}`")
    lines.append(f"• **Verification Token:** `{booking.tracking_token}`")
    lines.append(f"• **Current Status:** **{booking.status}** (Simulated MVP Record)")
    lines.append(f"• **Destination Channel:** {booking.channel_name}")
    lines.append(f"• **Produce:** {booking.quantity_quintals:g} quintals of {booking.crop}")
    lines.append(f"• **Scheduled Date:** {booking.pickup_date}")
    lines.append(
        f"• **Financial Summary:** Gross Value: INR {booking.gross_revenue:,.2f} | "
        f"Total Deductions: INR {total_deductions:,.2f} (Transport: INR {booking.transport_cost:,.2f} + Other: INR {booking.other_deductions:,.2f}) | "
        f"Net Payout: INR {booking.net_payout:,.2f} *(Advisory estimate only; NOT a guaranteed payout or confirmed sale price)*"
    )
    lines.append(f"• **Ledger Timestamp:** {booking.timestamp_utc}")
    lines.append("")
    lines.append(
        "ℹ️ **Audit Notice:** This record exists in the local SQLite ledger for MVP audit trail testing. "
        "No real carrier, buyer, or financial transaction has occurred."
    )

    return lines
