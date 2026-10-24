"""LabelLie backend: FastAPI + Gemma 4, with Claude as the code decoder.

Who does what
  Gemma 4  reads the label photos (front claims, ingredients, sugar, weight, verdicts).
  Claude   reads the photos instead if Gemma has no key or fails, so every scan is AI-read.
  Facts    additives.json explains known INS / E codes with sources.
  Claude   explains any code that additives.json does not know (e.g. the colours in Gems).
           If no Claude key is set, Gemma explains it instead.

Keys (set in the terminal, never in a file)
  GEMINI_API_KEY     Gemma 4 online, from https://aistudio.google.com/apikey
  ANTHROPIC_API_KEY  Claude, from https://console.anthropic.com (optional)
  No GEMINI_API_KEY  -> Gemma runs offline through Ollama (`ollama pull gemma4:e4b`)
  No Gemma at all    -> the phone reads the label text itself (OCR) and rules.py checks it.
                        The app never depends on any AI to work.

Run:  python -m uvicorn main:app --host 0.0.0.0 --port 8000   (or double-click start.bat)
"""
import json
import os
import re
from pathlib import Path

from fastapi import FastAPI, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

import rules

HERE = Path(__file__).parent
FACTS = json.loads((HERE / "additives.json").read_text(encoding="utf-8"))
GRAMS_PER_TEASPOON = 4

GEMINI_KEY = os.getenv("GEMINI_API_KEY")
GEMMA_API_MODEL = os.getenv("GEMMA_API_MODEL", "gemma-4-26b-a4b-it")  # Gemma 4 on the Gemini API
GEMMA_LOCAL_MODEL = os.getenv("GEMMA_MODEL", "gemma4:e4b")            # Gemma 4 on Ollama
CLAUDE_KEY = os.getenv("ANTHROPIC_API_KEY")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-haiku-5-5")         # fast, low-cost Claude model

app = FastAPI(title="LabelLie")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# =====================================================================
# 1. Gemma reads the label
# =====================================================================
LABEL_PROMPT = """You are a food-label analyst for Indian shoppers.
{image_note}
Read ONLY what is printed. Return ONLY JSON in exactly this shape:
{{
  "product_name": "string",
  "front_claims": ["each marketing claim on the front, e.g. 'Made with real fruit'"],
  "ingredients": ["each ingredient in label order, copied exactly, keep INS / E codes and % like 'Mango pulp (2%)' or 'Colour (INS 102, 133)'"],
  "sugar_g_per_100g": number or null,
  "net_weight_g": number or null,
  "sodium_mg_per_100g": number or null,
  "claim_checks": [
    {{"claim": "string", "verdict": "True" or "Misleading" or "False", "reason": "one short sentence using facts from the back label"}}
  ],
  "summary_en": "2 short, simple sentences for a common person",
  "summary_hi": "the same 2 sentences in simple Hindi (Devanagari script)"
}}
Rules:
- A claim is "Misleading" if technically true but the ingredients tell a different story
  (example: 'real fruit' but fruit is only 2% and sugar is near the top of the list).
- A claim is "False" only if the back label clearly contradicts it.
- If there is no front photo, front_claims and claim_checks are empty lists.
- If a number is not visible, use null. For ml products, treat 1 ml as 1 g.
"""


def image_type(data: bytes) -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


def parse_json(text: str):
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"[\[{].*[\]}]", text, re.S)
        return json.loads(match.group(0)) if match else {}


def gemma(prompt: str, images: list[bytes] | None = None, want_json: bool = True) -> str:
    """Send a prompt (and optional photos) to Gemma 4, online or offline."""
    images = images or []
    if GEMINI_KEY:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=GEMINI_KEY)
        parts = [types.Part.from_bytes(data=img, mime_type=image_type(img)) for img in images]
        res = client.models.generate_content(
            model=GEMMA_API_MODEL,
            contents=parts + [prompt],
            config=types.GenerateContentConfig(temperature=0.2),
        )
        return res.text or ""
    import ollama

    msg = {"role": "user", "content": prompt}
    if images:
        msg["images"] = images
    res = ollama.chat(model=GEMMA_LOCAL_MODEL, messages=[msg], format="json" if want_json else "", options={"temperature": 0.2})
    return res["message"]["content"]


_READY = {"value": None, "at": 0.0}


def gemma_ready() -> bool:
    """True if Gemma can be used: an API key, or Ollama running with a Gemma model."""
    if GEMINI_KEY:
        return True
    import time

    if _READY["value"] is not None and time.time() - _READY["at"] < 30:
        return _READY["value"]
    ok = False
    try:
        import ollama

        models = ollama.list()
        names = [getattr(m, "model", None) or (m.get("model") if isinstance(m, dict) else "") or "" for m in (getattr(models, "models", None) or models.get("models", []))]
        ok = any(str(n).startswith("gemma") for n in names)
    except Exception:
        ok = False
    _READY.update(value=ok, at=time.time())
    return ok


def claude_vision(prompt: str, images: list[bytes]) -> str:
    """Send the label photos to Claude (used when Gemma is missing or fails)."""
    import base64
    import anthropic

    client = anthropic.Anthropic(api_key=CLAUDE_KEY)
    content = [
        {"type": "image", "source": {"type": "base64", "media_type": image_type(img), "data": base64.b64encode(img).decode()}}
        for img in images
    ]
    content.append({"type": "text", "text": prompt})
    msg = client.messages.create(model=CLAUDE_MODEL, max_tokens=2500, messages=[{"role": "user", "content": content}])
    return "".join(block.text for block in msg.content if getattr(block, "type", "") == "text")


def read_label(images: list[bytes], has_front: bool) -> tuple[dict, str]:
    """AI reads the label: Gemma 4 first, Claude as the backup. Returns (data, engine name)."""
    note = (
        "Image 1 is the FRONT of the pack. Image 2 is the BACK (ingredients + nutrition table)."
        if has_front
        else "The image is the BACK of the pack (ingredients + nutrition table)."
    )
    prompt = LABEL_PROMPT.format(image_note=note)
    errors = []
    if gemma_ready():
        try:
            data = parse_json(gemma(prompt, images))
            if isinstance(data, dict) and data.get("ingredients"):
                return data, "Gemma 4"
            errors.append("Gemma found no ingredients")
        except Exception as err:
            errors.append(f"Gemma: {err}")
    if CLAUDE_KEY:
        try:
            data = parse_json(claude_vision(prompt, images))
            if isinstance(data, dict) and data.get("ingredients"):
                return data, "Claude"
            errors.append("Claude found no ingredients")
        except Exception as err:
            errors.append(f"Claude: {err}")
    raise RuntimeError("; ".join(errors) or "No AI key set")


# =====================================================================
# 2. Decoding INS / E codes: facts first, then AI
# =====================================================================
CODE_RE = re.compile(r"(?<![\d.])(\d{3,4}[a-f]?)(?:\s*\(\s*([ivx]+)\s*\))?(?![\d%.])", re.I)
DECODE_PROMPT = """You explain food additive codes (INS / E numbers) to Indian shoppers.
For each code below, return ONLY a JSON list, one object per code, in this shape:
[{{"code": "INS 133", "name": "common name", "type": "e.g. Artificial colour",
   "level": "green" | "orange" | "red",
   "fact": "one or two short plain sentences: what it is and its regulatory status (EU / FSSAI / US)",
   "veg": "Vegetarian" | "Non-vegetarian" | "Depends on source",
   "source": "the regulator or body your fact is based on"}}]
Levels: green = generally considered safe; orange = permitted but restricted, needs a warning,
or worth limiting; red = banned or withdrawn in major markets.
Rules: be accurate and calm. Never say "causes cancer" or "scam". If you do not recognise a code,
say so in "fact" and use level "orange". Codes: {codes}"""
AI_CACHE: dict[str, dict] = {}


def normalise(code: str) -> str:
    return re.sub(r"^(ins|e)\s*-?\s*", "", code.strip().lower()).replace(" ", "")


def lookup_fact(code: str):
    entry = FACTS["by_code"].get(code)
    if not entry:
        return None
    return {"code": f"INS {code.upper()}", **entry, "ai": False}


def ai_decode(codes: list[str]) -> list[dict]:
    """Explain codes not in additives.json. Claude if available, otherwise Gemma."""
    todo = [c for c in codes if c not in AI_CACHE]
    if todo and not CLAUDE_KEY and not gemma_ready():
        return [{"code": f"INS {c.upper()}", "name": f"INS {c.upper()}", "type": "Not in our list yet", "level": "orange", "veg": "",
                 "fact": "We have not checked this code yet. Add a Claude or Gemma key to explain it automatically.",
                 "source": "LabelLie facts list", "ai": False} for c in todo]
    if todo:
        prompt = DECODE_PROMPT.format(codes=", ".join(f"INS {c.upper()}" for c in todo))
        engine = "Claude" if CLAUDE_KEY else "Gemma"
        if CLAUDE_KEY:
            import anthropic

            client = anthropic.Anthropic(api_key=CLAUDE_KEY)
            msg = client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=1500,
                messages=[{"role": "user", "content": prompt}],
            )
            text = "".join(block.text for block in msg.content if getattr(block, "type", "") == "text")
        else:
            text = gemma(prompt)
        items = parse_json(text)
        if isinstance(items, dict):
            items = items.get("codes") or items.get("items") or [items]
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict):
                continue
            key = normalise(str(item.get("code", "")))
            if key:
                level = item.get("level") if item.get("level") in ("green", "orange", "red") else "orange"
                AI_CACHE[key] = {
                    "code": f"INS {key.upper()}",
                    "name": str(item.get("name") or f"INS {key.upper()}"),
                    "type": str(item.get("type") or "Food additive"),
                    "level": level,
                    "fact": str(item.get("fact") or ""),
                    "veg": str(item.get("veg") or ""),
                    "source": f"AI explanation ({engine}), based on {item.get('source') or 'general knowledge'}. Please verify.",
                    "ai": True,
                }
    return [AI_CACHE[c] for c in codes if c in AI_CACHE]


def find_additives(ingredients: list[str], use_ai: bool = True) -> list[dict]:
    text = " ".join(ingredients).lower()
    found, seen, unknown = [], set(), []
    for m in CODE_RE.finditer(text):
        code = m.group(1).lower()
        num = int(re.match(r"\d+", code).group())
        if not 100 <= num <= 1599 or code in seen:  # INS numbers live in 100-1599
            continue
        seen.add(code)
        fact = lookup_fact(code)
        if fact:
            found.append(fact)
        else:
            unknown.append(code)
    if unknown and use_ai:
        try:
            found.extend(ai_decode(unknown))
        except Exception as err:  # never let the decoder break a scan
            print("AI decode failed:", err)
    for entry in FACTS["by_name"]:
        hit = next((a for a in entry["aliases"] if a in text), None)
        if hit and entry["name"] not in seen:
            seen.add(entry["name"])
            found.append({"code": hit.title(), **{k: v for k, v in entry.items() if k != "aliases"}, "ai": False})
    order = {"red": 0, "orange": 1, "green": 2}
    return sorted(found, key=lambda a: order.get(a["level"], 3))


# =====================================================================
# 3. "Safe for whom?" rules (awareness only, not medical advice)
# =====================================================================
AZO_COLOURS = {"102", "104", "110", "122", "124", "129"}
SWEETENERS = {"950", "951", "955"}
SULPHITES = {str(c) for c in range(220, 229)}
BENZOATES = {"210", "211", "212", "213"}
HIDDEN_SUGARS = ["sugar", "maltodextrin", "dextrose", "glucose", "invert", "corn syrup", "fructose", "jaggery", "honey"]
LEVEL_RANK = {"OK": 0, "Check": 1, "Limit": 2, "Avoid": 3}


def safety_checks(ingredients: list[str], spoons, sodium) -> list[dict]:
    text = " ".join(ingredients).lower()
    codes = {m.group(1).lower() for m in CODE_RE.finditer(text)}
    has = lambda *words: any(w in text for w in words)
    tsp = spoons or 0
    sugars = [w for w in HIDDEN_SUGARS if w in text]
    caffeine = has("caffeine", "coffee", "cola", "guarana", "energy")
    alcohol = has("alcohol", "rum", "wine", "liqueur", "beer")
    raw = has("unpasteurised", "unpasteurized", "raw milk")
    trans = has("hydrogenated", "vanaspati")
    azo = codes & AZO_COLOURS
    people = []

    def person(key, name, icon):
        p = {"key": key, "name": name, "icon": icon, "level": "OK", "reasons": []}
        people.append(p)
        return p

    def flag(p, level, reason):
        if LEVEL_RANK[level] > LEVEL_RANK[p["level"]]:
            p["level"] = level
        if reason:
            p["reasons"].append(reason)

    p = person("pregnant", "Pregnant", "🤰")
    if alcohol: flag(p, "Avoid", "Contains alcohol")
    if raw: flag(p, "Avoid", "Unpasteurised ingredient")
    if caffeine: flag(p, "Limit", "Contains caffeine")
    if tsp >= 6: flag(p, "Limit", f"High sugar: {tsp:g} teaspoons")

    p = person("child", "Child", "🧒")
    if caffeine: flag(p, "Avoid", "Contains caffeine")
    if tsp >= 6: flag(p, "Avoid", f"{tsp:g} teaspoons of sugar, a full day's limit")
    elif tsp >= 3: flag(p, "Limit", f"{tsp:g} teaspoons of sugar")
    if azo: flag(p, "Limit", "Colour " + ", ".join(f"INS {c}" for c in sorted(azo)) + " needs a child warning in the EU")
    if codes & SWEETENERS: flag(p, "Limit", "Artificial sweetener")

    p = person("elderly", "Elderly", "👵")
    if sodium is not None and sodium >= 600: flag(p, "Limit", f"High salt: {sodium:g} mg sodium per 100 g")
    if trans: flag(p, "Limit", "Hydrogenated fat (trans fat risk)")
    if tsp >= 6: flag(p, "Limit", f"High sugar: {tsp:g} teaspoons")

    p = person("diabetic", "Diabetic", "🩸")
    if tsp >= 4: flag(p, "Avoid", f"{tsp:g} teaspoons of sugar")
    elif sugars: flag(p, "Limit", None)
    if sugars: p["reasons"].append("Sugars: " + ", ".join(sugars))
    if has("maida", "refined wheat flour"): flag(p, "Limit", "Maida raises blood sugar fast")

    p = person("asthma", "Asthma", "🫁")
    if codes & SULPHITES: flag(p, "Avoid", "Sulphites can trigger asthma in sensitive people")
    if codes & BENZOATES: flag(p, "Check", "Benzoate preservative")
    if azo: flag(p, "Check", "Azo colour")

    for p in people:
        if not p["reasons"]:
            p["reasons"].append("Nothing on our watch list")
    return people


# =====================================================================
# 4. Putting the result together
# =====================================================================
def to_number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def build_result(g: dict, use_ai: bool = True, engine: str = "Gemma 4") -> dict:
    ingredients = [str(i) for i in (g.get("ingredients") or [])]
    sugar = to_number(g.get("sugar_g_per_100g"))
    weight = to_number(g.get("net_weight_g"))
    sugar_total = round(sugar * weight / 100, 1) if sugar is not None and weight else None
    spoons = round(sugar_total / GRAMS_PER_TEASPOON, 1) if sugar_total is not None else None

    checks = [c for c in (g.get("claim_checks") or []) if isinstance(c, dict)]
    additives = find_additives(ingredients, use_ai)
    verdicts = {c.get("verdict") for c in checks}
    reds = sum(a["level"] == "red" for a in additives)

    if "False" in verdicts or reds or (spoons or 0) >= 6:
        overall = "Exposed"
    elif "Misleading" in verdicts or any(a["level"] == "orange" for a in additives):
        overall = "Misleading"
    else:
        overall = "Honest"

    return {
        "product_name": g.get("product_name") or "This product",
        "engine": engine,
        "overall": overall,
        "claim_checks": checks,
        "ingredients": ingredients,
        "additives": additives,
        "sugar_g_per_100g": sugar,
        "net_weight_g": weight,
        "sugar_total_g": sugar_total,
        "teaspoons": spoons,
        "safety": safety_checks(ingredients, spoons, to_number(g.get("sodium_mg_per_100g"))),
        "summary_en": g.get("summary_en", ""),
        "summary_hi": g.get("summary_hi", ""),
    }


# =====================================================================
# 5. Web routes
# =====================================================================
class DecodeRequest(BaseModel):
    q: str


class TextRequest(BaseModel):
    back_text: str
    front_text: str = ""


@app.get("/")
def home():
    return FileResponse(HERE / "index.html")


@app.get("/status")
def status():
    return {
        "gemma_ready": gemma_ready() or bool(CLAUDE_KEY),  # true when any AI can read labels
        "label_reader": ("Gemma 4 online" if GEMINI_KEY else "Gemma 4 offline (Ollama)" if gemma_ready() else "")
        + (" + Claude backup" if CLAUDE_KEY and gemma_ready() else "Claude" if CLAUDE_KEY else "")
        or "On-device text scanner (no AI key set)",
        "decoder": "Claude" if CLAUDE_KEY else ("Gemma 4" if gemma_ready() else "Facts list only"),
        "known_codes": len(FACTS["by_code"]),
    }


@app.post("/scan")
async def scan(back: UploadFile = File(...), front: UploadFile | None = File(None)):
    if not gemma_ready() and not CLAUDE_KEY:
        return JSONResponse({"error": "No AI key is set, so the phone will read the label itself.", "code": "no_ai"}, status_code=503)
    images = []
    if front is not None:
        images.append(await front.read())
    images.append(await back.read())
    try:
        data, engine = read_label(images, has_front=front is not None)
    except Exception as err:  # bad key, quota, Ollama not running, etc.
        return JSONResponse({"error": f"AI could not read the label: {err}"}, status_code=500)
    return build_result(data, engine=engine)


@app.post("/analyze-text")
def analyze_text(req: TextRequest):
    """No-AI path: label text from the phone's OCR (or typed) is checked with rules.py."""
    if len(req.back_text.strip()) < 5:
        return JSONResponse({"error": "We could not read any text. Try a clearer photo or type the ingredients."}, status_code=400)
    data = rules.read_text(req.back_text, req.front_text)
    return build_result(data, use_ai=True, engine="Text scanner (no AI)")


@app.post("/decode")
def decode(req: DecodeRequest):
    """Explain codes typed by the user, e.g. 'INS 133', 'E171, 129' or 'shellac 904'."""
    codes = []
    for m in CODE_RE.finditer(req.q.lower()):
        code = m.group(1).lower()
        if 100 <= int(re.match(r"\d+", code).group()) <= 1599 and code not in codes:
            codes.append(code)
    if not codes:
        return JSONResponse({"error": "Type an INS or E number, for example INS 133 or E171."}, status_code=400)
    results, unknown = [], []
    for c in codes[:10]:
        fact = lookup_fact(c)
        (results.append(fact) if fact else unknown.append(c))
    if unknown:
        try:
            results.extend(ai_decode(unknown))
        except Exception as err:
            return JSONResponse({"error": f"AI decoder failed: {err}", "results": results}, status_code=502)
    return {"results": results}
