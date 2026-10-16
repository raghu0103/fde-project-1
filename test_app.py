"""Tests for app.py. None of these call an external API."""

import json
import os
from types import SimpleNamespace

import pytest

import app

C1_GOOD = {
    "draft_reply": "Sorry your jacket zip arrived broken. We can refer this for a human return review.",
    "evidence_refs": ["P1", "P4"],
    "missing_information": [],
    "review_status": "READY_FOR_HUMAN_REVIEW",
}


class ExplodingClient:
    def __init__(self, *args, **kwargs):
        raise AssertionError("A test tried to build a real Anthropic client")


@pytest.fixture(autouse=True)
def no_real_client(monkeypatch, tmp_path):
    """Block real clients and point settings at an empty temp .env."""
    monkeypatch.setattr(app.anthropic, "Anthropic", ExplodingClient)
    monkeypatch.setattr(app, "ENV_PATH", tmp_path / ".env")
    monkeypatch.delenv("APP_ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("APP_ANTHROPIC_MODEL", raising=False)


def fake_model_returning(text):
    calls = []

    def fake(system, user):
        calls.append((system, user))
        return text

    fake.calls = calls
    return fake


def must_not_be_called(system, user):
    raise AssertionError("model_fn should not be called")


def assert_unavailable(outcome):
    assert outcome["source"] == "unavailable"
    assert outcome["result"] == app.unavailable_result()
    assert outcome["result"]["draft_reply"] == ""
    assert outcome["result"]["review_status"] == "UNAVAILABLE"
    assert app.MANUAL_FALLBACK in outcome["notes"]


# --- Input files -------------------------------------------------------------


CASE_FIELDS = {
    "case_id", "order_id", "product", "customer_question",
    "delivery_date", "issue_status", "issue_details",
}


def test_all_cases_load_with_expected_fields():
    for case_id in app.CASE_IDS:
        case = app.load_case(case_id)
        assert set(case) == CASE_FIELDS, case_id
        assert case["case_id"] == case_id
        assert case["issue_status"] == "open"


def test_c2_delivery_date_is_null():
    assert app.load_case("C2")["delivery_date"] is None
    assert app.load_case("C1")["delivery_date"] == "2026-09-12"


def test_case_file_has_exactly_the_cli_cases():
    assert set(app.load_case_file()) == set(app.CASE_IDS)


def test_unknown_case_raises():
    with pytest.raises(KeyError):
        app.load_case("C9")


def test_policy_ids_parsed():
    assert app.policy_ids(app.load_policy()) == {"P1", "P2", "P3", "P4"}


def test_prompt_includes_facts_policy_and_rules():
    case = app.load_case("C1")
    system, user = app.build_prompt(case, app.load_policy(), list(case))
    assert "only the case facts and the policy" in system
    assert "null is missing" in system
    for field in app.RESULT_FIELDS:
        assert field in system
    assert "MR-1042" in user
    assert "P3. If the issue record says open" in user


# --- Successful model result -------------------------------------------------


def test_c1_success_with_fake_model(capsys):
    fake = fake_model_returning(json.dumps(C1_GOOD))
    outcome = app.run_case("C1", model_fn=fake)
    assert outcome["source"] == "model"
    assert outcome["result"] == C1_GOOD
    assert len(fake.calls) == 1

    assert app.main(["--case", "C1"], model_fn=fake) == 0
    out = capsys.readouterr().out
    assert "SUPPLIED FACTS" in out
    assert "SUPPLIED POLICY" in out
    assert "DRAFT RESULT FOR HUMAN REVIEW" in out
    assert "READY_FOR_HUMAN_REVIEW" in out
    assert C1_GOOD["draft_reply"] in out
    assert "does not mean the content is correct" in out


def test_json_code_fence_is_accepted():
    fake = fake_model_returning("```json\n" + json.dumps(C1_GOOD) + "\n```")
    assert app.run_case("C1", model_fn=fake)["result"] == C1_GOOD


# --- Invalid model output ----------------------------------------------------


def _with(**changes):
    obj = dict(C1_GOOD)
    obj.update(changes)
    return obj


def _without(key):
    obj = dict(C1_GOOD)
    del obj[key]
    return obj


INVALID_OUTPUTS = {
    "not json": "Sure! Here is the draft: Hi Sam...",
    "json list": json.dumps([C1_GOOD]),
    "missing key": json.dumps(_without("evidence_refs")),
    "extra key": json.dumps(_with(refund_approved=True)),
    "draft not string": json.dumps(_with(draft_reply=["Hi"])),
    "refs not list": json.dumps(_with(evidence_refs="POL-ID-01")),
    "refs not strings": json.dumps(_with(evidence_refs=[1])),
    "missing info not list": json.dumps(_with(missing_information="order_number")),
    "bad status": json.dumps(_with(review_status="APPROVED")),
    "unknown policy id": json.dumps(_with(evidence_refs=["POL-FAKE-99"])),
    "unknown field": json.dumps(_with(review_status="NEEDS_INFORMATION", missing_information=["shoe_size"])),
    "ready with empty draft": json.dumps(_with(draft_reply="  ")),
    "ready with missing info": json.dumps(_with(missing_information=["delivery_date"])),
    "needs info with nothing missing": json.dumps(_with(review_status="NEEDS_INFORMATION")),
}


@pytest.mark.parametrize("text", INVALID_OUTPUTS.values(), ids=INVALID_OUTPUTS.keys())
def test_invalid_model_output_gives_unavailable(text):
    assert_unavailable(app.run_case("C1", model_fn=fake_model_returning(text)))


def test_invalid_output_does_not_print_partial_draft(capsys):
    bad = json.dumps(_with(review_status="APPROVED", draft_reply="Your refund is approved!"))
    app.main(["--case", "C1"], model_fn=fake_model_returning(bad))
    out = capsys.readouterr().out
    assert "Your refund is approved!" not in out
    assert "UNAVAILABLE" in out
    assert app.MANUAL_FALLBACK in out


# --- Timeout and provider failures -------------------------------------------


def test_simulate_timeout_makes_no_call(capsys):
    outcome = app.run_case("C1", model_fn=must_not_be_called, simulate_timeout=True)
    assert_unavailable(outcome)

    app.main(["--case", "C1", "--simulate-timeout"], model_fn=must_not_be_called)
    out = capsys.readouterr().out
    assert "review_status: UNAVAILABLE" in out
    assert "draft_reply:\n(empty)" in out
    assert app.MANUAL_FALLBACK in out
    assert "Traceback" not in out


def test_provider_timeout_gives_unavailable():
    def timing_out(system, user):
        raise app.anthropic.APITimeoutError(request=None)

    assert_unavailable(app.run_case("C1", model_fn=timing_out))


def test_any_provider_error_gives_unavailable():
    def failing(system, user):
        raise ConnectionError("network down")

    assert_unavailable(app.run_case("C2", model_fn=failing))


def test_missing_key_gives_unavailable_without_client():
    # call_model is the real one; the autouse fixture leaves no key configured
    # and makes building a client fail the test.
    assert_unavailable(app.run_case("C1"))


def test_timeout_and_demo_flags_are_exclusive():
    with pytest.raises(SystemExit):
        app.main(["--case", "C1", "--simulate-timeout", "--offline-demo"], model_fn=must_not_be_called)


def test_bad_case_id_rejected():
    with pytest.raises(SystemExit):
        app.main(["--case", "C4"], model_fn=must_not_be_called)


# --- Client configuration ----------------------------------------------------


def test_call_model_uses_app_key_timeout_and_no_retries(monkeypatch):
    created = {}

    class FakeClient:
        def __init__(self, **kwargs):
            created.update(kwargs)
            self.messages = SimpleNamespace(create=self.create)

        def create(self, **kwargs):
            created["request"] = kwargs
            return SimpleNamespace(
                stop_reason="end_turn",
                content=[SimpleNamespace(type="text", text=json.dumps(C1_GOOD))],
            )

    monkeypatch.setattr(app.anthropic, "Anthropic", FakeClient)
    monkeypatch.setenv("APP_ANTHROPIC_API_KEY", "app-test-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "global-key-must-not-be-used")

    text = app.call_model("system", "user")
    assert json.loads(text) == C1_GOOD
    assert created["api_key"] == "app-test-key"
    assert created["timeout"] == 20.0
    assert created["max_retries"] == 0
    assert created["request"]["model"] == "claude-haiku-4-5-20251001"
    assert os.environ["ANTHROPIC_API_KEY"] == "global-key-must-not-be-used"


def test_settings_ignore_global_key_and_read_env_file(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "global-key")
    env_file = tmp_path / ".env"
    assert app.load_settings(env_file) == ("", "claude-haiku-4-5-20251001")

    env_file.write_text("APP_ANTHROPIC_API_KEY=file-key\nAPP_ANTHROPIC_MODEL=file-model\n", encoding="utf-8")
    assert app.load_settings(env_file) == ("file-key", "file-model")
    assert "APP_ANTHROPIC_API_KEY" not in os.environ

    monkeypatch.setenv("APP_ANTHROPIC_MODEL", "env-model")
    assert app.load_settings(env_file) == ("file-key", "env-model")


def test_non_end_turn_response_is_rejected(monkeypatch):
    class TruncatedClient:
        def __init__(self, **kwargs):
            self.messages = SimpleNamespace(
                create=lambda **kw: SimpleNamespace(
                    stop_reason="max_tokens",
                    content=[SimpleNamespace(type="text", text='{"draft_reply": "Hi')],
                )
            )

    monkeypatch.setattr(app.anthropic, "Anthropic", TruncatedClient)
    monkeypatch.setenv("APP_ANTHROPIC_API_KEY", "app-test-key")
    assert_unavailable(app.run_case("C1"))


# --- Offline demo ------------------------------------------------------------


@pytest.mark.parametrize("case_id", app.CASE_IDS)
def test_offline_demo_outputs_are_valid_and_labelled(case_id):
    label, result = app.load_demo_output(case_id)
    assert "SYNTHETIC" in label
    app.validate_result(result, app.policy_ids(app.load_policy()), list(app.load_case(case_id)))

    outcome = app.run_case(case_id, model_fn=must_not_be_called, offline_demo=True)
    assert outcome["source"] == "offline_demo"
    assert outcome["result"] == result
    assert app.NO_API_LINE in outcome["notes"]


def test_offline_demo_expected_statuses():
    statuses = {c: app.load_demo_output(c)[1]["review_status"] for c in app.CASE_IDS}
    assert statuses == {
        "C1": "READY_FOR_HUMAN_REVIEW",
        "C2": "NEEDS_INFORMATION",
        "C3": "READY_FOR_HUMAN_REVIEW",
    }
    assert app.load_demo_output("C2")[1]["missing_information"] == ["delivery_date"]


def test_offline_demo_c3_does_not_claim_resolved():
    # P3: the issue is open, so the draft must not say it is resolved.
    draft = app.load_demo_output("C3")[1]["draft_reply"].lower()
    assert "can't confirm that your issue is resolved" in draft
    assert "still under review" in draft
    assert "has been resolved" not in draft


def test_offline_demo_prints_no_api_line(capsys):
    app.main(["--case", "C2", "--offline-demo"], model_fn=must_not_be_called)
    out = capsys.readouterr().out
    assert "OFFLINE DEMO: no model API was called." in out
    assert "NEEDS_INFORMATION" in out
    assert "delivery_date: null (missing)" in out
    assert "missing_information: delivery_date" in out
