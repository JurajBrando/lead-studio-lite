"""AI enrichment: classify a company and write the opening angle.

Uses whichever LLM provider is the default in Settings -> LLM providers (OpenAI,
Anthropic, DeepSeek, Ollama, OpenRouter or any compatible endpoint). It reads the
company's homepage, asks for industry / segment / size / country plus one
cold-email angle, and writes the result onto the lead.

Without a provider the job refuses politely and changes nothing.
"""
import json
import re
import time

from .. import crawl, db, llm
from .base import Job, Context

PROMPT = (
    "Company website: {domain}\nPage title: {title}\nMeta description: {description}\n\n"
    "Return JSON with exactly these keys: industry (2-4 words), segment (2-4 words), "
    "size_guess (solo|small|medium|large|unknown), country (ISO 2 letters or empty), "
    "angle (one sentence, max 20 words, why this company might need help with marketing or lead "
    "generation), confidence (0-1). Use only what the text supports; put \"unknown\" where it is a guess."
)


def _parse_json(text_value):
    match = re.search(r"\{.*\}", text_value or "", re.S)
    if not match:
        raise ValueError("no JSON in the answer")
    return json.loads(match.group(0))


def run(ctx):
    state = llm.status()
    if not state.get("configured"):
        return {"ok": False,
                "error": "No LLM provider configured. Add one in Settings -> LLM providers "
                         "(OpenAI, Claude, DeepSeek, Ollama or any compatible endpoint)."}

    limit = ctx.int_param("limit", 10)
    pause = float(ctx.param("pause", 1.0))
    max_pages = ctx.int_param("max_pages", 1)
    override = bool(ctx.param("override_industry", False))

    where = "1=1" if override else "(industry='' OR industry IS NULL)"
    rows = db.rows("SELECT * FROM leads WHERE %s ORDER BY id DESC LIMIT ?" % where, (limit,))
    if not rows:
        return ctx.summary(classified=0, note="Every lead already has an industry.")

    classified = 0
    for lead in rows:
        domain = (lead.get("domain") or "").strip() or (lead.get("email") or "").split("@")[-1]
        if not domain:
            continue
        page = crawl.crawl_domain(domain, max_pages=max_pages, pause=0.3)
        if not (page.get("title") or page.get("description")) and page.get("error"):
            ctx.warn("could not read %s: %s" % (domain, page["error"]))
            continue
        try:
            answer = llm.chat([{"role": "user", "content": PROMPT.format(
                domain=domain, title=page.get("title", "") or "-",
                description=page.get("description", "") or "-")}], max_tokens=450,
                temperature=0.2)
            profile = _parse_json(answer)
        except (llm.LLMError, ValueError) as exc:
            ctx.warn(str(exc))
            break

        industry = str(profile.get("industry") or "").strip()[:80]
        segment = str(profile.get("segment") or "").strip()[:80]
        country = str(profile.get("country") or "").strip()[:40]
        size = str(profile.get("size_guess") or "").strip()[:20]
        angle = str(profile.get("angle") or "").strip()[:300]

        patch = {"updated_ts": db.now()}
        if industry:
            patch["industry"] = industry
        if country and not lead.get("country"):
            patch["country"] = country
        tags = [t for t in (lead.get("tags") or "").split(",") if t.strip()]
        lowered = [t.lower() for t in tags]
        for tag in ("ai", size, segment):
            if tag and tag.lower() not in lowered:
                tags.append(tag)
        patch["tags"] = ",".join(tags)[:300]
        notes = (lead.get("notes") or "").strip()
        line = "AI angle: %s" % angle if angle else ""
        if line and line not in notes:
            patch["notes"] = (notes + "\n" + line).strip()[:4000]
        db.update("leads", lead["id"], patch)
        ctx.add_signal("ai_profile", domain=domain, company=lead.get("company") or "",
                       title=industry, source="ai", source_url="https://" + domain,
                       payload={"segment": segment, "size": size, "country": country,
                                "angle": angle, "confidence": profile.get("confidence")})
        classified += 1
        time.sleep(max(0.0, pause))

    ctx.log("classified %d leads with %s" % (classified, state.get("model") or "the AI endpoint"))
    return ctx.summary(classified=classified, model=state.get("model"))


JOBS = [Job(
    key="ai_enrich",
    name="AI enrichment: segment + angle",
    group="AI",
    description="Reads each lead's homepage and asks your default LLM provider for industry, "
                "segment, size, country and a one-line cold-email angle. Optional; needs a provider.",
    run=run,
    schedule="daily 10:00",
    params={"limit": 10, "pause": 1.0, "max_pages": 1, "override_industry": False},
)]
