"""
Smoke-test utility for ASI:One Structured Intent Extraction.
Usage:
    python smoke_test_asi1.py
    python smoke_test_asi1.py "I have 40 quintals of tomatoes in Pune, need Rs 60000 net"
"""

import json
import sys
from krishibazaar.config import settings
from krishibazaar.services.extraction import extract_farmer_intent


def run_smoke_test(input_text: str | None = None) -> None:
    test_prompt = (
        input_text
        if input_text
        else "I have 50 quintals of onions in Nashik, and I want at least INR 85000 net by tomorrow."
    )

    print("=" * 70)
    print("[*] KrishiBazaar - ASI:One Intent Extraction Smoke Test")
    print("=" * 70)

    print(f"[*] Base URL: {settings.asi_one_base_url}")
    print(f"[*] Target Model: {settings.asi_one_model}")

    api_key_status = (
        f"Present ({settings.asi_one_api_key[:6]}...)"
        if settings.asi_one_api_key
        else "Not set in .env (will use deterministic rule-based fallback)"
    )
    print(f"[*] ASI_ONE_API_KEY: {api_key_status}")
    print(f"[*] Farmer Input: \"{test_prompt}\"")
    print("-" * 70)

    intent = extract_farmer_intent(test_prompt)

    print("[*] Extraction Result:")
    result_dict = {
        "crop": intent.crop,
        "quantity_quintals": intent.quantity_quintals,
        "origin_location": intent.origin_location,
        "selling_deadline": intent.selling_deadline,
        "minimum_net_return_inr": intent.minimum_net_return_inr,
        "storage_preference": intent.storage_preference,
        "is_complete": intent.is_complete,
        "missing_fields": intent.missing_fields,
        "follow_up_question": intent.follow_up_question,
        "extraction_source": intent.extraction_source,
    }
    print(json.dumps(result_dict, indent=2))
    print("-" * 70)

    if intent.is_complete:
        print("[SUCCESS] All required intent fields successfully extracted and validated!")
    else:
        print(f"[ACTION REQUIRED] Incomplete inquiry. Follow-up question generated:\n\"{intent.follow_up_question}\"")

    print("=" * 70)


if __name__ == "__main__":
    cli_arg = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else None
    run_smoke_test(cli_arg)
