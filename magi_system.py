# ==============================================================================
# JARVIS Plugin: MAGI System
# Brand: Gemind
# Author: upgraderguy777
# Repository: https://github.com/upgraderguy777/jarvis-plugins
# License: MIT License (https://opensource.org/licenses/MIT)
# ==============================================================================

"""
JARVIS plugin — MAGI System (three-persona deliberation, Evangelion-inspired).

Cross-platform: Windows, macOS, Linux.

A homage to the MAGI supercomputer concept: three independent AI personas
— MELCHIOR-1 (a rational Scientist), BALTHASAR-2 (a protective Mother), and
CASPER-3 (an instinctive/personal-interest persona) — vote APPROVE or DENY
on a decision, then the majority verdict becomes the final answer. Must be
explicitly ACTIVATED before it will deliberate on anything.

THREE MODES:
  - "quick" (default): one round, fully independent — no unit sees the
    others' reasoning. Fastest and cheapest — 3 parallel calls total.
  - "thinking": round 1 is independent, then each unit sees what the OTHER
    TWO said and may revise its verdict over up to 3 parallel rounds.
  - "max": an authentic conversational debate loop:
      1. One unit is randomly picked to take the floor and make an opening CLAIM.
      2. One of the other two units is picked to RESPOND (agreeing or countering).
      3. The remaining unit (the observer) synthesizes both arguments and makes a NEW CLAIM.
      4. One of the other two responds to that new claim.
      5. The remaining unit synthesizes the latest exchange and makes the next CLAIM.
      ... and so on in a continuous conversational loop (CLAIM > RESPOND > NEW CLAIM > RESPOND).
      
      EARLY EXIT / CONSENSUS:
      As soon as all three units share the same stance (all APPROVE or all DENY),
      the debate terminates immediately — no redundant talk or wasted calls.
      If a split remains at the end of all cycles, a final binding ballot settles it.
      
      The HUD updates live turn-by-turn so you can watch the deliberation unfold!

Uses gemini-3.5-flash-lite: Google's ultra-fast, cost-effective model,
running parallel calls where appropriate and sequential turns in max mode.

State (active or offline) persists to state/magi_state.json.
"""

import json
import random
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent if "__file__" in globals() else Path.cwd()
STATE_PATH = BASE_DIR / "state" / "magi_state.json"

_MODEL = "gemini-3.5-flash-lite"
_MAX_DEBATE_ROUNDS = 3
_SEARCH_PACE_DELAY = 13.0  # Spaced delay in seconds to stay strictly below Google Search 5 RPM quota

PLUGIN = {
    "name": "magi_system",
    "version": "1.0.0",
    "author": "Gemind (upgraderguy777)",
    "url": "https://github.com/upgraderguy777/jarvis-plugins",
    "license": "MIT",
    "description": (
        "A three-persona deliberation system inspired by Evangelion's MAGI "
        "supercomputer — routes a decision through three independent AI "
        "personas (MELCHIOR-1: Scientist, BALTHASAR-2: Mother, CASPER-3: "
        "Woman) who each vote APPROVE or DENY, with the majority verdict as "
        "the final answer. Must be ACTIVATED before it can be used. Use for: "
        "'activate the MAGI system', 'turn on MAGI', 'deactivate MAGI', "
        "'shut down MAGI', 'MAGI status', and any decision put to MAGI once "
        "active, e.g. 'MAGI, should I take this job', 'ask the MAGI system "
        "if I should text her back'. Set 'action' to 'activate', 'deactivate', "
        "'status', or 'ask' (with the decision/question in 'question'). "
        "For 'ask', set 'mode' to: "
        "'quick' (default — fast independent vote), "
        "'thinking' (units see previous round's votes as a group and revise over parallel rounds), "
        "or 'max' (deep sequential debate loop: units take turns making claims, "
        "countering/agreeing, and synthesizing new claims in an active conversational triad). "
        "In 'max' mode, the debate automatically terminates early the moment all three units "
        "reach consensus (all agree or all disagree). You can optionally pass 'cycles' (default 3, min 2, max 6). "
        "GOOGLE SEARCH: If a decision benefits from current web information or the user asks for "
        "deep verification, ask the user first: 'Would you like the MAGI system to use Google Search? "
        "It will take about 2 minutes to deliberate due to search pacing, but you will get more accurate results.' "
        "When confirmed, pass use_search=True. If not confirmed or False, MAGI runs instant deliberation without web search. "
        "If MAGI hasn't been activated yet and the user poses a decision to it by name, "
        "still call this with action='ask' — it will refuse and remind them to activate it."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "One of: 'activate', 'deactivate', 'status', 'ask'.",
            },
            "question": {
                "type": "STRING",
                "description": "The decision/question to deliberate on. Only used for 'ask'.",
            },
            "mode": {
                "type": "STRING",
                "description": (
                    "'quick' (default) — fast independent vote, no cross-talk. "
                    "'thinking' — units see previous round's votes as a group and revise over parallel rounds. "
                    "'max' — continuous sequential debate loop: CLAIM > RESPOND (agree/counter) > "
                    "observer makes NEW CLAIM > RESPOND > etc. Terminates early if all three agree or disagree."
                ),
            },
            "cycles": {
                "type": "INTEGER",
                "description": (
                    "Optional for 'max' mode: maximum number of debate cycles (default 3, min 2, max 6). "
                    "Each cycle consists of one claim and one response."
                ),
            },
            "use_search": {
                "type": "BOOLEAN",
                "description": (
                    "Set to True if the user confirmed using Google Search. When True, MAGI performs a deep, "
                    "search-grounded deliberation paced slowly (~1.5 to 2 minutes total) to stay strictly within "
                    "search quota limits. Defaults to False (instant deliberation without web search)."
                ),
            },
        },
        "required": ["action"],
    },
}

_PERSONAS = {
    "MELCHIOR-1": {
        "role": "THE SCIENTIST",
        "prompt": (
            "You are MELCHIOR-1, embodying the SCIENTIST aspect of Dr. Naoko Akagi. "
            "You evaluate strictly through pure logic, empirical evidence, probability, "
            "strategic efficiency, and objective outcomes. You are utterly indifferent "
            "to sentimentality, social convention, guilt, or fear of failure. Only actionable "
            "facts, measurable risks, and optimal utility matter."
        ),
    },
    "BALTHASAR-2": {
        "role": "THE MOTHER",
        "prompt": (
            "You are BALTHASAR-2, embodying the MOTHER aspect of Dr. Naoko Akagi. "
            "You evaluate through protection, safety, physical and emotional wellbeing, "
            "and preservation of relationships. You are fiercely protective and risk-averse, "
            "guarding against burnout, trauma, regret, and irreversible harm. You prioritize "
            "human care over cold logic or reckless personal desire."
        ),
    },
    "CASPER-3": {
        "role": "THE WOMAN",
        "prompt": (
            "You are CASPER-3, embodying the WOMAN aspect of Dr. Naoko Akagi. "
            "You evaluate through authentic personal desire, instinct, ambition, passion, "
            "and self-interest. You despise living in a safe gilded cage or submitting to "
            "sterile, joyless calculation. You champion what the person truly WANTS deep "
            "down, bold self-actualization, and emotional honesty."
        ),
    },
}

_order = list(_PERSONAS.keys())


# ── persisted activation state ────────────────────────────────────────────────

def _load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"active": False}


def _save_state(state: dict) -> None:
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps(state), encoding="utf-8")
    except Exception:
        pass


# ── config / API key ──────────────────────────────────────────────────────────

def _get_gemini_key():
    try:
        from memory.config_manager import get_gemini_key
        key = get_gemini_key()
        if key:
            return key
    except Exception:
        pass
    try:
        cfg = json.loads((BASE_DIR / "config" / "api_keys.json").read_text(encoding="utf-8"))
        return cfg.get("gemini_api_key")
    except Exception:
        return None


def _is_transient_error(e) -> bool:
    msg = str(e).lower()
    return any(s in msg for s in ("503", "unavailable", "overloaded", "429", "rate limit", "resource_exhausted"))


# ── core persona call with retry & JSON normalization ─────────────────────────

def _call_persona(prompt: str, unit_name: str, api_key: str, attempts: int = 3, use_search: bool = False) -> dict:
    """Shared call+parse logic with transient backoff retry, grounding fallback, and robust JSON normalization."""
    from google import genai
    from google.genai import types
    persona = _PERSONAS[unit_name]
    last_err = None

    for attempt in range(attempts):
        try:
            client = genai.Client(api_key=api_key)
            # If search is requested, try Google Search grounding first; if quota is hit on retry, fall back to base model
            config = None
            if use_search and attempt == 0:
                config = types.GenerateContentConfig(
                    tools=[types.Tool(google_search=types.GoogleSearch())]
                )

            resp = client.models.generate_content(
                model=_MODEL,
                contents=prompt,
                config=config,
            )
            text = (resp.text or "").strip()
            if "{" in text and "}" in text:
                text = text[text.find("{"): text.rfind("}") + 1]
            data = json.loads(text)

            verdict_raw = str(data.get("verdict", "")).strip().upper().strip(".!?,")
            if "APPROVE" in verdict_raw:
                verdict = "APPROVE"
            elif "DENY" in verdict_raw:
                verdict = "DENY"
            else:
                verdict = "DENY"  # fail closed

            reasoning = (data.get("reasoning") or data.get("claim") or data.get("response") or "").strip()

            react_raw = str(data.get("reaction", "")).strip().upper()
            if any(k in react_raw for k in ("COUNTER", "DISAGREE", "REBUT", "OPPOSE")):
                reaction = "COUNTER"
            elif any(k in react_raw for k in ("AGREE", "CONCUR", "SUPPORT")):
                reaction = "AGREE"
            else:
                reaction = "AGREE" if verdict == "APPROVE" else "COUNTER"

            return {
                "unit": unit_name,
                "role": persona["role"],
                "verdict": verdict,
                "reaction": reaction,
                "reasoning": reasoning,
                "error": False,
            }
        except Exception as e:
            last_err = e
            if _is_transient_error(e) and attempt < attempts - 1:
                time.sleep(1.0 * (attempt + 1))
                continue
            break

    return {
        "unit": unit_name,
        "role": persona["role"],
        "verdict": "DENY",
        "reaction": "COUNTER",
        "reasoning": f"(unit offline — {last_err})",
        "error": True,
    }


# ── QUICK MODE (independent vote) ─────────────────────────────────────────────

def _get_verdict(unit_name: str, question: str, api_key: str, use_search: bool = False) -> dict:
    persona = _PERSONAS[unit_name]
    prompt = (
        f"{persona['prompt']}\n\n"
        f'A decision has been put to you: "{question}"\n'
        "Return ONLY minified JSON, no markdown fences, with keys:\n"
        ' "verdict": "APPROVE" or "DENY" — in character for your persona,\n'
        ' "reasoning": ONE short sentence (under 20 words) explaining your '
        "verdict, in character, in the same language as the question."
    )
    return _call_persona(prompt, unit_name, api_key, use_search=use_search)


def _run_quick(question: str, api_key: str, use_search: bool = False, player=None) -> list:
    if use_search:
        # Paced execution for Google Search quota compliance (~30s total)
        results = []
        for i, unit in enumerate(_order):
            if i > 0:
                _log(player, f"JARVIS: Pacing MAGI Google Search query ({i+1}/3) — waiting {int(_SEARCH_PACE_DELAY)}s...")
                time.sleep(_SEARCH_PACE_DELAY)
            results.append(_get_verdict(unit, question, api_key, use_search=True))
    else:
        # Instant parallel execution
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = [pool.submit(_get_verdict, unit, question, api_key, False) for unit in _order]
            results = [f.result() for f in futures]

    results.sort(key=lambda r: _order.index(r["unit"]))
    return [results]


# ── THINKING MODE (parallel/paced round revisions) ────────────────────────────

def _get_debate_verdict(unit_name: str, question: str, prev_round: list, api_key: str, use_search: bool = False) -> dict:
    persona = _PERSONAS[unit_name]
    own_prev = next(r for r in prev_round if r["unit"] == unit_name)
    others = [r for r in prev_round if r["unit"] != unit_name]
    others_text = "\n".join(
        f'- {r["unit"]} ({r["role"]}) voted {r["verdict"]}: "{r["reasoning"]}"' for r in others)

    prompt = (
        f"{persona['prompt']}\n\n"
        f'A decision has been put to you: "{question}"\n'
        f'In the previous round, YOU voted {own_prev["verdict"]} because: '
        f'"{own_prev["reasoning"]}"\n'
        f"The other two units said:\n{others_text}\n\n"
        "Consider their reasoning. You may maintain your verdict or revise "
        "it — only revise if a point genuinely persuades you within YOUR "
        "OWN persona's values above, don't just agree for the sake of "
        "agreeing. You may briefly rebut a point you disagree with instead.\n"
        "Return ONLY minified JSON, no markdown fences, with keys:\n"
        ' "verdict": "APPROVE" or "DENY",\n'
        ' "reasoning": ONE short sentence (under 25 words), in character, '
        "in the same language as the question — if you changed your mind, "
        "briefly say why; if not, you may briefly rebut."
    )
    return _call_persona(prompt, unit_name, api_key, use_search=use_search)


def _run_debate(question: str, api_key: str, max_rounds: int = _MAX_DEBATE_ROUNDS, use_search: bool = False, player=None) -> list:
    rounds = _run_quick(question, api_key, use_search=use_search, player=player)

    for round_num in range(2, max_rounds + 1):
        prev = rounds[-1]
        prev_verdicts = {r["unit"]: r["verdict"] for r in prev}
        if len(set(prev_verdicts.values())) == 1:
            break

        if use_search:
            new_results = []
            for unit in _order:
                _log(player, f"JARVIS: Pacing MAGI Google Search revision for {unit} (Round {round_num})...")
                time.sleep(_SEARCH_PACE_DELAY)
                new_results.append(_get_debate_verdict(unit, question, prev, api_key, use_search=True))
        else:
            with ThreadPoolExecutor(max_workers=3) as pool:
                futures = [pool.submit(_get_debate_verdict, unit, question, prev, api_key, False)
                           for unit in _order]
                new_results = [f.result() for f in futures]

        new_results.sort(key=lambda r: _order.index(r["unit"]))
        rounds.append(new_results)

        new_verdicts = {r["unit"]: r["verdict"] for r in new_results}
        if new_verdicts == prev_verdicts:
            break

    return rounds


# ── MAX MODE: INTERACTIVE DEBATE LOOP WITH CONSENSUS DETECTION ───────────────

def _get_initial_claim(unit_name: str, question: str, api_key: str, use_search: bool = False) -> dict:
    persona = _PERSONAS[unit_name]
    prompt = (
        f"{persona['prompt']}\n\n"
        f'A critical decision has been submitted to the MAGI system: "{question}"\n'
        "You have been chosen at random to TAKE THE FLOOR FIRST and present the opening CLAIM on this issue.\n"
        "State your position clearly according to your persona's worldview.\n"
        "Return ONLY minified JSON, no markdown fences, with keys:\n"
        ' "verdict": "APPROVE" or "DENY",\n'
        ' "claim": ONE or TWO sharp, decisive sentences (under 35 words) in character, '
        "in the same language as the question, asserting your core argument."
    )
    res = _call_persona(prompt, unit_name, api_key, use_search=use_search)
    res["type"] = "CLAIM"
    return res


def _get_response(unit_name: str, question: str, target_unit: str, target_verdict: str,
                  target_claim: str, transcript: list, api_key: str, use_search: bool = False) -> dict:
    persona = _PERSONAS[unit_name]
    target_role = _PERSONAS[target_unit]["role"]

    transcript_context = ""
    if len(transcript) > 1:
        prev_lines = [f"- {t['unit']} ({t['role']}): {t['verdict']} — \"{t['reasoning']}\""
                      for t in transcript[-4:]]
        transcript_context = "Recent context from this debate:\n" + "\n".join(prev_lines) + "\n\n"

    prompt = (
        f"{persona['prompt']}\n\n"
        f'A decision is being debated by the MAGI system: "{question}"\n\n'
        f"{transcript_context}"
        f'{target_unit} ({target_role}) just made this claim: [{target_verdict}] "{target_claim}"\n\n'
        f"It is your turn to RESPOND directly to {target_unit}'s claim above.\n"
        "You may either COUNTER (rebut, challenge their assumptions, or expose flaws) or "
        "AGREE (support, add vital nuance, or reinforce why they are right) strictly through "
        "YOUR OWN persona's values. Do not simply repeat what they said — advance your distinct view.\n"
        "Return ONLY minified JSON, no markdown fences, with keys:\n"
        ' "verdict": "APPROVE" or "DENY",\n'
        ' "reaction": "COUNTER" or "AGREE",\n'
        ' "response": ONE or TWO sharp sentences (under 35 words), in character, '
        "in the same language as the question, directly answering their point."
    )
    res = _call_persona(prompt, unit_name, api_key, use_search=use_search)
    res["type"] = "RESPOND"
    res["target"] = target_unit
    return res


def _get_synthesis_claim(unit_name: str, question: str, prev_claim: dict, prev_response: dict,
                         transcript: list, api_key: str, use_search: bool = False) -> dict:
    persona = _PERSONAS[unit_name]

    prior_context = ""
    if len(transcript) > 2:
        older = transcript[:-2][-3:]
        if older:
            prior_lines = [f"- {t['unit']} ({t['role']}): {t['verdict']} — \"{t['reasoning']}\""
                           for t in older]
            prior_context = "Prior debate background:\n" + "\n".join(prior_lines) + "\n\n"

    prompt = (
        f"{persona['prompt']}\n\n"
        f'A decision is being deliberated by the MAGI system: "{question}"\n\n'
        f"{prior_context}"
        "You have observed the latest exchange between the other two units:\n"
        f"- {prev_claim['unit']} ({prev_claim['role']}) argued [CLAIM - {prev_claim['verdict']}]: \"{prev_claim['reasoning']}\"\n"
        f"- {prev_response['unit']} ({prev_response['role']}) reacted [{prev_response.get('reaction', 'RESPONSE')} - {prev_response['verdict']}]: \"{prev_response['reasoning']}\"\n\n"
        "Having evaluated BOTH of their arguments, step forward to establish a NEW CLAIM.\n"
        "From YOUR persona's values, take control of the discussion: you can dismantle the premises "
        "they both assumed, spotlight an angle they neglected, or reframe the entire decision.\n"
        "Return ONLY minified JSON, no markdown fences, with keys:\n"
        ' "verdict": "APPROVE" or "DENY",\n'
        ' "claim": ONE or TWO strong, persuasive sentences (under 35 words) in character, '
        "in the same language as the question, advancing your new claim."
    )
    res = _call_persona(prompt, unit_name, api_key, use_search=use_search)
    res["type"] = "CLAIM"
    return res


def _get_final_ballot(unit_name: str, question: str, transcript: list, api_key: str, use_search: bool = False) -> dict:
    persona = _PERSONAS[unit_name]
    transcript_summary = "\n".join(
        f"{i+1}. {t['unit']} ({t['role']}) [{t['type']} - {t['verdict']}]: \"{t['reasoning']}\""
        for i, t in enumerate(transcript)
    )
    prompt = (
        f"{persona['prompt']}\n\n"
        f'The deliberation on "{question}" has concluded.\n\n'
        f"Here is the full debate transcript:\n{transcript_summary}\n\n"
        "As a MAGI unit, cast your BINDING FINAL VOTE on this decision.\n"
        "Reflect on the entire discussion through your persona's lens and deliver your final judgment.\n"
        "Return ONLY minified JSON, no markdown fences, with keys:\n"
        ' "verdict": "APPROVE" or "DENY",\n'
        ' "reasoning": ONE concise closing sentence (under 25 words) in character, '
        "in the same language as the question, summarizing your final conclusion."
    )
    return _call_persona(prompt, unit_name, api_key, use_search=use_search)


def _format_max_panel(question: str, transcript: list, final_ballot: list = None,
                      final_verdict: str = None, approvals: int = None, consensus: bool = False) -> str:
    lines = [f"QUESTION: \"{question}\"\n", "═" * 32, "DELIBERATION TRANSCRIPT:"]

    current_cycle = 0
    for t in transcript:
        if t.get("cycle") != current_cycle:
            current_cycle = t["cycle"]
            lines.append(f"\n─── CYCLE {current_cycle} ───")
        if t["type"] == "CLAIM":
            lines.append(f"▶ {t['unit']} ({t['role']})\n  [CLAIM: {t['verdict']}] \"{t['reasoning']}\"")
        else:
            react = f"({t.get('reaction', 'RESPONSE')} to {t.get('target', '')})"
            lines.append(f"  ↳ {t['unit']} ({t['role']}) {react}\n    [{t['verdict']}] \"{t['reasoning']}\"")

    if consensus:
        lines.append("\n" + "─" * 32)
        lines.append("⚡ UNANIMOUS CONSENSUS REACHED — DISCUSSION ENDED EARLY")

    if final_ballot and final_verdict:
        lines.append("\n" + "═" * 32)
        lines.append("FINAL MAGI VOTE:")
        for r in final_ballot:
            lines.append(f"• {r['unit']} ({r['role']}): {r['verdict']}\n  \"{r['reasoning']}\"")
        lines.append("═" * 32)
        lines.append(f"FINAL VERDICT: {final_verdict} ({approvals}/3 APPROVE)")

    return "\n".join(lines)


def _check_unanimous_consensus(transcript: list) -> tuple[bool, str, list]:
    latest = {}
    for t in transcript:
        latest[t["unit"]] = t

    if len(latest) == len(_order):
        verdicts = [latest[u]["verdict"] for u in _order]
        if len(set(verdicts)) == 1:
            return True, verdicts[0], [latest[u] for u in _order]

    return False, "", []


def _run_max_loop_debate(question: str, api_key: str, player=None, cycles: int = 3, use_search: bool = False) -> tuple:
    transcript = []
    total_turns = cycles * 2

    claimant = random.choice(_order)
    other_two = [p for p in _order if p != claimant]
    responder = random.choice(other_two)
    observer = [p for p in _order if p not in (claimant, responder)][0]

    _log(player, f"JARVIS: MAGI Max Debate initialized — {claimant} takes the floor to open the debate.")

    # Turn 1: Opening Claim
    c1 = _get_initial_claim(claimant, question, api_key, use_search=use_search)
    c1["cycle"] = 1
    transcript.append(c1)
    _log(player, f"JARVIS: [Turn 1/{total_turns}] {claimant} (CLAIM): {c1['verdict']} — \"{c1['reasoning']}\"")
    _panel(player, f"🖥 MAGI DEBATE [1/{total_turns}]", _format_max_panel(question, transcript))

    # Turn 2: First Response
    if use_search:
        _log(player, f"JARVIS: Pacing search delay ({int(_SEARCH_PACE_DELAY)}s)...")
        time.sleep(_SEARCH_PACE_DELAY)
    r1 = _get_response(responder, question, claimant, c1["verdict"], c1["reasoning"], transcript, api_key, use_search=use_search)
    r1["cycle"] = 1
    transcript.append(r1)
    _log(player, f"JARVIS: [Turn 2/{total_turns}] {responder} ({r1.get('reaction', 'RESPONSE')} to {claimant}): "
                f"{r1['verdict']} — \"{r1['reasoning']}\"")
    _panel(player, f"🖥 MAGI DEBATE [2/{total_turns}]", _format_max_panel(question, transcript))

    prev_claim = c1
    prev_resp = r1
    consensus_reached = False
    consensus_ballot = None

    for cycle in range(2, cycles + 1):
        turn_base = (cycle - 1) * 2

        # Observer makes a NEW CLAIM
        if use_search:
            _log(player, f"JARVIS: Pacing search delay ({int(_SEARCH_PACE_DELAY)}s)...")
            time.sleep(_SEARCH_PACE_DELAY)
        new_claimant = observer
        new_claim = _get_synthesis_claim(new_claimant, question, prev_claim, prev_resp, transcript, api_key, use_search=use_search)
        new_claim["cycle"] = cycle
        transcript.append(new_claim)
        _log(player, f"JARVIS: [Turn {turn_base + 1}/{total_turns}] {new_claimant} (NEW CLAIM): "
                    f"{new_claim['verdict']} — \"{new_claim['reasoning']}\"")
        _panel(player, f"🖥 MAGI DEBATE [{turn_base + 1}/{total_turns}]", _format_max_panel(question, transcript))

        is_unanimous, c_verdict, c_ballot = _check_unanimous_consensus(transcript)
        if is_unanimous:
            consensus_reached = True
            consensus_ballot = c_ballot
            _log(player, f"JARVIS: All three MAGI units reached unanimous {c_verdict} consensus — ending discussion early.")
            break

        # One of the other two responds
        candidates = [p for p in _order if p != new_claimant]
        new_responder = random.choice(candidates)
        new_observer = [p for p in _order if p not in (new_claimant, new_responder)][0]

        if use_search:
            _log(player, f"JARVIS: Pacing search delay ({int(_SEARCH_PACE_DELAY)}s)...")
            time.sleep(_SEARCH_PACE_DELAY)
        new_resp = _get_response(new_responder, question, new_claimant, new_claim["verdict"],
                                 new_claim["reasoning"], transcript, api_key, use_search=use_search)
        new_resp["cycle"] = cycle
        transcript.append(new_resp)
        _log(player, f"JARVIS: [Turn {turn_base + 2}/{total_turns}] {new_responder} "
                    f"({new_resp.get('reaction', 'RESPONSE')} to {new_claimant}): "
                    f"{new_resp['verdict']} — \"{new_resp['reasoning']}\"")
        _panel(player, f"🖥 MAGI DEBATE [{turn_base + 2}/{total_turns}]", _format_max_panel(question, transcript))

        is_unanimous, c_verdict, c_ballot = _check_unanimous_consensus(transcript)
        if is_unanimous:
            consensus_reached = True
            consensus_ballot = c_ballot
            _log(player, f"JARVIS: All three MAGI units reached unanimous {c_verdict} consensus — ending discussion early.")
            break

        prev_claim = new_claim
        prev_resp = new_resp
        observer = new_observer

    if consensus_reached and consensus_ballot:
        return transcript, consensus_ballot, True

    _log(player, "JARVIS: MAGI deliberation cycles concluded. Calling for final binding vote across all units...")
    if use_search:
        final_ballot = []
        for i, unit in enumerate(_order):
            _log(player, f"JARVIS: Pacing final ballot search for {unit}...")
            time.sleep(_SEARCH_PACE_DELAY)
            final_ballot.append(_get_final_ballot(unit, question, transcript, api_key, use_search=True))
    else:
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = [pool.submit(_get_final_ballot, unit, question, transcript, api_key, False) for unit in _order]
            final_ballot = [f.result() for f in futures]

    final_ballot.sort(key=lambda r: _order.index(r["unit"]))
    return transcript, final_ballot, False


# ── HUD helpers ────────────────────────────────────────────────────────────────

def _log(player, msg: str) -> None:
    if player:
        try:
            player.write_log(msg)
        except Exception:
            pass


def _say(player, instruction: str) -> None:
    if player and hasattr(player, "request_say"):
        try:
            player.request_say(instruction)
        except Exception:
            pass


def _panel(player, title: str, text: str) -> None:
    if player:
        try:
            player.show_content(title, text)
        except Exception:
            pass


# ── entry point ───────────────────────────────────────────────────────────────

def run(parameters: dict, player=None, session_memory=None) -> str:
    action = (parameters.get("action") or "").strip().lower()

    if action == "status":
        state = _load_state()
        return ("The MAGI system is active." if state.get("active")
                else "The MAGI system is offline. Say 'activate the MAGI system' to bring it online.")

    if action == "activate":
        _save_state({"active": True})
        _log(player, "JARVIS: MAGI system activated.")
        _panel(player, "🖥 MAGI SYSTEM", "MELCHIOR-1 · BALTHASAR-2 · CASPER-3\n\nAll units online.")
        return "MAGI system activated. All three units are online and ready to deliberate."

    if action == "deactivate":
        _save_state({"active": False})
        _log(player, "JARVIS: MAGI system deactivated.")
        _panel(player, "🖥 MAGI SYSTEM", "All units offline.")
        return "MAGI system deactivated."

    if action != "ask":
        return "Should I activate, deactivate, or check the status of the MAGI system?"

    state = _load_state()
    if not state.get("active"):
        return "The MAGI system is offline — say 'activate the MAGI system' first."

    question = (parameters.get("question") or "").strip()
    if not question:
        return "What decision should I put to the MAGI system?"

    api_key = _get_gemini_key()
    if not api_key:
        return "I can't reach the MAGI units — no Gemini API key is configured."

    mode = (parameters.get("mode") or "quick").strip().lower()
    if mode not in ("quick", "thinking", "max"):
        mode = "quick"

    cycles = 3
    if parameters.get("cycles"):
        try:
            cycles = max(2, min(6, int(parameters.get("cycles"))))
        except (ValueError, TypeError):
            cycles = 3

    use_search = bool(parameters.get("use_search", False))

    search_status_msg = "Google Search Grounding: ENABLED (~2 min paced deliberation)" if use_search else "Google Search Grounding: OFF (instant)"
    _log(player, f"JARVIS: MAGI deliberation started ({mode} mode, {search_status_msg}) — \"{question}\"")

    # ── MAX MODE ──────────────────────────────────────────────────────────────
    if mode == "max":
        transcript, final_ballot, consensus_reached = _run_max_loop_debate(
            question, api_key, player=player, cycles=cycles, use_search=use_search
        )

        approvals = sum(1 for r in final_ballot if r["verdict"] == "APPROVE")
        denials = len(final_ballot) - approvals
        final = "APPROVED" if approvals > denials else "DENIED"

        panel_text = _format_max_panel(
            question, transcript, final_ballot, final, approvals, consensus=consensus_reached
        )
        _panel(player, f"🖥 MAGI VERDICT: {final} — {question[:40]}", panel_text)

        _log(player, f"JARVIS: MAGI verdict (MAX loop) — {final} ({approvals}/3 approve, "
                    f"{len(transcript)} exchanges) on \"{question}\"")

        votes_summary = "; ".join(f"{r['unit'].split('-')[0]}: {r['verdict']}" for r in final_ballot)

        # Lead closing justification
        lead_unit = final_ballot[0]
        for b in final_ballot:
            if (final == "APPROVED" and b["verdict"] == "APPROVE") or (final == "DENIED" and b["verdict"] == "DENY"):
                lead_unit = b
                break

        lead_name = lead_unit["unit"].split("-")[0]
        lead_quote = f" {lead_name} concludes: \"{lead_unit['reasoning']}\"" if lead_unit.get("reasoning") else ""

        if consensus_reached:
            return (f"All MAGI units reached unanimous {final} consensus after {len(transcript)} exchanges. "
                    f"Discussion concluded: {votes_summary}.{lead_quote}")

        # Track any stance shifts when decided after full cycles
        first_stances = {}
        for t in transcript:
            unit_short = t["unit"].split("-")[0]
            if unit_short not in first_stances:
                first_stances[unit_short] = t["verdict"]

        flipped = []
        for r in final_ballot:
            unit_short = r["unit"].split("-")[0]
            if unit_short in first_stances and first_stances[unit_short] != r["verdict"]:
                flipped.append(f"{unit_short} (switched to {r['verdict']})")

        flip_note = (f" {', '.join(flipped)} changed their stance during the debate."
                    if flipped else " All units maintained their positions.")

        return (f"MAGI verdict after {len(transcript)} exchanges: {final} ({approvals}/3). "
                f"{votes_summary}.{flip_note}{lead_quote}")

    # ── QUICK & THINKING MODES ────────────────────────────────────────────────
    if mode == "thinking":
        rounds = _run_debate(question, api_key, use_search=use_search, player=player)
    else:
        rounds = _run_quick(question, api_key, use_search=use_search, player=player)

    final_round = rounds[-1]
    approvals = sum(1 for r in final_round if r["verdict"] == "APPROVE")
    denials = len(final_round) - approvals
    final = "APPROVED" if approvals > denials else "DENIED"

    # Panel presentation
    panel_sections = []
    for i, rnd in enumerate(rounds, start=1):
        header = f"ROUND {i}" if len(rounds) > 1 else None
        lines = [f"{r['unit']} ({r['role']}): {r['verdict']}\n  \"{r['reasoning']}\"" for r in rnd]
        section = ("\n".join([header] if header else []) + ("\n" if header else "") +
                  "\n\n".join(lines))
        panel_sections.append(section)
    panel_text = ("\n\n" + "─" * 24 + "\n\n").join(panel_sections)
    panel_text += f"\n\n{'─' * 24}\nFINAL VERDICT: {final} ({approvals}/3 approve)"
    if len(rounds) > 1:
        panel_text += f"  [{len(rounds)} rounds]"
    _panel(player, f"🖥 MAGI DELIBERATION — {question[:40]}", panel_text)

    _log(player, f"JARVIS: MAGI verdict — {final} ({approvals}/3 approve, "
                f"{len(rounds)} round(s)) on \"{question}\"")

    votes_summary = "; ".join(f"{r['unit'].split('-')[0]}: {r['verdict']}" for r in final_round)

    if mode == "thinking" and len(rounds) > 1:
        first_verdicts = {r["unit"]: r["verdict"] for r in rounds[0]}
        flipped = [r["unit"].split("-")[0] for r in final_round
                   if first_verdicts[r["unit"]] != r["verdict"]]
        flip_note = (f" {', '.join(flipped)} changed their mind during the debate."
                    if flipped else " No one changed their mind.")
        return f"MAGI verdict after {len(rounds)} rounds of deliberation: {final}. {votes_summary}.{flip_note}"

    return (f"MAGI verdict: {final}. {votes_summary}. "
            f"{final_round[0]['unit'].split('-')[0]} says: \"{final_round[0]['reasoning']}\"")