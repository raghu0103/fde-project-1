# Meridian Retail support-draft review tool

A small command-line tool that drafts a reply to one customer-support case so a
human agent can review it. It loads the case facts from `cases.json` and the
policy from `policy.md`, asks a Claude model for a draft in a fixed JSON shape,
checks that shape, and prints the result.

> `policy.md`, `cases.json` and `demo_outputs.json` are **synthetic**. They are
> not real Meridian Retail policy, customers or orders.

## What it does not do

- It never sends a message, approves a refund or changes a record. It only
  reads local files and prints to the terminal.
- It has no web UI, database, retrieval system, login or production integration.
- A result that passes the structure checks is not necessarily correct. A
  person must check every draft against the facts and policy.

## Setup

Needs Python 3.10 or newer.

```bash
python -m venv .venv
```

Activate it (`.venv\Scripts\activate` on Windows, `source .venv/bin/activate`
elsewhere), then:

```bash
pip install -r requirements.txt
```

```bash
python setup_env.py
```

`setup_env.py` asks for the app API key (the input is hidden) and the model name
(press Enter for `claude-haiku-4-5-20251001`). It writes them to `.env` in this
folder as `APP_ANTHROPIC_API_KEY` and `APP_ANTHROPIC_MODEL`. It never prints the
key. `.env` is git-ignored. `.env.example` shows the format with placeholders.

The app reads only `APP_ANTHROPIC_API_KEY` and `APP_ANTHROPIC_MODEL`, from the
process environment first and then `.env`. It does not read or set the global
`ANTHROPIC_API_KEY`.

## Usage

```bash
python app.py --case C1
```

The case can be `C1`, `C2` or `C3`. The output has three sections:
`SUPPLIED FACTS`, `SUPPLIED POLICY` and `DRAFT RESULT FOR HUMAN REVIEW`.

The draft result has these fields:

| Field | Type | Meaning |
|---|---|---|
| `draft_reply` | text | Draft for a human to review |
| `evidence_refs` | list of policy IDs | Clauses in `policy.md` the draft relies on |
| `missing_information` | list of field names | Facts the policy needs that the case lacks |
| `review_status` | `READY_FOR_HUMAN_REVIEW`, `NEEDS_INFORMATION`, `BLOCKED` | Suggested next step for the reviewer |

### Offline demo (no API call)

```bash
python app.py --case C1 --offline-demo
```

Shows the prerecorded synthetic result for the case from `demo_outputs.json`
and prints `OFFLINE DEMO: no model API was called.`

### Simulated timeout (no API call)

```bash
python app.py --case C1 --simulate-timeout
```

Shows the unavailable state: empty `draft_reply`, `review_status` of
`UNAVAILABLE`, and a manual fallback message.

## Failure handling

The model call uses a 20-second timeout and no retries. The tool shows the same
`UNAVAILABLE` state and manual fallback message, never a traceback or a stale
draft, when any of these happen:

- no key is configured
- the request times out or the provider returns an error
- the response is not a single JSON object
- the response has missing or extra keys, or a field has the wrong type
- `review_status` is not one of the allowed values
- `evidence_refs` names an ID that is not in `policy.md`
- `missing_information` names a field that is not listed in `cases.json`
- the status contradicts the other fields (for example, `READY_FOR_HUMAN_REVIEW`
  with an empty draft, or `NEEDS_INFORMATION` with nothing missing)

## Tests

```bash
python -m pytest
```

The tests never call an external API. They use fake model functions and a
temporary `.env` path.

## Files

| File | Purpose |
|---|---|
| `app.py` | The command-line tool |
| `setup_env.py` | Writes the project `.env` |
| `policy.md` | Synthetic support policy with clause IDs |
| `cases.json` | Synthetic cases C1-C3 and the allowed fact field names |
| `demo_outputs.json` | Prerecorded synthetic results for `--offline-demo` |
| `test_app.py`, `test_setup_env.py` | Tests |
