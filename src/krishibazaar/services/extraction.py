"""
Structured Intent Extraction Service using the ASI:One Developer API.
Converts a farmer's natural-language inquiry into validated Pydantic models.
Gracefully handles missing credentials, network timeouts, malformed JSON, and rate limits.
"""

from __future__ import annotations
import asyncio
import json
import logging
import re
from typing import Any
import httpx

from krishibazaar.config import settings
from krishibazaar.models import FarmerIntent

logger = logging.getLogger("krishibazaar.extraction")

# Prompt instructing ASI:One model to extract strictly without fabricating data
EXTRACTION_SYSTEM_PROMPT = (
    "You are the KrishiBazaar Agricultural Intent Extraction Engine for Indian farmers.\n"
    "Your goal is to parse the farmer's natural language into structured JSON data.\n\n"
    "Extract ONLY the following fields:\n"
    "- crop: string (the name of the crop/produce in lowercase, e.g., 'onion', 'wheat', 'tomato', 'potato', 'soybean', 'mustard', 'cotton', 'rice'). Set to null if not mentioned.\n"
    "- quantity_quintals: float (the quantity in quintals. 1 quintal = 100 kg, 1 metric ton = 10 quintals). Set to null if not mentioned.\n"
    "- origin_location: string (the farmer's village, town, or district in Title Case, e.g., 'Nashik', 'Khanna', 'Indore'). Set to null if not mentioned.\n"
    "- selling_deadline: string (the preferred selling timeframe, e.g., 'tomorrow', 'by Friday', 'in 3 days'). Set to null if not mentioned.\n"
    "- minimum_net_return_inr: float (the farmer's minimum expected net earnings in Indian Rupees, e.g., 85000.0). Set to null if not mentioned.\n"
    "- storage_preference: string (e.g., 'cold storage', 'dry shed', 'warehouse') or null if not mentioned.\n\n"
    "CRITICAL RULES:\n"
    "1. NEVER invent, fabricate, or guess any value. If a field is not explicitly stated or directly implied, return null.\n"
    "2. NEVER approve bookings or fabricate market prices.\n"
    "3. Return ONLY a single valid JSON object. Do not include introductory text, explanations, or markdown fences."
)

KNOWN_CROPS = {
    "onion": "onion",
    "onions": "onion",
    "pyaz": "onion",
    "pyaaz": "onion",
    "kanda": "onion",
    "प्याज": "onion",
    "प्याज़": "onion",
    "कांदा": "onion",
    "tomato": "tomato",
    "tomatoes": "tomato",
    "tamatar": "tomato",
    "टमाटर": "tomato",
    "potato": "potato",
    "potatoes": "potato",
    "aloo": "potato",
    "batata": "potato",
    "आलू": "potato",
    "बटाटा": "potato",
    "wheat": "wheat",
    "gehu": "wheat",
    "गेहूं": "wheat",
    "गेहू": "wheat",
    "soybean": "soybean",
    "soya": "soybean",
    "सोयाबीन": "soybean",
    "सोया": "soybean",
    "mustard": "mustard",
    "sarson": "mustard",
    "सरसों": "mustard",
    "cotton": "cotton",
    "kapas": "cotton",
    "कपास": "cotton",
    "rice": "rice",
    "paddy": "rice",
    "dhaan": "rice",
    "चावल": "rice",
    "धान": "rice",
    "maize": "maize",
    "makka": "maize",
    "corn": "maize",
    "मक्का": "maize",
    "gram": "gram",
    "chana": "gram",
    "चना": "gram",
    "garlic": "garlic",
    "lahsun": "garlic",
    "लहसुन": "garlic",
}


def _extract_via_rules(text: str, source: str = "rule_based_fallback") -> FarmerIntent:
    """
    Deterministic rule-based heuristic extractor used as an offline or fallback engine.
    Ensures zero crashes when ASI:One credentials are absent or remote API is unreachable.
    Supports English, Hinglish, Devanagari Hindi, and Rupee (₹) symbols.
    """
    text_lower = text.lower()

    # 1. Detect crop (supports English and Devanagari aliases)
    detected_crop: str | None = None
    for alias, canon in KNOWN_CROPS.items():
        if alias in text_lower or re.search(rf"\b{re.escape(alias)}\b", text_lower):
            detected_crop = canon
            break

    # 2. Detect quantity in quintals
    quantity: float | None = None
    # Check for quintals / qtl / क्विंटल
    qtl_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:quintals?|qtl|qtls?|क्विंटल|क्विण्टल)\b", text_lower)
    if qtl_match:
        quantity = float(qtl_match.group(1))
    else:
        # Check for tons / tonnes (1 ton = 10 quintals)
        ton_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:tons?|tonnes?|metric tons?|टन)\b", text_lower)
        if ton_match:
            quantity = float(ton_match.group(1)) * 10.0
        else:
            # Check for kg (100 kg = 1 quintal)
            kg_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:kg|kilograms?|किलो|किलोग्राम)\b", text_lower)
            if kg_match:
                quantity = float(kg_match.group(1)) / 100.0

    # 3. Detect origin location
    origin: str | None = None
    loc_match = re.search(
        r"(?:in|at|near|from)\s+([A-Za-z]+(?:\s+[A-Za-z]+)?)(?:,|\.|\s+and|\s+by|\s+with|\s*$)",
        text,
    )
    if loc_match:
        candidate = loc_match.group(1).strip()
        # Avoid false positives like "in 2 days" or "in bulk"
        if not re.search(r"\b(days?|hours?|bulk|advance|cash|transit)\b", candidate.lower()):
            origin = candidate.title()
    if not origin:
        # Direct location check fallback (supports English and Devanagari Hindi)
        HINDI_DISTRICTS = {
            "नासिक": "Nashik",
            "पुणे": "Pune",
            "इंदौर": "Indore",
            "इन्दौर": "Indore",
            "खन्ना": "Khanna",
            "सोलापुर": "Solapur",
            "लातूर": "Latur",
            "राजकोट": "Rajkot",
            "आगरा": "Agra",
            "दिल्ली": "Delhi",
        }
        for h_loc, en_loc in HINDI_DISTRICTS.items():
            if h_loc in text:
                origin = en_loc
                break

    if not origin:
        from krishibazaar.services.weather_service import LOCATION_COORDINATES
        for loc_key in LOCATION_COORDINATES:
            if loc_key in text_lower:
                origin = loc_key.title()
                break

    # 4. Detect minimum net return in INR / ₹
    min_return: float | None = None
    money_match = re.search(
        r"(?:₹|inr|rs\.?|rupees?|रुपये|रुपए|at least|minimum)\s*(\d+(?:,\d+)*(?:\.\d+)?)\s*(?:net|inr|rs|₹|रुपये)?",
        text_lower,
    )
    if money_match:
        raw_val = money_match.group(1).replace(",", "")
        min_return = float(raw_val)
    else:
        money_match_alt = re.search(
            r"(\d+(?:,\d+)*(?:\.\d+)?)\s*(?:₹|inr|rs\.?|rupees?|रुपये|रुपए)\s*(?:net)?",
            text_lower,
        )
        if money_match_alt:
            raw_val = money_match_alt.group(1).replace(",", "")
            min_return = float(raw_val)

    # 5. Detect selling deadline
    deadline: str | None = None
    deadline_match = re.search(
        r"\b(?:by|before|within)\s+(tomorrow|today|this weekend|monday|tuesday|wednesday|thursday|friday|saturday|sunday|\d+\s+days?|\d+\s+hours?)\b",
        text_lower,
    )
    if deadline_match:
        deadline = deadline_match.group(1).strip()
    elif "tomorrow" in text_lower:
        deadline = "tomorrow"
    elif "today" in text_lower:
        deadline = "today"

    # 6. Detect storage preference
    storage: str | None = None
    if "cold storage" in text_lower:
        storage = "cold storage"
    elif "dry warehouse" in text_lower or "warehouse" in text_lower:
        storage = "warehouse"
    elif "dry shed" in text_lower or "shed" in text_lower:
        storage = "dry shed"

    intent = FarmerIntent(
        crop=detected_crop,
        quantity_quintals=quantity,
        origin_location=origin,
        selling_deadline=deadline,
        minimum_net_return_inr=min_return,
        storage_preference=storage,
        extraction_source=source,
    )
    return intent.evaluate_completeness()


def _sanitize_and_parse_json(content: str) -> dict[str, Any] | None:
    """Extract and parse JSON safely from model response text."""
    if not content:
        return None
    content = content.strip()

    # If wrapped in markdown code fence (```json ... ```)
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?\s*", "", content, flags=re.IGNORECASE)
        content = re.sub(r"\s*```$", "", content)

    # Attempt direct parse
    try:
        data = json.loads(content)
        if isinstance(data, dict):
            return data
    except Exception:
        pass

    # Attempt substring regex search for {...}
    match = re.search(r"(\{.*\})", content, flags=re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(1))
            if isinstance(data, dict):
                return data
        except Exception:
            pass

    return None


async def extract_farmer_intent_async(
    user_text: str,
    api_key: str | None = None,
    client: httpx.AsyncClient | None = None,
    max_retries: int = 1,
    backoff_seconds: float = 0.5,
) -> FarmerIntent:
    """
    Extract structured farmer intent from natural language using ASI:One API.
    Performs bounded retries with backoff on transient errors (e.g. HTTP 500/502/503/504).
    Falls back gracefully and explicitly to rule-based parser on persistent failure or missing API key.
    """
    effective_key = api_key or settings.asi_one_api_key

    # If no API key configured, use transparent rule-based extraction
    if not effective_key:
        logger.info("ASI_ONE_API_KEY is not configured. Utilizing deterministic rule-based extractor.")
        return _extract_via_rules(user_text)

    endpoint = f"{settings.asi_one_base_url.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {effective_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": settings.asi_one_model,
        "messages": [
            {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
            {"role": "user", "content": user_text},
        ],
        "temperature": 0.0,
    }

    max_attempts = 1 + max(0, max_retries)
    last_status: int | None = None

    for attempt in range(max_attempts):
        try:
            # Use provided client or instantiate a temporary one
            if client:
                resp = await client.post(
                    endpoint,
                    headers=headers,
                    json=payload,
                    timeout=settings.asi_one_timeout_seconds,
                )
            else:
                async with httpx.AsyncClient() as session:
                    resp = await session.post(
                        endpoint,
                        headers=headers,
                        json=payload,
                        timeout=settings.asi_one_timeout_seconds,
                    )

            last_status = resp.status_code

            if resp.status_code == 200:
                result = resp.json()
                message_content = (
                    result.get("choices", [{}])[0].get("message", {}).get("content", "")
                )
                parsed = _sanitize_and_parse_json(message_content)

                if parsed is not None:
                    # Build validated Pydantic model
                    intent = FarmerIntent(
                        crop=parsed.get("crop"),
                        quantity_quintals=parsed.get("quantity_quintals"),
                        origin_location=parsed.get("origin_location"),
                        selling_deadline=parsed.get("selling_deadline"),
                        minimum_net_return_inr=parsed.get("minimum_net_return_inr"),
                        storage_preference=parsed.get("storage_preference"),
                        extraction_source="asi1",
                    )
                    return intent.evaluate_completeness()
                else:
                    logger.warning(
                        f"ASI:One returned non-JSON content. Falling back to rule-based parser: {message_content[:100]}"
                    )
                    return _extract_via_rules(user_text)

            elif resp.status_code in (500, 502, 503, 504):
                if attempt < max_attempts - 1:
                    logger.warning(
                        f"ASI:One API returned HTTP {resp.status_code}. Retrying in {backoff_seconds}s "
                        f"(attempt {attempt + 1}/{max_attempts})..."
                    )
                    await asyncio.sleep(backoff_seconds)
                    continue
                logger.warning(
                    f"ASI:One API failed with HTTP {resp.status_code} after {max_attempts} attempts. "
                    "Falling back to rule-based extractor."
                )
                return _extract_via_rules(
                    user_text,
                    source=f"rule_based_fallback (ASI:One HTTP {resp.status_code})",
                )

            elif resp.status_code == 401:
                logger.error("ASI:One API returned 401 Unauthorized. Verify ASI_ONE_API_KEY.")
                return _extract_via_rules(user_text)

            elif resp.status_code == 429:
                logger.warning("ASI:One API rate limit exceeded (HTTP 429). Falling back to rule-based parser.")
                return _extract_via_rules(user_text)

            else:
                logger.error(f"ASI:One API error HTTP {resp.status_code}: {resp.text[:120]}")
                return _extract_via_rules(
                    user_text,
                    source=f"rule_based_fallback (ASI:One HTTP {resp.status_code})",
                )

        except httpx.TimeoutException:
            if attempt < max_attempts - 1:
                logger.warning(
                    f"ASI:One API timed out after {settings.asi_one_timeout_seconds}s. "
                    f"Retrying in {backoff_seconds}s (attempt {attempt + 1}/{max_attempts})..."
                )
                await asyncio.sleep(backoff_seconds)
                continue
            logger.warning(
                f"ASI:One API timed out after {max_attempts} attempts. Falling back to rule-based parser."
            )
            return _extract_via_rules(user_text)

        except Exception as ex:
            logger.error(f"Unexpected error calling ASI:One API: {type(ex).__name__} - {str(ex)}")
            return _extract_via_rules(user_text)

    # Fallback if loop finishes without returning
    return _extract_via_rules(
        user_text,
        source=f"rule_based_fallback (ASI:One HTTP {last_status})" if last_status else "rule_based_fallback",
    )


def extract_farmer_intent(user_text: str, api_key: str | None = None) -> FarmerIntent:
    """
    Synchronous wrapper for intent extraction.
    Safe for direct call from scripts or non-async contexts.
    """
    effective_key = api_key or settings.asi_one_api_key

    if not effective_key:
        return _extract_via_rules(user_text)

    endpoint = f"{settings.asi_one_base_url.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {effective_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": settings.asi_one_model,
        "messages": [
            {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
            {"role": "user", "content": user_text},
        ],
        "temperature": 0.0,
    }

    try:
        with httpx.Client() as client:
            resp = client.post(
                endpoint,
                headers=headers,
                json=payload,
                timeout=settings.asi_one_timeout_seconds,
            )

        if resp.status_code == 200:
            result = resp.json()
            message_content = (
                result.get("choices", [{}])[0].get("message", {}).get("content", "")
            )
            parsed = _sanitize_and_parse_json(message_content)

            if parsed is not None:
                intent = FarmerIntent(
                    crop=parsed.get("crop"),
                    quantity_quintals=parsed.get("quantity_quintals"),
                    origin_location=parsed.get("origin_location"),
                    selling_deadline=parsed.get("selling_deadline"),
                    minimum_net_return_inr=parsed.get("minimum_net_return_inr"),
                    storage_preference=parsed.get("storage_preference"),
                    extraction_source="asi1",
                )
                return intent.evaluate_completeness()
            else:
                return _extract_via_rules(user_text)
        else:
            return _extract_via_rules(user_text)
    except Exception as ex:
        logger.warning(f"ASI:One sync extraction failed ({type(ex).__name__}). Using fallback.")
        return _extract_via_rules(user_text)
