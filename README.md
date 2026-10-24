# LabelLie 🔍

**What is the brand hiding?** Scan the front and back of any food packet. LabelLie checks the brand's front claims against the real ingredient list, shows the whole packet's sugar in teaspoons, decodes INS codes, and reads the verdict aloud in Hindi. It runs fully offline on Gemma 4.

Built at **HackFest 2.0**, Track 2: Best Use of Gemma 4.

**Team:** Sayed Afia · Ryaan Shah · Anjala Kumari

## Features

- **Front vs Back check:** every front claim ("real fruit", "no preservatives") marked True, Misleading or False, with the reason from the back label
- **Whole-packet sugar:** "this packet = 8 teaspoons", calculated in code from the printed numbers
- **Code-word decoder:** INS numbers and hidden names (maltodextrin, refined wheat flour) explained in plain words, with sources
- **Hindi voice:** the verdict read aloud
- **Share card:** one tap turns the verdict into an image
- **Decode a code:** type any INS / E number and get what it is, whether it is vegetarian, and its regulatory status
- **Safe for whom?:** pick who is eating (pregnant, child, elderly, diabetic, asthma) and get Avoid / Limit / Check / OK with reasons, from simple rules in `main.py`

## How the AI works

| Job | Who does it |
| --- | --- |
| Read both label photos: claims, ingredients, sugar, weight, verdict per claim, English + Hindi summary | **Gemma 4** (online via Google AI Studio, or offline via Ollama) |
| Read the photos if Gemma has no key or fails | **Claude** (vision), so every scan is still read by AI |
| Emergency backup when no AI key works | On-phone text scanner (Tesseract.js) + rules in `rules.py`, or type the ingredients |
| Explain known INS / E codes, with vegetarian status and source | **`additives.json`** (56 checked codes + hidden-sugar names) |
| Explain any code not in the list (e.g. rare candy colours), marked "AI answer" | **Claude** (`claude-haiku-5-5`), or Gemma if no Claude key |
| Sugar in teaspoons, "Safe for whom?" levels, overall verdict | **Python rules** in `main.py`, so numbers are never guessed |

The **Decode a code** box on the home screen lets anyone type codes from a packet (for example `INS 133, 129, 904` from a candy pack) and get plain-language answers.

## Run it

**Easiest (Windows):** double-click `app/start.bat`. Paste your Gemma key from https://aistudio.google.com/apikey when asked, and optionally a Claude key from https://console.anthropic.com. The app opens in your browser.

**Option A, online Gemma (no Ollama):** get a free API key at https://aistudio.google.com/apikey, then:

```bash
cd app
pip install -r requirements.txt
set GEMINI_API_KEY=your-key        # Windows (Mac/Linux: export GEMINI_API_KEY=your-key)
uvicorn main:app --host 0.0.0.0 --port 8000
```

**Option B, offline Gemma with Ollama:**

```bash
# 1. Install Ollama from https://ollama.com, then:
ollama pull gemma4:e4b          # use gemma4:e2b on a slower laptop

# 2. Install and start the app
cd app
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000
```

Open http://localhost:8000, or http://YOUR-LAPTOP-IP:8000 on a phone on the same Wi-Fi.
If Gemma is not running, the app shows a sample result marked "Demo data".

## Online version (no server, no keys)

LabelLie also runs as a hosted page on Claude, where Claude reads the label photos directly. `online/host-engine.js` holds the same facts, sugar maths and "Safe for whom?" rules as `main.py`, and `app/index.html` uses it automatically when it is present.

## Project structure

```
labellie/
├── app/
│   ├── main.py            FastAPI backend: Gemma 4 + Claude, builds the verdict
│   ├── rules.py           Backup label checker that needs no AI
│   ├── start.bat          One-click start on Windows
│   ├── index.html         Animated mobile frontend
│   ├── additives.json     Sourced facts for INS codes and hidden ingredients
│   └── requirements.txt
├── online/
│   └── host-engine.js     Same rules in JavaScript for the hosted version
├── pitch/
│   ├── labellie-pitch.html   Animated presentation (open in Chrome)
│   └── LabelLie-Pitch.pptx   PowerPoint version with speaker notes
└── README.md
```

## Tech stack

Gemma 4 · Claude · Python · FastAPI · HTML · CSS · JavaScript · Web Speech API · Canvas

## Disclaimer

LabelLie is for awareness, not medical advice. Additive facts come from FSSAI, EFSA, WHO and EU sources listed in `additives.json`.

## Acknowledgements

Planning, code drafts and pitch materials were made with help from Claude (Anthropic) as a development assistant. In the app, Gemma 4 reads the labels and Claude explains codes that are not in our facts list.
