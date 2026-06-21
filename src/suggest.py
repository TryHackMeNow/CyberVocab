"""Fetches cybersecurity term suggestions from Google's public autocomplete
("suggest") endpoint as the user types into the 'new_term_input' input
field, and fetches term definitions from an LLM (Anthropic API).

Suggestions: uses the unofficial but widely-used Google Suggest endpoint at
https://suggestqueries.google.com/complete/search (same family of protocol
described in Google's /suggest XML reference). No API key is required for
this part. If the request fails for any reason (network error, unexpected
response shape, timeout), suggest_terms() fails gracefully and returns [].

Definitions: continues to use Claude via the Anthropic API, as before.
Requires the ANTHROPIC_API_KEY environment variable to be set. If it is
missing, the SDK is not installed, or a request fails for any reason,
get_definition() fails gracefully and returns None. Both fallback cases let
callers fall back to manual entry.
"""

import json
import os
import urllib.parse
import urllib.request

MODEL = "claude-sonnet-4-6"
DEFINITION_MAX_TOKENS = 150

DEFINITION_PROMPT_TEMPLATE = (
    'Define the cybersecurity term "{term}" as a single concise, precise '
    "vocabulary entry, one or two sentences. No preamble, no markdown, "
    "no restating the term itself — just the definition text."
)

# --- Google Suggest endpoint configuration ---
GOOGLE_SUGGEST_URL = "https://suggestqueries.google.com/complete/search"
GOOGLE_SUGGEST_TIMEOUT_SECONDS = 3.0
GOOGLE_SUGGEST_USER_AGENT = "Mozilla/5.0"

def _get_client():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    try:
        import anthropic
    except ImportError:
        return None
    return anthropic.Anthropic(api_key=api_key)


def _call_claude(prompt: str, max_tokens: int) -> str | None:
    client = _get_client()
    if client is None:
        return None
    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(
            block.text for block in response.content if getattr(block, "type", None) == "text"
        ).strip()
        return text or None
    except Exception as e:
        print(e)
        return None


def _fetch_google_suggestions(query: str) -> list[str]:
    """Low-level call to Google's public autocomplete endpoint. Returns the
    raw list of suggestion strings, or [] on any failure (network error,
    timeout, unexpected response shape). No API key required.

    Response shape (per Google's /suggest "OpenSearch"-style protocol,
    requested here via client=firefox): a JSON array whose first element
    is the echoed query and second element is a list of suggestion
    strings, e.g. ["phi", ["phishing", "phishing email", ...]].
    """
    params = urllib.parse.urlencode({"client": "firefox", "q": query})
    url = f"{GOOGLE_SUGGEST_URL}?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": GOOGLE_SUGGEST_USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=GOOGLE_SUGGEST_TIMEOUT_SECONDS) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"suggest.py: Google Suggest request failed: {e}")
        return []

    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return []

    if not isinstance(data, list) or len(data) < 2 or not isinstance(data[1], list):
        return []

    return [item for item in data[1] if isinstance(item, str)]


def suggest_terms(prefix: str, limit: int = 3) -> list[str]:
    """Return a list of likely cybersecurity term suggestions starting with
    `prefix`, sourced from Google's public autocomplete endpoint. The query
    sent to Google is biased toward cybersecurity terminology so results
    stay on-topic. Returns an empty list if the prefix is too short or the
    request fails for any reason."""
    prefix = (prefix or "").strip()
    if len(prefix) < 2:
        return []

    raw_suggestions = _fetch_google_suggestions(f"{prefix}")

    results: list[str] = []
    seen: set[str] = set()
    for raw in raw_suggestions:
        term = raw.strip()
        key = term.lower()
        if not term or key in seen:
            continue
        seen.add(key)
        results.append(term)
        if len(results) >= limit:
            break

    return results


def get_definition(term: str) -> str | None:
    """Ask the LLM for a concise definition of `term`.

    Returns the definition text, or None if no API key is configured or
    the request fails for any reason (network error, bad response, etc.).
    """
    term = (term or "").strip()
    if not term:
        return None

    return _call_claude(
        DEFINITION_PROMPT_TEMPLATE.format(term=term),
        DEFINITION_MAX_TOKENS,
    )
