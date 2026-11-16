"""Key-gated connectors to outside services.

Only three, and only because each one closes a real gap in a small SDR workflow:

  Hunter.io   - find and verify the address behind a domain when the site does
                not publish one. The crawler handles the free half; this is the
                paid half, and it is optional.
  AI          - any OpenAI-compatible chat endpoint (OpenAI, DeepSeek, Ollama
                Cloud, a local server). Used to classify a company into a
                segment and to draft one opening line. Optional.
  Serper      - Google results for "company name -> official website" when all
                you have is a name. Optional.

Nothing here runs without a key in Settings, and every function raises
ConnectorError with a readable message instead of failing silently.
"""
import json
import re

from . import config, net

DEFAULT_AI_BASE = "https://api.openai.com/v1"


class ConnectorError(Exception):
    pass


def settings():
    value = config.load_settings().get("connectors") or {}
    return value if isinstance(value, dict) else {}


def status():
    s = settings()
    return {
        "hunter": bool((s.get("hunter_key") or "").strip()),
        "ai": bool((s.get("ai_key") or "").strip() and (s.get("ai_model") or "").strip()),
        "serper": bool((s.get("serper_key") or "").strip()),
        "treg": bool((s.get("treg_key") or "").strip()),
        "ai_base": (s.get("ai_base") or "").strip() or DEFAULT_AI_BASE,
        "ai_model": (s.get("ai_model") or "").strip(),
    }


def _need(name):
    s = settings()
    if name == "hunter":
        key = (s.get("hunter_key") or "").strip()
        if not key:
            raise ConnectorError("No Hunter.io API key. Add one in Settings -> Connectors.")
        return key
    if name == "ai":
        key = (s.get("ai_key") or "").strip()
        model = (s.get("ai_model") or "").strip()
        if not key or not model:
            raise ConnectorError("No AI endpoint configured. Add a base URL, key and model in "
                                 "Settings -> Connectors.")
        return key
    if name == "serper":
        key = (s.get("serper_key") or "").strip()
        if not key:
            raise ConnectorError("No Serper API key. Add one in Settings -> Connectors.")
        return key
    raise ConnectorError("Unknown connector %r" % name)


def _post_json(url, payload, headers, timeout=60):
    body = net.fetch(url, timeout=timeout, retries=1, method="POST",
                     data=json.dumps(payload).encode("utf-8"),
                     headers={"Content-Type": "application/json", **headers})
    try:
        return json.loads(body.decode("utf-8", "replace") or "{}")
    except ValueError:
        raise ConnectorError("The service returned something that is not JSON.")


# ------------------------------------------------------------------- Hunter
def hunter_domain(domain, limit=10):
    """Find addresses published behind a domain. Returns a list of dicts."""
    key = _need("hunter")
    url = ("https://api.hunter.io/v2/domain-search?domain=%s&limit=%d&api_key=%s"
           % (net.quote(domain), max(1, min(50, limit)), net.quote(key)))
    data = json.loads(net.text(url, timeout=45, retries=1) or "{}")
    body = data.get("data") or {}
    out = []
    for item in body.get("emails") or []:
        addr = (item.get("value") or "").strip().lower()
        if not addr:
            continue
        out.append({"email": addr, "first_name": item.get("first_name") or "",
                    "last_name": item.get("last_name") or "",
                    "position": item.get("position") or "",
                    "confidence": item.get("confidence") or 0,
                    "type": item.get("type") or ""})
    return {"emails": out, "organization": body.get("organization") or "",
            "pattern": body.get("pattern") or ""}


def hunter_verify(email):
    key = _need("hunter")
    url = ("https://api.hunter.io/v2/email-verifier?email=%s&api_key=%s"
           % (net.quote(email), net.quote(key)))
    data = json.loads(net.text(url, timeout=45, retries=1) or "{}")
    body = data.get("data") or {}
    return {"email": email, "status": body.get("status") or "unknown",
            "score": body.get("score"), "result": body.get("result") or ""}


# ----------------------------------------------------------------------- AI
def ai_chat(messages, temperature=0.2, max_tokens=400, timeout=90):
    """One call to an OpenAI-compatible chat endpoint. Returns the text."""
    key = _need("ai")
    s = settings()
    base = (s.get("ai_base") or "").strip().rstrip("/") or DEFAULT_AI_BASE
    model = (s.get("ai_model") or "").strip()
    payload = {"model": model, "messages": messages, "temperature": temperature,
               "max_tokens": max_tokens}
    data = _post_json(base + "/chat/completions", payload,
                      {"Authorization": "Bearer " + key}, timeout=timeout)
    try:
        return (data["choices"][0]["message"]["content"] or "").strip()
    except (KeyError, IndexError, TypeError):
        raise ConnectorError("The AI endpoint answered, but not in the OpenAI chat format.")


def ai_json(prompt, system="Reply with a single JSON object and nothing else.",
            max_tokens=500):
    text_value = ai_chat([{"role": "system", "content": system},
                          {"role": "user", "content": prompt}], max_tokens=max_tokens)
    match = re.search(r"\{.*\}", text_value, re.S)
    if not match:
        raise ConnectorError("The AI did not return JSON.")
    try:
        return json.loads(match.group(0))
    except ValueError:
        raise ConnectorError("The AI returned JSON-like text that does not parse.")


def classify_company(domain, title="", description="", text_sample=""):
    """Ask the model for a segment plus a one-line cold-email angle."""
    prompt = (
        "Company website: %s\nPage title: %s\nMeta description: %s\nVisible text: %s\n\n"
        "Return JSON with exactly these keys: industry (2-4 words), segment (2-4 words), "
        "size_guess (one of: solo, small, medium, large, unknown), country (ISO 2 letters or empty), "
        "angle (one sentence, max 20 words, why this company might need help with marketing or "
        "lead generation), confidence (0-1). Use only what the text supports; put \"unknown\" "
        "where it is a guess." % (domain, title or "-", description or "-", (text_sample or "-")[:2500])
    )
    return ai_json(prompt, max_tokens=450)


# ------------------------------------------------------------------- Serper
def serper_search(query, limit=5):
    key = _need("serper")
    data = _post_json("https://google.serper.dev/search", {"q": query, "num": max(1, min(10, limit))},
                      {"X-API-KEY": key}, timeout=45)
    out = []
    for item in (data.get("organic") or [])[:limit]:
        out.append({"title": item.get("title") or "", "link": item.get("link") or "",
                    "snippet": item.get("snippet") or ""})
    return out


def find_website(company):
    """Company name -> official website, using Serper when it is configured."""
    for hit in serper_search('"%s" official site' % company, limit=5):
        domain = net.domain_of(hit.get("link") or "")
        if domain and "wikipedia.org" not in domain and "linkedin.com" not in domain:
            return {"domain": domain, "url": hit.get("link"), "title": hit.get("title")}
    return None
