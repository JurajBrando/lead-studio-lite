"""Managed LLM providers.

The app used to have one AI endpoint in Settings. This turns that into a list:
add as many providers as you like (OpenAI, Anthropic, DeepSeek, Ollama, OpenRouter
or any compatible server), pick a default, test them, and every AI feature uses
whichever is default.

Two wire formats are supported, because the two big families differ:
  openai     POST {base}/chat/completions   Authorization: Bearer <key>
  anthropic  POST {base}/messages           x-api-key + anthropic-version
"""
import json

from . import config, db, net

PRESETS = {
    "openai": {"label": "OpenAI", "base_url": "https://api.openai.com/v1",
               "model": "gpt-4o-mini", "kind": "openai"},
    "anthropic": {"label": "Anthropic (Claude)", "base_url": "https://api.anthropic.com/v1",
                  "model": "claude-3-5-sonnet-latest", "kind": "anthropic"},
    "deepseek": {"label": "DeepSeek", "base_url": "https://api.deepseek.com/v1",
                 "model": "deepseek-chat", "kind": "openai"},
    "ollama": {"label": "Ollama (local or cloud)", "base_url": "http://127.0.0.1:11434/v1",
               "model": "llama3.1", "kind": "openai"},
    "openrouter": {"label": "OpenRouter", "base_url": "https://openrouter.ai/api/v1",
                   "model": "", "kind": "openai"},
    "custom": {"label": "Custom / self-hosted", "base_url": "", "model": "", "kind": "openai"},
}

SECRET_KEYS = ("api_key", "key", "token")


class LLMError(Exception):
    pass


def presets():
    return [{"id": key, **value} for key, value in PRESETS.items()]


def _mask(row):
    out = dict(row)
    out["has_key"] = bool(str(out.pop("api_key", "") or "").strip())
    return out


def list_providers():
    rows = db.rows("SELECT * FROM llm_providers ORDER BY is_default DESC, id ASC")
    return [_mask(row) for row in rows]


def get(provider_id=None):
    """The requested provider, else the default, else the legacy Settings AI endpoint."""
    if provider_id:
        row = db.one("SELECT * FROM llm_providers WHERE id=?", (provider_id,))
        if row:
            return row
    row = db.one("SELECT * FROM llm_providers WHERE is_default=1 LIMIT 1")
    if row:
        return row
    row = db.one("SELECT * FROM llm_providers ORDER BY id LIMIT 1")
    if row:
        return row
    legacy = (config.load_settings().get("connectors") or {})
    if (legacy.get("ai_key") or "").strip() and (legacy.get("ai_model") or "").strip():
        return {"id": 0, "name": "Settings AI endpoint", "kind": "openai",
                "base_url": legacy.get("ai_base") or "https://api.openai.com/v1",
                "model": legacy.get("ai_model"), "api_key": legacy.get("ai_key"),
                "is_default": 1}
    return None


def save(payload):
    name = (payload.get("name") or "").strip()[:80]
    if not name:
        return {"ok": False, "error": "Give the provider a name."}
    kind = payload.get("kind") if payload.get("kind") in ("openai", "anthropic") else "openai"
    base_url = (payload.get("base_url") or "").strip().rstrip("/")
    model = (payload.get("model") or "").strip()
    if not base_url or not model:
        return {"ok": False, "error": "A base URL and a model are required."}
    data = {"name": name, "kind": kind, "base_url": base_url, "model": model}
    if payload.get("api_key"):
        data["api_key"] = payload["api_key"].strip()
    provider_id = payload.get("id")
    if provider_id:
        row = db.one("SELECT * FROM llm_providers WHERE id=?", (provider_id,))
        if not row:
            return {"ok": False, "error": "That provider no longer exists."}
        db.update("llm_providers", provider_id, data)
    else:
        data["created_ts"] = db.now()
        provider_id = db.insert("llm_providers", data)
    if payload.get("is_default"):
        set_default(provider_id)
    elif not db.scalar("SELECT COUNT(*) FROM llm_providers WHERE is_default=1"):
        set_default(provider_id)
    return {"ok": True, "id": provider_id}


def set_default(provider_id):
    db.execute("UPDATE llm_providers SET is_default=0")
    db.execute("UPDATE llm_providers SET is_default=1 WHERE id=?", (provider_id,))
    return {"ok": True}


def delete(provider_id):
    db.execute("DELETE FROM llm_providers WHERE id=?", (provider_id,))
    row = db.one("SELECT id FROM llm_providers ORDER BY id LIMIT 1")
    if row and not db.scalar("SELECT COUNT(*) FROM llm_providers WHERE is_default=1"):
        set_default(row["id"])
    return {"ok": True}


def status():
    rows = list_providers()
    default = next((r for r in rows if r["is_default"]), None)
    if not default:
        legacy = get()                    # may come from the old Settings AI endpoint
        if legacy:
            return {"count": len(rows), "configured": True,
                    "default": legacy.get("name") or "Settings AI endpoint",
                    "model": legacy.get("model") or ""}
    return {"count": len(rows), "configured": bool(default),
            "default": default["name"] if default else "",
            "model": default["model"] if default else ""}


# ------------------------------------------------------------------ talking
def _post(url, payload, headers, timeout=90):
    body = net.fetch(url, timeout=timeout, retries=1, method="POST",
                     data=json.dumps(payload).encode("utf-8"),
                     headers={"Content-Type": "application/json", **headers})
    try:
        return json.loads(body.decode("utf-8", "replace") or "{}")
    except ValueError:
        raise LLMError("The provider answered with something that is not JSON.")


def chat(messages, provider_id=None, max_tokens=600, temperature=0.3, system=None):
    """Send a chat completion to the chosen provider and return the text."""
    provider = get(provider_id)
    if not provider:
        raise LLMError("No LLM provider configured. Add one in Settings -> LLM providers "
                       "(OpenAI, Claude, DeepSeek, Ollama or any compatible endpoint).")
    key = str(provider.get("api_key") or "").strip()
    base = str(provider.get("base_url") or "").rstrip("/")
    model = provider.get("model") or ""
    kind = (provider.get("kind") or "openai").lower()
    if not base or not model:
        raise LLMError("That provider has no base URL or model.")

    if kind == "anthropic":
        payload = {"model": model, "max_tokens": max_tokens, "temperature": temperature,
                   "messages": [{"role": m["role"], "content": m["content"]} for m in messages]}
        if system:
            payload["system"] = system
        data = _post(base + "/messages", payload,
                     {"x-api-key": key, "anthropic-version": "2023-06-01"})
        blocks = data.get("content") or []
        text_value = "".join(b.get("text", "") for b in blocks if isinstance(b, dict))
        if not text_value:
            raise LLMError("The provider answered, but without text content.")
        return text_value.strip()

    payload = {"model": model, "temperature": temperature, "max_tokens": max_tokens,
               "messages": ([{"role": "system", "content": system}] if system else [])
               + [{"role": m["role"], "content": m["content"]} for m in messages]}
    headers = {"Authorization": "Bearer " + key} if key else {}
    data = _post(base + "/chat/completions", payload, headers)
    try:
        return (data["choices"][0]["message"]["content"] or "").strip()
    except (KeyError, IndexError, TypeError):
        raise LLMError("The provider answered, but not in the OpenAI chat format.")


def test(provider_id=None):
    provider = get(provider_id)
    if not provider:
        return {"ok": False, "error": "No provider configured yet."}
    try:
        reply = chat([{"role": "user", "content": "Reply with the single word: ready"}],
                     provider_id=provider.get("id") or None, max_tokens=10, temperature=0)
    except LLMError as exc:
        if provider.get("id"):
            db.execute("UPDATE llm_providers SET last_test_ts=?, last_test_ok=0 WHERE id=?",
                       (db.now(), provider["id"]))
        return {"ok": False, "error": str(exc), "provider": provider.get("name")}
    if provider.get("id"):
        db.execute("UPDATE llm_providers SET last_test_ts=?, last_test_ok=1 WHERE id=?",
                   (db.now(), provider["id"]))
    return {"ok": True, "reply": reply[:80], "provider": provider.get("name"),
            "model": provider.get("model")}
