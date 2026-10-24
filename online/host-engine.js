/* LabelLie online engine: runs the app with no server.
   Claude (through the page host) reads the label photos; the facts list, sugar maths,
   "Safe for whom?" rules and verdict are the same as the Python backend (main.py). */
(function () {
  'use strict';
  var FACTS = __FACTS__;
  var TSP = 4;
  var samplePromise = null;
  function getSample() {
    if (!samplePromise) {
      samplePromise = (window.claude && window.claude.use) ? window.claude.use('sample') : Promise.resolve(null);
    }
    return samplePromise;
  }
  function fail(kind, message) { var e = new Error(message); e.kind = kind; return e; }
  function aiError(e) {
    var code = e && e.code;
    if (code === 'not_granted' || code === 'sampling_disabled') return fail('consent', 'Claude was not allowed for this page.');
    if (code === 'rate_limited') return fail('host', 'Too many requests right now. Wait a minute and try again.');
    if (code === 'image_rejected' || code === 'images_unavailable') return fail('host', 'This photo could not be used. Try a JPG or PNG photo.');
    if (code === 'invalid_json') return fail('host', 'The AI answer was not in the expected format. Try again.');
    return fail('host', (e && e.message) || 'The AI did not answer.');
  }

  var LABEL_PROMPT = 'You are a food-label analyst for Indian shoppers.\n{NOTE}\n' +
    'Read ONLY what is printed. Reply with ONLY one JSON object in exactly this shape:\n' +
    '{"product_name": "string", "front_claims": ["each marketing claim on the front"],\n' +
    ' "ingredients": ["each ingredient in label order, copied exactly, keep INS / E codes and % like \'Mango pulp (2%)\' or \'Colour (INS 102, 133)\'"],\n' +
    ' "sugar_g_per_100g": number or null, "net_weight_g": number or null, "sodium_mg_per_100g": number or null,\n' +
    ' "claim_checks": [{"claim": "string", "verdict": "True" or "Misleading" or "False", "reason": "one short sentence using facts from the back label"}],\n' +
    ' "summary_en": "2 short, simple sentences for a common person",\n' +
    ' "summary_hi": "the same 2 sentences in simple Hindi (Devanagari script)"}\n' +
    'Rules:\n- A claim is "Misleading" if technically true but the ingredients tell a different story (example: \'real fruit\' but fruit is only 2% and sugar is near the top of the list).\n' +
    '- A claim is "False" only if the back label clearly contradicts it.\n' +
    '- If there is no front information, front_claims and claim_checks are empty lists.\n' +
    '- If a number is not visible, use null. For ml products, treat 1 ml as 1 g.\n' +
    '- If the image is not a food label, return "ingredients": [].';

  var DECODE_PROMPT = 'You explain food additive codes (INS / E numbers) to Indian shoppers.\n' +
    'Reply with ONLY a JSON array, one object per code, like:\n' +
    '[{"code": "INS 133", "name": "common name", "type": "e.g. Artificial colour", "level": "green" or "orange" or "red",\n' +
    '  "fact": "one or two short plain sentences: what it is and its regulatory status (EU / FSSAI / US)",\n' +
    '  "veg": "Vegetarian" or "Non-vegetarian" or "Depends on source", "source": "the regulator or body your fact is based on"}]\n' +
    'Levels: green = generally considered safe; orange = permitted but restricted, needs a warning, or worth limiting; red = banned or withdrawn in major markets.\n' +
    'Be accurate and calm. Never say "causes cancer" or "scam". If you do not recognise a code, say so in "fact" and use level "orange".\nCodes: ';

  // ---------- facts + AI decoding ----------
  var CODE_RE = /(^|[^\d.])(\d{3,4}[a-f]?)(?![\d%.])/gi;
  var cache = {};
  function codesIn(text) {
    var out = [], m;
    CODE_RE.lastIndex = 0;
    while ((m = CODE_RE.exec(text))) {
      var c = m[2].toLowerCase(), n = parseInt(c, 10);
      if (n >= 100 && n <= 1599 && out.indexOf(c) === -1) out.push(c);
    }
    return out;
  }
  function fact(code) {
    var e = FACTS.by_code[code];
    if (!e) return null;
    var o = { code: 'INS ' + code.toUpperCase(), ai: false };
    for (var k in e) o[k] = e[k];
    return o;
  }
  function aiDecode(codes) {
    var todo = codes.filter(function (c) { return !cache[c]; });
    if (!todo.length) return Promise.resolve(codes.map(function (c) { return cache[c]; }).filter(Boolean));
    return getSample().then(function (sample) {
      if (!sample) throw fail('host', 'AI is not available here.');
      return sample.json(DECODE_PROMPT + todo.map(function (c) { return 'INS ' + c.toUpperCase(); }).join(', '), { modelTier: 'quick' });
    }).then(function (items) {
      if (items && !Array.isArray(items)) items = items.codes || items.items || [items];
      (items || []).forEach(function (it) {
        if (!it || typeof it !== 'object') return;
        var key = String(it.code || '').toLowerCase().replace(/^(ins|e)\s*-?\s*/, '').replace(/\s/g, '');
        if (!key) return;
        cache[key] = {
          code: 'INS ' + key.toUpperCase(), name: String(it.name || 'INS ' + key.toUpperCase()), type: String(it.type || 'Food additive'),
          level: ['green', 'orange', 'red'].indexOf(it.level) > -1 ? it.level : 'orange', fact: String(it.fact || ''), veg: String(it.veg || ''),
          source: 'AI explanation (Claude), based on ' + (it.source || 'general knowledge') + '. Please verify.', ai: true
        };
      });
      return codes.map(function (c) { return cache[c]; }).filter(Boolean);
    }, function (e) { throw aiError(e); });
  }
  function findAdditives(ingredients) {
    var text = ingredients.join(' ').toLowerCase(), found = [], unknown = [], seen = {};
    codesIn(text).forEach(function (c) { seen[c] = 1; var f = fact(c); f ? found.push(f) : unknown.push(c); });
    var ai = unknown.length ? aiDecode(unknown).catch(function () {
      return unknown.map(function (c) { return { code: 'INS ' + c.toUpperCase(), name: 'INS ' + c.toUpperCase(), type: 'Not in our list yet', level: 'orange', veg: '', fact: 'We could not look this code up right now.', source: 'LabelLie facts list', ai: false }; });
    }) : Promise.resolve([]);
    return ai.then(function (extra) {
      found = found.concat(extra);
      FACTS.by_name.forEach(function (e) {
        var hit = e.aliases.filter(function (a) { return text.indexOf(a) > -1; })[0];
        if (hit && !seen[e.name]) {
          seen[e.name] = 1;
          var o = { code: hit.replace(/\b\w/g, function (ch) { return ch.toUpperCase(); }), ai: false };
          for (var k in e) if (k !== 'aliases') o[k] = e[k];
          found.push(o);
        }
      });
      var order = { red: 0, orange: 1, green: 2 };
      return found.sort(function (a, b) { return (order[a.level] == null ? 3 : order[a.level]) - (order[b.level] == null ? 3 : order[b.level]); });
    });
  }

  // ---------- "Safe for whom?" (same rules as main.py) ----------
  var AZO = ['102', '104', '110', '122', '124', '129'], SWEET = ['950', '951', '955'], BENZ = ['210', '211', '212', '213'];
  var SULPH = ['220', '221', '222', '223', '224', '225', '226', '227', '228'];
  var SUGARS = ['sugar', 'maltodextrin', 'dextrose', 'glucose', 'invert', 'corn syrup', 'fructose', 'jaggery', 'honey'];
  var RANK = { OK: 0, Check: 1, Limit: 2, Avoid: 3 };
  function safety(ingredients, tsp, sodium) {
    var text = ingredients.join(' ').toLowerCase(), codes = codesIn(text);
    var has = function () { for (var i = 0; i < arguments.length; i++) if (text.indexOf(arguments[i]) > -1) return true; return false; };
    var inter = function (list) { return codes.filter(function (c) { return list.indexOf(c) > -1; }); };
    tsp = tsp || 0;
    var sugars = SUGARS.filter(function (w) { return text.indexOf(w) > -1; });
    var caffeine = has('caffeine', 'coffee', 'cola', 'guarana', 'energy'), alcohol = has('alcohol', 'rum', 'wine', 'liqueur', 'beer');
    var raw = has('unpasteurised', 'unpasteurized', 'raw milk'), trans = has('hydrogenated', 'vanaspati'), azo = inter(AZO).sort();
    var people = [];
    function person(key, name, icon) { var p = { key: key, name: name, icon: icon, level: 'OK', reasons: [] }; people.push(p); return p; }
    function flag(p, level, reason) { if (RANK[level] > RANK[p.level]) p.level = level; if (reason) p.reasons.push(reason); }
    var t = Math.round(tsp * 10) / 10, p;
    p = person('pregnant', 'Pregnant', '🤰');
    if (alcohol) flag(p, 'Avoid', 'Contains alcohol');
    if (raw) flag(p, 'Avoid', 'Unpasteurised ingredient');
    if (caffeine) flag(p, 'Limit', 'Contains caffeine');
    if (tsp >= 6) flag(p, 'Limit', 'High sugar: ' + t + ' teaspoons');
    p = person('child', 'Child', '🧒');
    if (caffeine) flag(p, 'Avoid', 'Contains caffeine');
    if (tsp >= 6) flag(p, 'Avoid', t + " teaspoons of sugar, a full day's limit"); else if (tsp >= 3) flag(p, 'Limit', t + ' teaspoons of sugar');
    if (azo.length) flag(p, 'Limit', 'Colour ' + azo.map(function (c) { return 'INS ' + c; }).join(', ') + ' needs a child warning in the EU');
    if (inter(SWEET).length) flag(p, 'Limit', 'Artificial sweetener');
    p = person('elderly', 'Elderly', '👵');
    if (sodium != null && sodium >= 600) flag(p, 'Limit', 'High salt: ' + sodium + ' mg sodium per 100 g');
    if (trans) flag(p, 'Limit', 'Hydrogenated fat (trans fat risk)');
    if (tsp >= 6) flag(p, 'Limit', 'High sugar: ' + t + ' teaspoons');
    p = person('diabetic', 'Diabetic', '🩸');
    if (tsp >= 4) flag(p, 'Avoid', t + ' teaspoons of sugar'); else if (sugars.length) flag(p, 'Limit', null);
    if (sugars.length) p.reasons.push('Sugars: ' + sugars.join(', '));
    if (has('maida', 'refined wheat flour')) flag(p, 'Limit', 'Maida raises blood sugar fast');
    p = person('asthma', 'Asthma', '🫁');
    if (inter(SULPH).length) flag(p, 'Avoid', 'Sulphites can trigger asthma in sensitive people');
    if (inter(BENZ).length) flag(p, 'Check', 'Benzoate preservative');
    if (azo.length) flag(p, 'Check', 'Azo colour');
    people.forEach(function (q) { if (!q.reasons.length) q.reasons.push('Nothing on our watch list'); });
    return people;
  }

  // ---------- result (same as build_result in main.py) ----------
  function num(v) { var n = parseFloat(v); return isFinite(n) ? n : null; }
  function build(g) {
    var ingredients = (Array.isArray(g.ingredients) ? g.ingredients : []).map(String);
    if (!ingredients.length) return Promise.reject(fail('read', 'No ingredient list found in the photo.'));
    var sugar = num(g.sugar_g_per_100g), weight = num(g.net_weight_g);
    var total = sugar != null && weight ? Math.round(sugar * weight / 100 * 10) / 10 : null;
    var tsp = total != null ? Math.round(total / TSP * 10) / 10 : null;
    var checks = (Array.isArray(g.claim_checks) ? g.claim_checks : []).filter(function (c) { return c && typeof c === 'object'; });
    return findAdditives(ingredients).then(function (adds) {
      var verdicts = checks.map(function (c) { return c.verdict; });
      var reds = adds.filter(function (a) { return a.level === 'red'; }).length;
      var overall = verdicts.indexOf('False') > -1 || reds || (tsp || 0) >= 6 ? 'Exposed'
        : verdicts.indexOf('Misleading') > -1 || adds.some(function (a) { return a.level === 'orange'; }) ? 'Misleading' : 'Honest';
      return {
        product_name: g.product_name || 'This product', engine: 'Claude', overall: overall, claim_checks: checks,
        ingredients: ingredients, additives: adds, sugar_g_per_100g: sugar, net_weight_g: weight, sugar_total_g: total, teaspoons: tsp,
        safety: safety(ingredients, tsp, num(g.sodium_mg_per_100g)), summary_en: g.summary_en || '', summary_hi: g.summary_hi || ''
      };
    });
  }

  // ---------- what the app calls ----------
  window.LabelLieHost = {
    status: function () {
      return getSample().then(function (s) {
        return { gemma_ready: !!s, label_reader: s ? 'Claude (AI, online)' : 'AI not available here', decoder: s ? 'Facts list + Claude' : 'Facts list only', known_codes: Object.keys(FACTS.by_code).length };
      });
    },
    scan: function (front, back) {
      return getSample().then(function (sample) {
        if (!sample) throw fail('host', 'AI is not available in this view. Open the page in Claude.');
        var note = front ? 'Image 1 is the FRONT of the pack. Image 2 is the BACK (ingredients + nutrition table).'
          : 'The image is the BACK of the pack (ingredients + nutrition table).';
        var images = front ? [front, back] : [back];
        return sample.json(LABEL_PROMPT.replace('{NOTE}', note), { images: images }).catch(function (e) { throw aiError(e); });
      }).then(build);
    },
    text: function (backText, frontText) {
      return getSample().then(function (sample) {
        if (!sample) throw fail('host', 'AI is not available in this view.');
        var note = 'The label was typed by the user instead of photographed.\nFRONT OF PACK:\n' + (frontText || '(not given)').slice(0, 3000) +
          '\nBACK OF PACK:\n' + String(backText).slice(0, 8000);
        return sample.json(LABEL_PROMPT.replace('{NOTE}', note), { modelTier: 'quick' }).catch(function (e) { throw aiError(e); });
      }).then(build);
    },
    decode: function (q) {
      var codes = codesIn(String(q).toLowerCase()).slice(0, 10);
      if (!codes.length) return Promise.reject(fail('host', 'Type an INS or E number, for example INS 133 or E171.'));
      var known = [], unknown = [];
      codes.forEach(function (c) { var f = fact(c); f ? known.push(f) : unknown.push(c); });
      return (unknown.length ? aiDecode(unknown) : Promise.resolve([])).then(function (extra) { return { results: known.concat(extra) }; });
    }
  };
})();
