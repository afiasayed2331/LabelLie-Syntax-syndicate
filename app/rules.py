"""No-AI label reader for LabelLie.

Turns raw label text (from the phone's OCR, or typed by the user) into the same shape
Gemma returns, using plain rules. Used when Gemma is not available.
"""
import re

SUGAR_WORDS = ["sugar", "glucose", "dextrose", "maltodextrin", "invert", "corn syrup", "fructose", "sucrose", "jaggery", "honey", "liquid glucose"]
FRUITS = ["mango", "orange", "apple", "guava", "litchi", "lychee", "pineapple", "grape", "strawberry", "banana", "mixed fruit", "fruit", "lemon", "pomegranate", "cranberry"]
SYNTHETIC_COLOURS = {"102", "104", "110", "122", "124", "127", "129", "132", "133", "151", "155"}
CODE_RE = re.compile(r"(?<![\d.])(\d{3,4}[a-f]?)(?![\d%.])", re.I)


def clean(text: str) -> str:
    text = (text or "").replace("\r", "\n")
    text = re.sub(r"[|_]+", " ", text)
    return re.sub(r"[ \t]+", " ", text)


def split_ingredients(block: str) -> list[str]:
    """Split on commas that are not inside brackets."""
    items, depth, cur = [], 0, ""
    for ch in block:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth = max(0, depth - 1)
        if ch in ",;" and depth == 0:
            items.append(cur)
            cur = ""
        else:
            cur += ch
    items.append(cur)
    out = []
    for item in items:
        item = re.sub(r"\s+", " ", item).strip(" .:-\n")
        if 1 < len(item) < 120:
            out.append(item[0].upper() + item[1:])
    return out


def find_ingredients(text: str) -> list[str]:
    low = text.lower()
    start = low.find("ingredient")
    if start == -1:
        block = text  # typed text with no heading: treat it all as the list
    else:
        block = text[start:]
        block = re.sub(r"^\s*ingredients?\s*[:\-.]?", "", block, flags=re.I)
    stop = re.search(r"(nutrition|nutritional|allergen|contains:|may contain|manufactured|marketed|best before|net (wt|weight|qty|quantity)|storage|mrp|batch|fssai|lic\.? no)", block, re.I)
    if stop:
        block = block[: stop.start()]
    return split_ingredients(block.replace("\n", " "))


def number_after(pattern: str, text: str):
    m = re.search(pattern, text, re.I | re.S)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", "."))
    except ValueError:
        return None


def parse_numbers(back: str, front: str = "") -> dict:
    low = back.lower()
    i = low.find("nutrition")
    table = back[i:] if i != -1 else back  # read nutrition numbers from the nutrition table when we can
    sugar = number_after(r"(?:total\s+)?sugars?\s*(?:\(g\))?\s*[:\-]?\s*(\d+(?:[.,]\d+)?)\s*(?:g|gm)?(?!\s*%)", table)
    protein = number_after(r"protein\s*(?:\(g\))?\s*[:\-]?\s*(\d+(?:[.,]\d+)?)(?!\s*%)", table)
    sodium = number_after(r"sodium\s*(?:\(mg\))?\s*[:\-]?\s*(\d+(?:[.,]\d+)?)\s*(?:mg)?(?!\s*%)", table)
    weight = None
    unit_re = r"(kg|g|gm|gms|grams?|ml|l|litre|liter)\b"
    m = re.search(r"net\s*(?:wt|weight|qty|quantity|vol|volume|content)?\.?\s*[:\-]?\s*(\d+(?:[.,]\d+)?)\s*" + unit_re, back + "\n" + front, re.I)
    if not m and front:  # the front of a pack often shows just "40 g" or "250 ml"
        m = re.search(r"\b(\d+(?:[.,]\d+)?)\s*" + unit_re, front, re.I)
    if m:
        value = float(m.group(1).replace(",", "."))
        unit = m.group(2).lower()
        weight = value * 1000 if unit in ("kg", "l", "litre", "liter") else value
    return {"sugar_g_per_100g": sugar, "net_weight_g": weight, "protein_g_per_100g": protein, "sodium_mg_per_100g": sodium}


def percent_of(word: str, ingredients: list[str]):
    for item in ingredients:
        if word in item.lower():
            m = re.search(r"(\d+(?:\.\d+)?)\s*%", item)
            return float(m.group(1)) if m else None
    return None


def position(words, ingredients: list[str]):
    for i, item in enumerate(ingredients):
        if any(w in item.lower() for w in words):
            return i
    return None


def check_claims(front: str, ingredients: list[str], nums: dict) -> list[dict]:
    f = front.lower()
    ing_text = " ".join(ingredients).lower()
    codes = {c.lower() for c in CODE_RE.findall(ing_text)}
    added_sugars = [w for w in SUGAR_WORDS if w in ing_text]
    sugar_pos = position(SUGAR_WORDS, ingredients)
    checks = []

    def add(claim, verdict, reason):
        checks.append({"claim": claim, "verdict": verdict, "reason": reason})

    fruit = next((w for w in FRUITS if re.search(r"real\s+" + w, f) or re.search(w + r"\s+(juice|pulp)", f)), None)
    if fruit or "real fruit" in f:
        fruit = fruit or "fruit"
        pct = percent_of(fruit, ingredients)
        fpos = position([fruit], ingredients)
        if pct is not None and pct < 25:
            add(f"Made with real {fruit}", "Misleading", f"{fruit.title()} is only {pct:g}% of the product.")
        elif sugar_pos is not None and fpos is not None and sugar_pos < fpos:
            add(f"Made with real {fruit}", "Misleading", f"Sugar is listed before {fruit}, so there is more sugar than {fruit}.")
        elif fpos is None:
            add(f"Made with real {fruit}", "False", f"No {fruit} found in the ingredient list.")
        else:
            add(f"Made with real {fruit}", "True", f"{fruit.title()} is in the ingredient list.")

    if re.search(r"no\s+(added\s+)?sugar\s*(added)?|sugar\s*free|zero\s+sugar", f):
        sweet = codes & {"950", "951", "955", "954", "952"}
        if added_sugars:
            add("No added sugar", "False", "The ingredients include " + ", ".join(added_sugars[:3]) + ".")
        elif sweet:
            add("No added sugar", "True", "No sugar added, but it contains artificial sweetener (INS " + ", ".join(sorted(sweet)) + ").")
        else:
            add("No added sugar", "True", "No added sugar found in the ingredients.")

    if re.search(r"no\s+(added\s+|artificial\s+)?preservatives?|preservative\s*free", f):
        pres = sorted(c for c in codes if c[:3].isdigit() and 200 <= int(c[:3]) <= 299)
        if pres or "preservative" in ing_text:
            add("No preservatives", "False", "The back lists a preservative" + (" (INS " + ", ".join(pres) + ")" if pres else "") + ".")
        else:
            add("No preservatives", "True", "No preservative found in the ingredients.")

    if re.search(r"no\s+artificial\s+colou?rs?|natural\s+colou?rs?\s+only", f):
        syn = sorted(codes & SYNTHETIC_COLOURS)
        if syn:
            add("No artificial colours", "False", "Contains synthetic colour INS " + ", ".join(syn) + ".")
        else:
            add("No artificial colours", "True", "No synthetic colour codes found.")

    if re.search(r"multi\s*-?grain|whole\s*wheat|atta|wholegrain|whole\s+grain", f):
        first = ingredients[0].lower() if ingredients else ""
        if "refined wheat flour" in first or "maida" in first:
            add("Multigrain / whole wheat", "Misleading", "The first ingredient is refined wheat flour (maida), not whole grain.")
        elif ingredients:
            add("Multigrain / whole wheat", "True", "Whole grains appear early in the ingredient list.")

    if re.search(r"no\s+maida", f):
        if "maida" in ing_text or "refined wheat flour" in ing_text:
            add("No maida", "False", "Refined wheat flour is maida, and it is in the ingredients.")
        else:
            add("No maida", "True", "No refined wheat flour found.")

    if re.search(r"high\s+protein|protein\s+rich|rich\s+in\s+protein", f):
        p = nums.get("protein_g_per_100g")
        if p is not None and p < 10:
            add("High protein", "Misleading", f"Only {p:g} g protein per 100 g.")
        elif p is not None:
            add("High protein", "True", f"{p:g} g protein per 100 g.")

    if re.search(r"(100\s*%\s*)?natural|no\s+artificial", f) and not any(c["claim"] == "No artificial colours" for c in checks):
        artificial = sorted(codes & SYNTHETIC_COLOURS)
        if artificial or "artificial" in ing_text or "nature identical" in ing_text:
            add("Natural", "Misleading", "Contains artificial or nature-identical flavour or colour.")

    if re.search(r"(zero|no|0\s*g)\s+trans\s*fat", f) and ("hydrogenated" in ing_text or "vanaspati" in ing_text):
        add("Zero trans fat", "Misleading", "It uses hydrogenated fat, a trans-fat source; small amounts can be rounded down to zero.")

    if re.search(r"\bhealthy\b|\bhealth\b", f):
        tsp = (nums["sugar_g_per_100g"] or 0) * (nums["net_weight_g"] or 0) / 400
        if tsp >= 4 or "refined wheat flour" in ing_text or "maida" in ing_text:
            add("Healthy", "Misleading", "High in sugar or refined flour.")

    return checks


def guess_name(front: str, back: str) -> str:
    for line in (front or back or "").split("\n"):
        line = line.strip()
        if 3 <= len(line) <= 40 and re.search(r"[a-zA-Z]{3}", line) and not re.search(r"ingredient|nutrition|net|fssai|mrp", line, re.I):
            return line.title()
    return "This product"


def summarise(name, checks, nums, ingredients):
    bad = [c for c in checks if c["verdict"] != "True"]
    sugar, weight = nums["sugar_g_per_100g"], nums["net_weight_g"]
    tsp = round(sugar * weight / 100 / 4, 1) if sugar is not None and weight else None
    parts_en, parts_hi = [], []
    if bad:
        parts_en.append(f'The claim "{bad[0]["claim"]}" does not match the back label.')
        parts_hi.append(f'"{bad[0]["claim"]}" का दावा पीछे के लेबल से मेल नहीं खाता।')
    elif checks:
        parts_en.append("The front claims match the back label.")
        parts_hi.append("आगे के दावे पीछे के लेबल से मेल खाते हैं।")
    if tsp is not None:
        parts_en.append(f"The whole pack has about {tsp:g} teaspoons of sugar.")
        parts_hi.append(f"पूरे पैकेट में लगभग {tsp:g} चम्मच चीनी है।")
    if not parts_en:
        parts_en.append(f"We found {len(ingredients)} ingredients. Tap the codes below to see what they mean.")
        parts_hi.append(f"हमें {len(ingredients)} सामग्री मिलीं। नीचे के कोड पर टैप करके मतलब जानें।")
    return " ".join(parts_en), " ".join(parts_hi)


def read_text(back_text: str, front_text: str = "") -> dict:
    back, front = clean(back_text), clean(front_text)
    ingredients = find_ingredients(back)
    nums = parse_numbers(back, front)
    checks = check_claims(front, ingredients, nums) if front.strip() else []
    name = guess_name(front, back)
    summary_en, summary_hi = summarise(name, checks, nums, ingredients)
    return {
        "product_name": name,
        "front_claims": [c["claim"] for c in checks],
        "ingredients": ingredients,
        "sugar_g_per_100g": nums["sugar_g_per_100g"],
        "net_weight_g": nums["net_weight_g"],
        "sodium_mg_per_100g": nums["sodium_mg_per_100g"],
        "claim_checks": checks,
        "summary_en": summary_en,
        "summary_hi": summary_hi,
    }
