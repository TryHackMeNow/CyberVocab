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

# Anthropic model used for definition generation.
MODEL = "claude-sonnet-4-6"

# Upper bound on tokens generated for a single definition. Definitions are
# meant to be short (one or two sentences), so this is kept tight.
DEFINITION_MAX_TOKENS = 150

# Prompt template used to ask the LLM for a definition. ``{term}`` is
# substituted with the user-supplied (and already-stripped) term.
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
    """Build an Anthropic API client from the environment, if possible.

    Returns:
        anthropic.Anthropic | None: A configured client instance, or
        ``None`` if the ``ANTHROPIC_API_KEY`` environment variable is not
        set or the ``anthropic`` package is not installed. Returning
        ``None`` lets callers degrade gracefully instead of raising.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    try:
        import anthropic
    except ImportError:
        return None
    return anthropic.Anthropic(api_key=api_key)


def _call_claude(prompt: str, max_tokens: int) -> str | None:
    """Send a single-turn prompt to Claude and return the text response.

    Args:
        prompt: The user-role message content to send.
        max_tokens: Maximum number of tokens to generate for the reply.

    Returns:
        str | None: The concatenated text of all text content blocks in
        the response, stripped of leading/trailing whitespace; ``None``
        if no client is available, the response contains no text, or the
        API call raises any exception (e.g. network error, auth failure,
        rate limit).
    """
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
    """Query the Google Suggest endpoint and return the raw suggestion list.

    Issues a GET request against ``GOOGLE_SUGGEST_URL`` using the
    ``firefox`` client parameter, which returns a JSON array of the shape
    ``[query, [suggestion, ...], ...]``.

    Args:
        query: The search prefix to request suggestions for.

    Returns:
        list[str]: The list of suggestion strings from the response
        (index 1 of the decoded JSON array). Returns ``[]`` if the
        request fails, times out, or the response is not valid JSON in
        the expected shape.
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
    """Return autocomplete suggestions for a search/term prefix.

    Wraps :func:`_fetch_google_suggestions` with input normalization,
    de-duplication (case-insensitive), and result-count limiting.

    Args:
        prefix: The text typed so far. Suggestions are only fetched once
            this is at least 2 characters long (after stripping
            whitespace); shorter prefixes return ``[]`` immediately to
            avoid noisy single-character queries.
        limit: Maximum number of suggestions to return. Defaults to 3.

    Returns:
        list[str]: Up to ``limit`` unique suggestion strings, in the
        order returned by Google, or ``[]`` if the prefix is too short or
        the underlying request fails.
    """
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
    """Fetch a short definition for a cybersecurity term via the LLM.

    Args:
        term: The term to define. Leading/trailing whitespace is
            stripped; an empty term short-circuits to ``None``.

    Returns:
        str | None: The generated definition text, or ``None`` if the
        term is empty or the underlying API call fails for any reason
        (see :func:`_call_claude`). Callers should treat ``None`` as
        "fall back to manual entry", not as an error to surface verbatim.
    """
    term = (term or "").strip()
    if not term:
        return None

    return _call_claude(
        DEFINITION_PROMPT_TEMPLATE.format(term=term),
        DEFINITION_MAX_TOKENS,
    )
