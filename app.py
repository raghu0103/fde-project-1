"""Meridian Retail support-draft review tool.

Builds a draft reply for one support case so a human can review it. The tool
only reads local files and prints. It never sends a message, approves a refund,
or changes a record.

Usage:
    python app.py --case C1
    python app.py --case C1 --offline-demo
    python app.py --case C1 --simulate-timeout
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Callable

import anthropic
from dotenv import dotenv_values

BASE_DIR = Path(__file__).resolve().parent
CASES_PATH = BASE_DIR / "cases.json"
POLICY_PATH = BASE_DIR / "policy.md"
DEMO_PATH = BASE_DIR / "demo_outputs.json"
ENV_PATH = BASE_DIR / ".env"

CASE_IDS = ("C1", "C2", "C3")
DEFAULT_MODEL = "claude-haiku-4-5-20251001"
REQUEST_TIMEOUT_SECONDS = 20.0

REVIEW_STATUSES = ("READY_FOR_HUMAN_REVIEW", "NEEDS_INFORMATION", "BLOCKED")
UNAVAILABLE = "UNAVAILABLE"
RESULT_FIELDS = ("draft_reply", "evidence_refs", "missing_information", "review_status")

MANUAL_FALLBACK = (
    "MANUAL FALLBACK: No draft is available for this case. Handle it manually "
    "using policy.md. Nothing was sent and no record was changed."
)
NO_API_LINE = "OFFLINE DEMO: no model API was called."
DEMO_LABEL_LINE = "The result below is a prerecorded synthetic example, not a live model output."
FOOTER = (
    "This is a draft for human review only. It has not been sent, no refund has "
    "been approved, and no record has been changed.\n"
    "Passing the structure checks does not mean the content is correct: check the "
    "draft against the supplied facts and policy before using any of it."
)

POLICY_ID_PATTERN = re.compile(r"^(P\d+)\.", re.MULTILINE)

ModelFn = Callable[[str, str], str]


class InvalidModelOutput(ValueError):
    """The model output is not the required JSON shape."""


class ModelUnavailable(RuntimeError):
    """The model could not be called (for example, no key is configured)."""


# --- Input files -------------------------------------------------------------


def load_case_file(path: Path = CASES_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_case(case_id: str, path: Path = CASES_PATH) -> dict:
    cases = load_case_file(path)
    if case_id not in cases:
        raise KeyError(f"Unknown case {case_id!r}")
    return cases[case_id]


def load_policy(path: Path = POLICY_PATH) -> str:
    return path.read_text(encoding="utf-8")


def policy_ids(policy_text: str) -> set[str]:
    return set(POLICY_ID_PATTERN.findall(policy_text))


# --- Prompt and model call ---------------------------------------------------


def build_prompt(case: dict, policy_text: str, fact_fields: list[str]) -> tuple[str, str]:
    """Return (system, user) prompt text for one case."""
    system = (
        "You draft customer-support replies for Meridian Retail. A human support "
        "agent reviews every draft before anything is sent. You cannot send "
        "messages, approve refunds, or change records, and your draft must not "
        "say or imply that any of those things has happened.\n\n"
        "Use only the case facts and the policy supplied in the user message. Do "
        "not invent order details, dates, amounts, timelines, or policy. A case "
        "field whose value is null is missing. If a fact the policy needs is "
        "missing, list it instead of assuming it.\n\n"
        "Return only one JSON object. No prose, no markdown, no code fences. It "
        "must have exactly these keys:\n"
        '- "draft_reply": string. The reply to the customer for a human to review. '
        'Use "" when review_status is BLOCKED.\n'
        '- "evidence_refs": list of policy clause IDs (for example "P1") '
        "that the draft and status rely on. Only use IDs that appear in the policy.\n"
        '- "missing_information": list of field names from the allowed list that '
        "are needed but missing from the case facts. Use [] if nothing is missing.\n"
        '- "review_status": one of "READY_FOR_HUMAN_REVIEW", "NEEDS_INFORMATION", '
        '"BLOCKED".\n\n'
        "Status rules: BLOCKED when a policy clause says the case must not get a "
        "substantive reply. Otherwise NEEDS_INFORMATION when required facts are "
        "missing (the draft asks the customer for them). Otherwise "
        "READY_FOR_HUMAN_REVIEW."
    )
    user = (
        "<policy>\n"
        f"{policy_text.strip()}\n"
        "</policy>\n\n"
        "<case_facts>\n"
        f"{json.dumps(case, indent=2)}\n"
        "</case_facts>\n\n"
        "Allowed field names for missing_information: "
        f"{', '.join(fact_fields)}\n\n"
        "Return the JSON object now."
    )
    return system, user


def load_settings(env_path: Path = ENV_PATH) -> tuple[str, str]:
    """Return (api_key, model) from APP_* variables only.

    The process environment wins over the project .env file. The .env file is
    read without being copied into os.environ, and ANTHROPIC_API_KEY is never
    read or set.
    """
    file_values = dotenv_values(env_path) if env_path.exists() else {}
    api_key = os.environ.get("APP_ANTHROPIC_API_KEY") or file_values.get("APP_ANTHROPIC_API_KEY") or ""
    model = os.environ.get("APP_ANTHROPIC_MODEL") or file_values.get("APP_ANTHROPIC_MODEL") or DEFAULT_MODEL
    return api_key.strip(), model.strip()


def call_model(system: str, user: str) -> str:
    """Call the Anthropic API once and return the response text."""
    api_key, model = load_settings()
    if not api_key:
        raise ModelUnavailable("APP_ANTHROPIC_API_KEY is not set")

    client = anthropic.Anthropic(
        api_key=api_key,
        timeout=REQUEST_TIMEOUT_SECONDS,
        max_retries=0,
    )
    response = client.messages.create(
        model=model,
        max_tokens=1024,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    if response.stop_reason != "end_turn":
        raise InvalidModelOutput(f"Unexpected stop_reason: {response.stop_reason}")
    return "".join(block.text for block in response.content if block.type == "text")


# --- Validation --------------------------------------------------------------


def parse_json_object(text: str) -> dict:
    """Parse the model text as one JSON object, allowing one ```json fence."""
    stripped = text.strip()
    fence = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", stripped, re.DOTALL)
    if fence:
        stripped = fence.group(1)
    try:
        obj = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise InvalidModelOutput(f"Not valid JSON: {exc}") from exc
    if not isinstance(obj, dict):
        raise InvalidModelOutput("Top-level JSON value is not an object")
    return obj


def _is_str_list(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def validate_result(obj: dict, known_policy_ids: set[str], fact_fields: list[str]) -> dict:
    """Check the shape and basic consistency of a result.

    This proves the structure only. It does not prove the draft is accurate.
    """
    if set(obj) != set(RESULT_FIELDS):
        missing = sorted(set(RESULT_FIELDS) - set(obj))
        extra = sorted(set(obj) - set(RESULT_FIELDS))
        raise InvalidModelOutput(f"Wrong keys (missing={missing}, extra={extra})")

    draft = obj["draft_reply"]
    refs = obj["evidence_refs"]
    missing_info = obj["missing_information"]
    status = obj["review_status"]

    if not isinstance(draft, str):
        raise InvalidModelOutput("draft_reply must be a string")
    if not _is_str_list(refs):
        raise InvalidModelOutput("evidence_refs must be a list of strings")
    if not _is_str_list(missing_info):
        raise InvalidModelOutput("missing_information must be a list of strings")
    if status not in REVIEW_STATUSES:
        raise InvalidModelOutput(f"review_status must be one of {REVIEW_STATUSES}")

    unknown_refs = sorted(set(refs) - known_policy_ids)
    if unknown_refs:
        raise InvalidModelOutput(f"evidence_refs not in policy.md: {unknown_refs}")
    unknown_fields = sorted(set(missing_info) - set(fact_fields))
    if unknown_fields:
        raise InvalidModelOutput(f"missing_information has unknown fields: {unknown_fields}")

    if status == "READY_FOR_HUMAN_REVIEW" and (not draft.strip() or missing_info):
        raise InvalidModelOutput("READY_FOR_HUMAN_REVIEW needs a draft and no missing information")
    if status == "NEEDS_INFORMATION" and not missing_info:
        raise InvalidModelOutput("NEEDS_INFORMATION needs at least one missing field")

    return {field: obj[field] for field in RESULT_FIELDS}


def unavailable_result() -> dict:
    return {
        "draft_reply": "",
        "evidence_refs": [],
        "missing_information": [],
        "review_status": UNAVAILABLE,
    }


# --- Running a case ----------------------------------------------------------


def load_demo_output(case_id: str, path: Path = DEMO_PATH) -> tuple[str, dict]:
    """Return (label, result) for the prerecorded synthetic demo output."""
    with open(path, encoding="utf-8") as f:
        entry = json.load(f)["outputs"][case_id]
    return entry["label"], entry["result"]


def run_case(
    case_id: str,
    model_fn: ModelFn = call_model,
    simulate_timeout: bool = False,
    offline_demo: bool = False,
) -> dict:
    """Produce the result for one case.

    Returns a dict with "result" (the validated result or the unavailable
    state), "source" ("model", "offline_demo", or "unavailable"), and "notes"
    (lines to show the reviewer).
    """
    case = load_case(case_id)
    policy_text = load_policy()
    known_ids = policy_ids(policy_text)
    fact_fields = list(case)

    if simulate_timeout:
        return {
            "result": unavailable_result(),
            "source": "unavailable",
            "notes": ["Simulated timeout: no model API was called.", MANUAL_FALLBACK],
        }

    if offline_demo:
        try:
            label, demo = load_demo_output(case_id)
            result = validate_result(demo, known_ids, fact_fields)
        except (OSError, KeyError, ValueError) as exc:
            return {
                "result": unavailable_result(),
                "source": "unavailable",
                "notes": [NO_API_LINE, f"Demo output unusable ({exc}).", MANUAL_FALLBACK],
            }
        return {
            "result": result,
            "source": "offline_demo",
            "notes": [NO_API_LINE, DEMO_LABEL_LINE, f"Demo label: {label}"],
        }

    system, user = build_prompt(case, policy_text, fact_fields)
    try:
        text = model_fn(system, user)
        result = validate_result(parse_json_object(text), known_ids, fact_fields)
    except Exception as exc:  # Any provider or output failure ends in the same safe state.
        return {
            "result": unavailable_result(),
            "source": "unavailable",
            "notes": [f"Model draft unavailable ({type(exc).__name__}).", MANUAL_FALLBACK],
        }
    return {"result": result, "source": "model", "notes": []}


def _section(title: str) -> str:
    return f"\n{'=' * 72}\n{title}\n{'=' * 72}"


def render(case_id: str, case: dict, policy_text: str, outcome: dict) -> str:
    result = outcome["result"]
    lines = [_section(f"SUPPLIED FACTS (case {case_id}, from cases.json)")]
    for key, value in case.items():
        lines.append(f"{key}: {'null (missing)' if value is None else value}")

    lines.append(_section("SUPPLIED POLICY (from policy.md)"))
    lines.append(policy_text.strip())

    lines.append(_section("DRAFT RESULT FOR HUMAN REVIEW"))
    lines.extend(outcome["notes"])
    lines.append(f"source: {outcome['source']}")
    lines.append(f"review_status: {result['review_status']}")
    lines.append(f"evidence_refs: {', '.join(result['evidence_refs']) or '(none)'}")
    lines.append(f"missing_information: {', '.join(result['missing_information']) or '(none)'}")
    lines.append("draft_reply:")
    lines.append(result["draft_reply"] or "(empty)")
    lines.append("-" * 72)
    lines.append(FOOTER)
    return "\n".join(lines)


def main(argv: list[str] | None = None, model_fn: ModelFn = call_model) -> int:
    parser = argparse.ArgumentParser(description="Draft a support reply for human review.")
    parser.add_argument("--case", required=True, choices=CASE_IDS, help="Case ID from cases.json")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--simulate-timeout", action="store_true", help="Show the unavailable state without calling the API")
    mode.add_argument("--offline-demo", action="store_true", help="Show a prerecorded synthetic result without calling the API")
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    outcome = run_case(
        args.case,
        model_fn=model_fn,
        simulate_timeout=args.simulate_timeout,
        offline_demo=args.offline_demo,
    )
    print(render(args.case, load_case(args.case), load_policy(), outcome))
    return 0


if __name__ == "__main__":
    sys.exit(main())
