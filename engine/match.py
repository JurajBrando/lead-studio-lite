"""Business fit: describe what you sell, get the leads that fit.

This is the "monitoring your offer across potential leads" idea, separated from
the offer catalogue. You write a paragraph about your business - or paste your
website - and the app turns it into a profile (industry, segment, keywords) and
scores every lead against it.

With an LLM provider configured, the profile comes from the model. Without one,
it falls back to a keyword extraction on your text, which is cruder but never
fails.
"""
import re

from . import crawl, db, llm

STOP = {
    "the", "and", "for", "with", "that", "this", "from", "your", "you", "our", "their", "have",
    "are", "was", "were", "will", "can", "not", "but", "all", "any", "who", "what", "when",
    "which", "them", "they", "his", "her", "its", "about", "into", "more", "than", "then",
    "there", "been", "being", "would", "could", "should", "also", "just", "like", "such",
    "very", "much", "many", "most", "some", "only", "over", "under", "between", "after",
    "before", "while", "where", "because", "through", "during", "without", "within", "help",
    "helps", "helping", "need", "needs", "want", "wants", "get", "gets", "make", "makes",
    "business", "businesses", "company", "companies", "service", "services", "product",
    "products", "people", "work", "works", "working", "team", "teams", "time", "times",
}

MATCH_FIELDS = (("industry", 4), ("tags", 3), ("company", 2), ("title", 2), ("notes", 1))


def keywords_from_text(text_value, limit=15):
    words = re.findall(r"[a-zA-Z][a-zA-Z\-]{3,}", text_value or "")
    counts = {}
    for word in words:
        low = word.lower()
        if low in STOP:
            continue
        counts[low] = counts.get(low, 0) + 1
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return [word for word, _ in ranked[:limit]]


def _from_ai(text_value):
    prompt = (
        "This describes a business that sells a product or service:\n\n%s\n\n"
        "Return JSON with keys: industry (2-4 words), segment (2-4 words), "
        "keywords (array of 8-12 single words or short phrases that describe who NEEDS this, "
        "not what it is), summary (one sentence)." % (text_value or "")[:4000])
    answer = llm.chat([{"role": "user", "content": prompt}], max_tokens=500, temperature=0.2)
    match = re.search(r"\{.*\}", answer or "", re.S)
    if not match:
        raise ValueError("no JSON in the answer")
    import json
    data = json.loads(match.group(0))
    words = [str(w).strip().lower() for w in (data.get("keywords") or []) if str(w).strip()]
    return {"industry": str(data.get("industry") or ""),
            "segment": str(data.get("segment") or ""),
            "summary": str(data.get("summary") or ""),
            "keywords": words[:15]}


def build_profile(text=None, url=None, use_ai=True):
    """Turn a description or a URL into a matching profile."""
    source = (url or "").strip()
    description = (text or "").strip()
    page = {}
    if source:
        page = crawl.crawl_domain(source, max_pages=2, pause=0.2)
        description = " ".join(filter(None, [page.get("title"), page.get("description"),
                                             description]))[:4000]
    if not description:
        return {"ok": False, "error": "Describe your business or paste your website."}

    profile = {"description": description[:2000], "keywords": keywords_from_text(description),
               "industry": "", "segment": "", "summary": "", "used_ai": False,
               "url": source, "title": page.get("title", "")}
    if use_ai and llm.status().get("configured"):
        try:
            ai = _from_ai(description)
            profile.update({"industry": ai["industry"], "segment": ai["segment"],
                            "summary": ai["summary"], "used_ai": True})
            if ai["keywords"]:
                merged = ai["keywords"] + [k for k in profile["keywords"] if k not in ai["keywords"]]
                profile["keywords"] = merged[:15]
        except (llm.LLMError, ValueError):
            profile["ai_error"] = "The model did not answer with a usable profile; keywords only."
    return {"ok": True, **profile}


def score_lead(words, lead):
    score = 0
    matched = []
    for field, weight in MATCH_FIELDS:
        haystack = str(lead.get(field) or "").lower()
        if not haystack:
            continue
        for word in words:
            if word and word in haystack:
                score += weight
                if word not in matched:
                    matched.append(word)
    return score, matched


def match(text=None, url=None, limit=25, use_ai=True, min_score=2):
    profile = build_profile(text=text, url=url, use_ai=use_ai)
    if not profile.get("ok"):
        return profile
    words = profile.get("keywords") or []
    rows = db.rows("SELECT * FROM leads ORDER BY id DESC LIMIT 4000")
    results = []
    for lead in rows:
        score, matched = score_lead(words, lead)
        if score >= min_score:
            results.append({"lead": lead, "score": score, "matched": matched[:6],
                            "reason": "matches: " + ", ".join(matched[:4])})
    results.sort(key=lambda item: (-item["score"], item["lead"]["id"]))
    return {"ok": True, "profile": profile, "matches": results[:limit],
            "scanned": len(rows), "found": len(results)}
