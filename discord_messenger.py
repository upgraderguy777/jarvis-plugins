"""
JARVIS plugin — Discord Messenger (voice → sent DM/message).

The user says something like "message John on Discord and tell him I'm
running late" or "Discord'da Ayşe'ye bugün gelemeyeceğimi söyle" — JARVIS
extracts who to message and what to say, brings the Discord desktop app
to the front, opens the quick-switcher (Ctrl+K) to find the contact or
server, jumps into that conversation, types the message, and — only
after a Gemini vision check confirms the right conversation is open and
the composer contains the right text — sends it.

Real pings ("ping John", "@everyone", "tag the study group") are
handled specially: pasting plain text like "@John" does NOT create a
real Discord ping — it stays grey, undecorated text — unless it's
accepted from Discord's mention autocomplete dropdown. The reserved
words @everyone/@here are the one exception; typed as literal text
they work on their own. Accepting a suggestion is done with Enter —
Discord intercepts Enter to accept the highlighted suggestion whenever
the popup is open, and only falls through to actually SENDING the
message when no popup is showing. That's also how the final send at
the end of the flow works: press Enter once more after everything is
typed and verified. See _type_segments / _parse_request.

Destinations can be a person (DM) OR a server + channel combo, e.g.
"go to [server], go to #general, and say hi" — each hop (server name,
then channel name) is its own Ctrl+K search-and-jump, since Discord's
quick switcher prioritizes channels of whatever server you're
currently in once you've already jumped into it.

Windows + Discord DESKTOP APP only (uses window activation + global
keystrokes, not the Discord API — there is no bot token involved, this
drives the user's own client exactly like the user would).

Why keyboard shortcuts instead of clicking a literal "Find or start
conversation" button: Ctrl+K opens that same search reliably regardless
of window size, theme, or DPI scaling — pixel-coordinate clicking breaks
under any of those. Vision is used for the one place it actually adds
safety: verifying the right person/message is on screen before Enter
is pressed.

Needs: pyautogui, pygetwindow, pyperclip (pip install pyautogui
pygetwindow pyperclip). pyperclip is used instead of raw keystrokes so
non-ASCII text (Turkish names, emoji, accents, etc.) types correctly.
"""

import json
import re
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
_PENDING_PATH = BASE_DIR / "state" / "discord_pending.json"
_PENDING_TTL_S = 300  # a stale pending confirmation older than this is ignored

_YES_WORDS = ("yes", "yeah", "yep", "yup", "correct", "confirm", "confirmed",
              "send", "send it", "go ahead", "do it", "that's right",
              "thats right", "affirmative", "sure", "right")
_NO_WORDS = ("no", "nope", "nah", "cancel", "stop", "don't", "dont",
             "wrong", "incorrect", "abort", "negative")


def _save_pending(destination: str, message: str) -> None:
    try:
        _PENDING_PATH.parent.mkdir(parents=True, exist_ok=True)
        _PENDING_PATH.write_text(
            json.dumps({"destination": destination, "message": message, "ts": time.time()}),
            encoding="utf-8")
    except Exception:
        pass


def _load_pending():
    try:
        data = json.loads(_PENDING_PATH.read_text(encoding="utf-8"))
        if time.time() - data.get("ts", 0) <= _PENDING_TTL_S:
            return data
    except Exception:
        pass
    return None


def _clear_pending() -> None:
    try:
        _PENDING_PATH.unlink()
    except Exception:
        pass


def _classify_yes_no(text: str):
    """Plain keyword match, no Gemini call — this is the whole point of
    the no-vision fallback: confirming shouldn't cost any tokens."""
    t = text.strip().lower().strip(".!?")
    if t in _YES_WORDS or any(t.startswith(w) for w in _YES_WORDS):
        return "yes"
    if t in _NO_WORDS or any(t.startswith(w) for w in _NO_WORDS):
        return "no"
    return None


# ── saved channel aliases (zero-token, zero-ambiguity deep links) ───────────
#
# Discord has a deep-link protocol — discord://discord.com/channels/
# <server_id>/<channel_id> — that jumps straight to an exact channel with
# no search and no ambiguity whatsoever. To save one, in Discord:
# right-click a channel -> Copy Link (or enable Developer Mode in Discord
# Settings -> Advanced, then right-click -> Copy Channel ID and the
# server's Copy Server ID). Then add a line to
# config/discord_channel_aliases.json, e.g.:
#   { "general-chat in gemind": "https://discord.com/channels/123.../456..." }
# The key just needs to loosely resemble how you'll say the channel and
# server together — matching is fuzzy. Any channel with a saved alias
# skips the vision-based search hop entirely: instant and free.

_ALIASES_PATH = BASE_DIR / "config" / "discord_channel_aliases.json"
_ALIAS_MATCH_CUTOFF = 0.6


def _load_aliases() -> dict:
    try:
        return json.loads(_ALIASES_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _normalize_alias_text(s: str) -> str:
    return re.sub(r"[\s_-]+", " ", s.strip().lower())


def _find_alias_link(server: str, channel: str):
    """Fuzzy-matches (server, channel) against saved alias keys so minor
    phrasing differences ('general chat' vs 'general-chat') still hit.
    Returns the saved link/ID string, or None if nothing matches well."""
    aliases = _load_aliases()
    if not aliases:
        return None
    import difflib
    target = _normalize_alias_text(f"{channel} {server}")
    keys = list(aliases.keys())
    norm_keys = [_normalize_alias_text(k) for k in keys]
    match = difflib.get_close_matches(target, norm_keys, n=1, cutoff=_ALIAS_MATCH_CUTOFF)
    if not match:
        return None
    return aliases[keys[norm_keys.index(match[0])]]


def _open_alias_link(link_or_ids: str) -> bool:
    """Extracts the server/channel Discord snowflake IDs from a saved
    alias (a full 'Copy Link' URL or just the two raw IDs) and opens
    Discord's deep-link protocol directly to that channel."""
    ids = re.findall(r"\d{15,25}", link_or_ids)  # Discord IDs are ~17-19 digits
    if len(ids) < 2:
        return False
    guild_id, channel_id = ids[-2], ids[-1]
    try:
        import os
        os.startfile(f"discord://discord.com/channels/{guild_id}/{channel_id}")
        return True
    except Exception:
        return False


PLUGIN = {
    "name": "discord_messenger",
    "description": (
        "Sends a message to a specific person or to a server channel on "
        "the DISCORD DESKTOP APP on the user's behalf, including real "
        "@-pings. Trigger this for ANY request that combines Discord + "
        "someone/somewhere to message + something to say, in any word "
        "order or phrasing, including typos of 'message' or 'discord' "
        "(e.g. 'messange'). Examples of phrasings that must ALL route "
        "here: 'message John on Discord and tell him I'm running late', "
        "'messange bob in discord and say hi to him', 'DM bob hi', 'tell "
        "bob hi on discord', 'open discord then tell bob hi', 'discord'da "
        "Ayşe'ye ... yaz', 'go to the study group server, go to general "
        "chat, and say practice moved', 'hey go to [server] then go to "
        "general chat and say whats up bob with a ping', 'ping everyone "
        "in the server and say practice moved', 'tag Sarah and ask if "
        "she's coming', '@everyone ...'. The recipient can be named "
        "before OR after the message content, and 'discord' may appear "
        "anywhere in the sentence or not at all if context makes it "
        "obvious a Discord message is meant. Pass the user's full spoken "
        "request verbatim in 'request' — it must contain who/where to "
        "message and what to say; do not paraphrase or clean it up "
        "first, this tool's own parsing handles typos and word order. "
        "Do NOT use this for reading messages, only for composing and "
        "sending a new one. ALSO call this tool (with the user's reply "
        "verbatim as 'request') when the user gives a short yes/no/"
        "confirm/cancel answer right after this tool asked them to "
        "confirm a pending Discord send — e.g. a bare 'yes', 'send it', "
        "'no', 'cancel'."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "request": {
                "type": "STRING",
                "description": (
                    "The user's full request verbatim, in their own "
                    "language, containing both the recipient and the "
                    "message content."
                ),
            }
        },
        "required": ["request"],
    },
}

_TEXT_MODEL = "gemini-flash-latest"
_VISION_MODEL = "gemini-2.5-flash"

_RETRY_ATTEMPTS = 3
_RETRY_BASE_DELAY_S = 1.5

_MAX_SCREENSHOT_WIDTH = 960   # downscaled before sending to Gemini — the
                              # vision check only needs to read UI text/
                              # labels, not full display resolution, and
                              # image tokens scale with pixel count, so
                              # this alone cuts that call's token cost a lot.

# Flip to True to skip the vision safety check entirely (saves a whole
# Gemini vision call — screenshots are the most token-expensive part of
# this plugin). When skipped, the message is typed but NOT auto-sent:
# JARVIS leaves it in the composer and asks the user to glance at it and
# press Enter themselves. Safer than either auto-sending unverified or
# giving up outright.
SKIP_VISION_CHECK = False

_BLANK_STDDEV_THRESHOLD = 10   # below this, a screenshot is treated as a
                               # near-solid-color loading/splash frame
_BLANK_MAX_WAIT_S = 8          # give up waiting for real content after this long
_BLANK_POLL_S = 0.6


def _is_screen_blank(image) -> bool:
    """Cheap, local, zero-token check: a loading/splash screen is close
    to a single flat color, so its pixel variance is far lower than any
    real UI full of text, icons, and color. No Gemini call involved."""
    try:
        from PIL import ImageStat
        stddev = ImageStat.Stat(image.convert("L")).stddev[0]
        return stddev < _BLANK_STDDEV_THRESHOLD
    except Exception:
        return False  # if we can't tell, don't block on it


def _wait_for_real_content(max_wait: float = _BLANK_MAX_WAIT_S):
    """Polls screenshots until one looks like real UI (not a flat grey/
    black loading frame) or max_wait is reached. Returns the last
    screenshot taken either way — this never blocks forever."""
    import pyautogui
    waited = 0.0
    shot = pyautogui.screenshot()
    while _is_screen_blank(shot) and waited < max_wait:
        time.sleep(_BLANK_POLL_S)
        waited += _BLANK_POLL_S
        shot = pyautogui.screenshot()
    return shot


def _is_transient_error(e) -> bool:
    """True for Gemini overload/rate-limit errors worth retrying —
    503 UNAVAILABLE ('model is overloaded'), 429 rate limits — as
    opposed to real bugs (bad JSON, auth errors, etc.) that won't be
    fixed by waiting a second and trying again."""
    msg = str(e).lower()
    return any(s in msg for s in ("503", "unavailable", "overloaded", "429", "rate limit", "resource_exhausted"))


def _generate_with_retry(client, model, contents, attempts=_RETRY_ATTEMPTS):
    """client.models.generate_content with automatic backoff retry on
    transient Gemini overload errors. Non-transient errors raise
    immediately on the first try."""
    last_err = None
    for attempt in range(attempts):
        try:
            return client.models.generate_content(model=model, contents=contents)
        except Exception as e:
            last_err = e
            if _is_transient_error(e) and attempt < attempts - 1:
                time.sleep(_RETRY_BASE_DELAY_S * (attempt + 1))
                continue
            raise
    raise last_err

_SEARCH_SETTLE_S = 0.9      # time for Discord's search results to populate
_CONVO_LOAD_S = 1.1         # time for a conversation/channel to open after Enter
_SERVER_LOAD_S = 1.4        # time for a server switch to fully load before next hop
_TYPE_SETTLE_S = 0.4        # time after pasting message before screenshot
_MENTION_SETTLE_S = 0.7     # time for the @mention autocomplete popup to appear
_WINDOW_WAIT_TRIES = 10


# ── config / API key (works with either of the two setups seen so far) ──────

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


# ── Gemini: request → {contact, segments} ────────────────────────────────────

def _parse_request(request: str, api_key: str) -> dict:
    from google import genai
    prompt = (
        "Extract the Discord destination and message content from this "
        "spoken request. The request may have typos (e.g. 'messange' "
        "for 'message'), be missing the word 'discord' entirely, or "
        "name the recipient before or after the message content — "
        "handle all of that.\n"
        f'REQUEST: "{request}"\n'
        "Return ONLY minified JSON, no markdown fences, with keys:\n"
        ' "contact": short name of the PERSON to send a direct message '
        "to (as it'd appear in Discord's search, no titles like \"my "
        "brother\" — resolve to the actual name), OR null if this "
        "should go to a server channel instead of a DM.\n"
        ' "server": short name of the SERVER to navigate to (as it\'d '
        "appear in Discord's search), OR null if this is a direct "
        'message to "contact" instead. Exactly one of "contact"/'
        '"server" must be non-null, never both.\n'
        ' "channel": short name of the channel within that server (e.g. '
        '"general"), OR null if no specific channel was mentioned '
        "(only meaningful when \"server\" is set).\n"
        ' "segments": an array representing the message content IN ORDER. '
        "Each element is EITHER a plain string of normal text, OR an "
        'object {"mention": "..."} representing a REAL Discord ping that '
        'should actually notify someone. Use {"mention": "everyone"} for '
        '@everyone, {"mention": "here"} for @here, or {"mention": '
        '"<username>"} to ping one specific person by their Discord '
        "username/display name. Only create a mention object when the "
        "user clearly asked to ping/tag/notify/mention someone — words "
        "like 'ping', 'tag', 'mention', 'notify', or an explicit '@' in "
        "the request. If a person's name is just part of a normal "
        "sentence with no such intent (e.g. 'tell John I said hi'), keep "
        "it as plain text, not a mention object. Concatenating all "
        "elements in order must form the full message with no extra "
        "characters between them."
    )
    client = genai.Client(api_key=api_key)
    resp = _generate_with_retry(client, _TEXT_MODEL, prompt)
    text = (resp.text or "").strip()
    if "{" in text and "}" in text:
        text = text[text.find("{"): text.rfind("}") + 1]
    return json.loads(text)


def _flatten_segments(segments) -> str:
    """Plain-text preview of the message, e.g. for logging/vision-checking."""
    parts = []
    for seg in segments:
        if isinstance(seg, dict):
            parts.append(f"@{seg.get('mention', '')}")
        else:
            parts.append(str(seg))
    return "".join(parts)


# ── zero-Gemini fallback parser (used only when the real parse call fails) ──
#
# Deliberately simple: handles the common straightforward phrasings —
# "message/tell <person> ... say/tell them <message>", "@everyone"/"ping
# everyone", "go to the <X> server ... <Y> channel/chat ... say <message>".
# It will NOT reliably parse unusual word orders, indirect phrasing, or a
# ping aimed at a specific named person (that's genuinely ambiguous
# without an LLM to tell "ping bob" apart from "bob" just being part of
# the message, so it's left unhandled here rather than guessed wrong).
# This exists purely to avoid a hard failure during a brief Gemini outage
# for the everyday cases — anything it can't confidently parse should
# just be retried once Gemini's available again.

_SAY_SPLIT_RE = re.compile(
    r"\b(?:and\s+)?(?:say|saying|tell(?:ing)?(?:\s+(?:him|her|them|it))?)\b[:,]?\s*",
    re.IGNORECASE)
_IMMEDIATE_VERB_RE = re.compile(
    r"^\s*(?:messange|message|tell|dm)\s+([a-zA-Z0-9_' -]{2,30}?)\s+(.+)$",
    re.IGNORECASE)
_FILLER_WORDS_RE = re.compile(
    r"\b(hey|please|can you|could you|go to|open|the|a|an|server|channel|"
    r"chat|discord|on discord|in discord|message|messange|dm|to|and)\b",
    re.IGNORECASE)
_LEADING_FILLER_RE = re.compile(r"^(?:hey\s+)?(?:please\s+)?(?:go to|open|can you|could you)\s+", re.IGNORECASE)
_LEADING_ARTICLE_RE = re.compile(r"^(?:the|a|an)\s+", re.IGNORECASE)
_SERVER_PHRASE_RE = re.compile(r"([a-zA-Z0-9_' -]{2,40}?)\s+server\b", re.IGNORECASE)
_CHANNEL_PHRASE_RE = re.compile(r"([a-zA-Z0-9_'\- ]{2,40}?)\s+(?:chat|channel)\b", re.IGNORECASE)
_PING_EVERYONE_RE = re.compile(r"@everyone|@here|\b(?:ping|tag|notify)\s+(everyone|here)\b", re.IGNORECASE)
_REJECT_WORDS = {"the", "this", "that", "our", "my", "a", "an", "it", "him", "her", "them"}
_REJECT_SUBSTRINGS = ("server", "channel", "chat", "ping", "tag", "notify", "mention", "go to", "open ")


def _clean_phrase(s: str) -> str:
    s = s.strip(" ,.")
    s = _LEADING_FILLER_RE.sub("", s).strip()
    s = re.sub(r"^then\s+", "", s, flags=re.IGNORECASE).strip()
    s = _LEADING_ARTICLE_RE.sub("", s).strip()
    return s


def _looks_like_a_name(name: str, max_words: int = 5) -> bool:
    """Guards against the regexes above over-capturing a run-on chunk of
    the sentence as if it were a real server/channel/contact name — safer
    to reject and fall through than to confidently act on a wrong name."""
    if not name:
        return False
    words = name.split()
    if not words or len(words) > max_words:
        return False
    if len(words) == 1 and words[0].lower() in _REJECT_WORDS:
        return False
    low = name.lower()
    return not any(b in low for b in _REJECT_SUBSTRINGS)


def _fallback_parse_request(request: str) -> dict:
    """Best-effort keyword parser, zero Gemini calls. Returns {} if it
    can't confidently make sense of the request — caller should treat
    that the same as a hard parse failure, never as a guess to act on."""
    text = request.strip()
    if not text:
        return {}
    low = text.lower()

    m_say = _SAY_SPLIT_RE.search(text)
    if m_say and m_say.start() > 0 and text[m_say.end():].strip():
        head, message = text[:m_say.start()], text[m_say.end():].strip()
    else:
        # No later "say"/"tell" cue past the start — try the immediate
        # "tell/message <name> <message>" pattern instead, where
        # everything after the name IS the message with no separate cue.
        m = _IMMEDIATE_VERB_RE.match(text)
        if not m:
            return {}
        head, message = m.group(1).strip(), m.group(2).strip()

    if not message:
        return {}

    segments = [message]
    if _PING_EVERYONE_RE.search(low):
        word = "here" if re.search(r"\bhere\b", low) and "everyone" not in low else "everyone"
        segments = ([message + " "] if message else []) + [{"mention": word}]

    # Find the server first (anywhere in head), then only look for the
    # channel in whatever text comes AFTER the server match — otherwise,
    # when both keywords sit in one clause with no separator ("gemind
    # server general channel"), one capture can swallow across into the
    # other's territory.
    server = None
    channel = None
    m = re.search(r"\bserver\b", head, re.IGNORECASE)
    if m:
        sm = _SERVER_PHRASE_RE.search(head[:m.end()])
        if sm:
            cand = _clean_phrase(sm.group(1))
            if _looks_like_a_name(cand):
                server = cand
        channel_scope = head[m.end():]
    else:
        channel_scope = head
    if re.search(r"\b(chat|channel)\b", channel_scope, re.IGNORECASE):
        cm = _CHANNEL_PHRASE_RE.search(channel_scope)
        if cm:
            cand = _clean_phrase(cm.group(1))
            if _looks_like_a_name(cand):
                channel = cand

    contact = None
    if not server:
        stripped = _FILLER_WORDS_RE.sub(" ", head)
        stripped = re.sub(r"\s+", " ", stripped).strip(" ,.")
        if _looks_like_a_name(stripped):
            contact = stripped

    if not (server or contact):
        return {}
    return {"contact": contact, "server": server, "channel": channel, "segments": segments}



# ── window handling ──────────────────────────────────────────────────────────

def _ensure_dpi_aware():
    """Without this, on a scaled Windows display pyautogui.screenshot()
    and pyautogui.click() can silently disagree about pixel coordinates
    — the vision model picks a point that's correct in the screenshot,
    but the actual click lands somewhere else. Only needs to run once."""
    try:
        import ctypes
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def _find_discord_window():
    import pygetwindow as gw
    matches = [w for w in gw.getAllWindows() if "discord" in (w.title or "").lower()]
    return matches[0] if matches else None


def _focus_discord():
    """Brings Discord to the foreground, launching it if it's not running.
    Returns True if a Discord window is focused, False otherwise."""
    try:
        win = _find_discord_window()
    except Exception:
        win = None
    if win is None:
        try:
            import os
            os.startfile("discord://")
        except Exception:
            return False
        for _ in range(_WINDOW_WAIT_TRIES):
            time.sleep(1.0)
            try:
                win = _find_discord_window()
            except Exception:
                win = None
            if win:
                break
    if win is None:
        return False
    try:
        if win.isMinimized:
            win.restore()
        win.activate()
        time.sleep(0.5)
        # The window existing doesn't mean Discord has finished rendering —
        # right after launch it often sits on a plain black/grey splash
        # frame for a few seconds. Wait for that to clear before anything
        # else (typing, navigating) happens on top of it.
        _wait_for_real_content()
        return True
    except Exception:
        return False


# ── clipboard-safe typing (handles non-ASCII: Turkish, emoji, accents) ──────

def _type_text(text: str):
    import pyautogui
    import pyperclip
    prev = None
    try:
        prev = pyperclip.paste()
    except Exception:
        pass
    pyperclip.copy(text)
    pyautogui.hotkey("ctrl", "v")
    time.sleep(0.15)
    if prev is not None:
        try:
            pyperclip.copy(prev)
        except Exception:
            pass


def _type_segments(segments):
    """Types a list of plain-text / mention segments into the composer.

    - "@everyone" / "@here": pasted as literal text — Discord recognizes
      these reserved words on their own, no autocomplete needed.
    - a specific person's mention: pastes "@name" to trigger Discord's
      autocomplete popup, then presses Enter. Discord intercepts Enter
      to accept the highlighted suggestion whenever that popup is open,
      so this does NOT send the message early — it only converts the
      text into a real mention pill and moves on. Only if the name has
      no match at all (typo, wrong server) would the popup fail to
      appear, and in that edge case this Enter would fall through to
      sending prematurely — keep names close to how they actually
      appear in Discord to avoid that.
    """
    import pyautogui
    for seg in segments:
        if isinstance(seg, dict):
            mention = str(seg.get("mention", "")).strip()
            if not mention:
                continue
            _type_text("@" + mention)
            if mention.lower() not in ("everyone", "here"):
                time.sleep(_MENTION_SETTLE_S)
                try:
                    pyautogui.press("enter")  # accept the highlighted suggestion
                except Exception:
                    pass
                time.sleep(0.2)
            else:
                time.sleep(0.2)  # @everyone/@here auto-convert, nothing to accept
        else:
            if seg:
                _type_text(str(seg))


# ── vision safety check before sending ───────────────────────────────────────

def _verify_before_send(destination: str, segments, api_key: str) -> dict:
    """Screenshots the current state and asks Gemini vision to confirm the
    right conversation is open, the composer holds the right text, and
    any pings actually became real mentions (not plain grey text).
    Returns {"ok": bool, "reason": str}."""
    import pyautogui
    from google import genai
    from google.genai import types as gtypes

    shot = _wait_for_real_content()
    w, h = shot.size
    if w > _MAX_SCREENSHOT_WIDTH:
        shot = shot.resize((_MAX_SCREENSHOT_WIDTH, int(h * _MAX_SCREENSHOT_WIDTH / w)))
    import io
    buf = io.BytesIO()
    shot.save(buf, format="JPEG", quality=70)

    message = _flatten_segments(segments)
    mention_names = [str(s.get("mention", "")) for s in segments if isinstance(s, dict)]
    mention_note = (
        f" Additionally, these should appear as REAL Discord mentions — "
        f"shown as a highlighted/colored pill, not plain undecorated grey "
        f"text: {', '.join('@' + m for m in mention_names)}. If any of "
        f"them still look like plain text, that ping failed."
        if mention_names else ""
    )

    prompt = (
        "This is a screenshot of the Discord desktop app, taken right "
        "before sending a message. Check:\n"
        f'1. The open conversation/channel header matches this intended '
        f'destination: "{destination}" (allow reasonable nickname/'
        "display-name variation, case-insensitive). Critically, also "
        "cross-check the SERVER shown (its name/icon in the server "
        "rail or header) against the server named in the destination, "
        "if one is named — a channel with a matching name in the WRONG "
        "server must be treated as a failure, not a match.\n"
        f'2. The message compose box at the bottom contains this text '
        f'(or something extremely close to it): "{message}"' + mention_note + "\n"
        "Return ONLY minified JSON with keys:\n"
        ' "ok": true only if ALL checks pass,\n'
        ' "reason": short explanation of what you saw, especially if '
        "ok is false (e.g. wrong conversation open, composer empty, "
        "search results still showing, a mention still shown as plain "
        "text instead of a highlighted ping, no Discord window visible)."
    )
    client = genai.Client(api_key=api_key)
    resp = _generate_with_retry(
        client, _VISION_MODEL,
        [gtypes.Part.from_bytes(data=buf.getvalue(), mime_type="image/jpeg"), prompt],
        attempts=1,  # fail fast into the no-vision confirmation fallback instead of burning quota retrying
    )
    text = (resp.text or "").strip()
    if "{" in text and "}" in text:
        text = text[text.find("{"): text.rfind("}") + 1]
    try:
        return json.loads(text)
    except Exception:
        return {"ok": False, "reason": "couldn't verify the screen state"}


# ── accessibility-tree channel picking (no vision, no tokens) ───────────────

def _find_and_click_channel_by_name(channel: str) -> bool:
    """Finds and clicks the given channel by reading Discord's own
    Windows UI Automation accessibility tree — NOT a screenshot, NOT
    Gemini. This only ever looks inside the CURRENTLY-ACTIVE server's own
    sidebar, so unlike Ctrl+K search there's no cross-server ambiguity to
    begin with: whatever it finds necessarily belongs to the server
    that's already open. Zero token cost. Returns True if it found and
    clicked a match, False if it couldn't (missing dependency, incomplete
    accessibility tree, collapsed category, etc.) — callers should fall
    back to the vision-assisted method in that case."""
    try:
        from pywinauto import Desktop
    except ImportError:
        return False
    try:
        win = Desktop(backend="uia").window(title_re=".*Discord.*")
        if not win.exists(timeout=2):
            return False
        needle = channel.strip().lower()
        if not needle:
            return False
        preferred_types = {"TreeItem", "ListItem"}
        best = None
        for ctrl in win.descendants():
            try:
                name = (ctrl.window_text() or "").lower()
            except Exception:
                continue
            if needle not in name:
                continue
            try:
                rect = ctrl.rectangle()
            except Exception:
                continue
            if rect.width() <= 0 or rect.height() <= 0:
                continue
            ctype = getattr(ctrl.element_info, "control_type", "") or ""
            if ctype in preferred_types:
                best = ctrl
                break
            if best is None:
                best = ctrl
        if best is None:
            return False
        best.click_input()
        return True
    except Exception:
        return False


# ── vision-assisted channel picking (server-scoped search fix) ──────────────

def _vision_pick_channel_result(channel: str, server: str, api_key: str) -> dict:
    """Screenshots Discord's currently-open quick-switcher results and asks
    Gemini vision for the pixel coordinates of the specific CHANNEL result
    that belongs to `server`. Discord's Ctrl+K search is NOT scoped to the
    current server — a same-named channel elsewhere can rank above the
    one actually wanted, so blindly pressing Enter on "top result" is not
    safe here. Full-resolution PNG is used (not the downscaled JPEG used
    elsewhere) so the returned pixel coordinates line up with the real
    screen for clicking. Returns {"found": bool, "x": int, "y": int,
    "reason": str}."""
    from google import genai
    from google.genai import types as gtypes
    import io

    shot = _wait_for_real_content(max_wait=4)
    buf = io.BytesIO()
    shot.save(buf, format="PNG")

    prompt = (
        "This is a screenshot of Discord's quick-switcher search popup, "
        f'right after searching for a channel named "{channel}" while '
        f'the currently-active server is "{server}". Discord\'s search '
        "is NOT scoped to the current server, so results from other "
        "servers can appear too, sometimes ranked first — each result "
        "row usually shows a small server icon or server name next to "
        "the channel name to indicate which server it belongs to.\n"
        f'Find the result row that is a CHANNEL (not a person or DM) '
        f'named closest to "{channel}" AND that clearly belongs to the '
        f'server "{server}" specifically — not a similarly-named '
        "channel in any other server. Return ONLY minified JSON with keys:\n"
        ' "found": true only if you are confident which row belongs to '
        "that exact server,\n"
        ' "x": integer pixel x-coordinate of the center of that result '
        "row (only meaningful if found is true),\n"
        ' "y": integer pixel y-coordinate of the center of that result '
        "row (only meaningful if found is true),\n"
        ' "reason": short note, especially if found is false (e.g. no '
        "channel with that name in that server, results still loading, "
        "ambiguous/can't tell which server a row belongs to)."
    )
    client = genai.Client(api_key=api_key)
    resp = _generate_with_retry(
        client, _VISION_MODEL,
        [gtypes.Part.from_bytes(data=buf.getvalue(), mime_type="image/png"), prompt],
        attempts=1,
    )
    text = (resp.text or "").strip()
    if "{" in text and "}" in text:
        text = text[text.find("{"): text.rfind("}") + 1]
    try:
        return json.loads(text)
    except Exception:
        return {"found": False, "reason": "couldn't read the search results"}


# ── entry point ───────────────────────────────────────────────────────────────

def run(parameters: dict, player=None, session_memory=None) -> str:
    request = (parameters.get("request") or "").strip()
    if not request:
        return "Who do you want me to message, and what should I say?"

    _ensure_dpi_aware()

    def _log(msg: str) -> None:
        if player:
            try:
                player.write_log(msg)
            except Exception:
                pass

    # A pending confirmation from a previous (vision-unavailable) call
    # takes priority over treating this as a new message request.
    pending = _load_pending()
    if pending:
        verdict = _classify_yes_no(request)
        if verdict == "yes":
            _clear_pending()
            try:
                import pyautogui
            except ImportError:
                return "I need pyautogui to send that — please install it."
            if not _focus_discord():
                return "I couldn't get back to Discord to send that — is it still open?"
            try:
                pyautogui.press("enter")
            except Exception as e:
                return f"I couldn't press send: {e}"
            _log(f"JARVIS: Discord message sent (confirmed without vision) to '{pending.get('destination')}'.")
            if player:
                try:
                    player.show_content("💬 DISCORD MESSAGE SENT",
                                         f"To: {pending.get('destination')}\n\n{pending.get('message')}")
                except Exception:
                    pass
            return f"Sent to {pending.get('destination')}: \"{pending.get('message')}\""
        elif verdict == "no":
            _clear_pending()
            try:
                import pyautogui
                pyautogui.hotkey("ctrl", "a")
                pyautogui.press("backspace")
            except Exception:
                pass
            return "Okay, cancelled — nothing was sent."
        else:
            # Not a yes/no reply — this is an unrelated new request, so
            # drop the stale pending confirmation and fall through.
            _clear_pending()

    try:
        import pyautogui  # noqa: F401
        import pygetwindow  # noqa: F401
        import pyperclip  # noqa: F401
    except ImportError:
        return ("I need a few libraries for Discord automation. Please "
                "install them with: pip install pyautogui pygetwindow pyperclip")

    api_key = _get_gemini_key()
    if not api_key:
        return "I can't do that — no Gemini API key is configured."

    try:
        spec = _parse_request(request, api_key)
    except Exception as e:
        if _is_transient_error(e):
            spec = _fallback_parse_request(request)
            if spec:
                _log("JARVIS: Gemini parse unavailable — used the no-Gemini keyword fallback instead.")
            else:
                return ("Google's Gemini service is overloaded right now, sir "
                        "— that's on their end, not Discord or this plugin. "
                        "I already retried a few times, and this request's "
                        "phrasing was too complex for my simple backup parser "
                        "to handle without it. It usually clears up within a "
                        "minute or two.")
        else:
            return f"I couldn't work out who to message or what to say: {e}"

    contact = (spec.get("contact") or "").strip() or None
    server = (spec.get("server") or "").strip() or None
    channel = (spec.get("channel") or "").strip() or None
    segments = spec.get("segments") or []
    message = _flatten_segments(segments)

    if not (contact or server) or not message.strip():
        return "I need somewhere to send it and something to say — could you rephrase that?"

    if server:
        hops = [server] + ([channel] if channel else [])
        destination = f"#{channel} in {server}" if channel else server
    else:
        hops = [contact]
        destination = contact

    _log(f"JARVIS: Sending Discord message to '{destination}'.")

    if not _focus_discord():
        return "I couldn't find or open the Discord desktop app — is it installed?"

    import pyautogui

    try:
        alias_link = _find_alias_link(server, channel) if (server and channel) else None
        if alias_link and _open_alias_link(alias_link):
            # Deep link jumps straight to the exact server+channel —
            # no search, no ambiguity, no vision call needed at all.
            _log(f"JARVIS: Jumped directly to saved alias for '{destination}'.")
            time.sleep(_SERVER_LOAD_S)
            _wait_for_real_content()
        else:
            # Hop 1: the server (or the contact, for a DM/group) — Ctrl+K
            # top-result Enter is fine here, server names are distinctive
            # enough that this rarely misfires.
            pyautogui.hotkey("ctrl", "k")
            time.sleep(0.4)
            _type_text(hops[0])
            time.sleep(_SEARCH_SETTLE_S)
            pyautogui.press("enter")
            time.sleep(_SERVER_LOAD_S if server else _CONVO_LOAD_S)
            _wait_for_real_content()

            # Hop 2 (only when a specific channel was requested and no
            # alias matched). Tier 1: read the channel straight out of the
            # currently-open server's own sidebar via accessibility tree —
            # inherently scoped to this server (no cross-server ambiguity
            # possible), zero tokens, works for any channel with no setup.
            # Tier 2 (last resort, only if that lookup fails): a vision-
            # assisted Ctrl+K click — the one remaining path that costs a
            # Gemini call.
            if server and channel:
                found = False
                try:
                    found = _find_and_click_channel_by_name(channel)
                except Exception:
                    found = False

                if found:
                    time.sleep(_CONVO_LOAD_S)
                    _wait_for_real_content()
                else:
                    pyautogui.hotkey("ctrl", "k")
                    time.sleep(0.4)
                    _type_text(channel)
                    time.sleep(_SEARCH_SETTLE_S)
                    pick = _vision_pick_channel_result(channel, server, api_key)
                    if pick.get("found") and pick.get("x") is not None and pick.get("y") is not None:
                        pyautogui.click(int(pick["x"]), int(pick["y"]))
                        time.sleep(_CONVO_LOAD_S)
                        _wait_for_real_content()
                    else:
                        pyautogui.press("escape")  # close the dangling search popup
                        reason = pick.get("reason") or "couldn't confidently find that channel in that server"
                        _log(f"JARVIS: Discord channel hop aborted — {reason}")
                        return (f"I stopped before sending — I couldn't confidently "
                                f"find #{channel} in {server} ({reason}). Nothing was sent.")

        # Type the message into the composer (should already have focus),
        # driving Discord's mention autocomplete for any real pings.
        pyautogui.press("escape")  # defensively close any lingering popup first
        time.sleep(0.15)
        _type_segments(segments)
        time.sleep(_TYPE_SETTLE_S)
    except Exception as e:
        return f"Something went wrong controlling Discord: {e}"

    try:
        verdict = _verify_before_send(destination, segments, api_key)
    except Exception as e:
        if _is_transient_error(e):
            # No vision available — leave the message typed but unsent,
            # remember it, and ask the user to confirm with a plain
            # yes/no instead (no Gemini call needed for that reply).
            _save_pending(destination, message)
            _log(f"JARVIS: Vision check unavailable, asking for manual confirmation on '{destination}'.")
            return (f"Gemini's vision check is unavailable right now, so I "
                    f"can't verify the screen myself. I've typed this to "
                    f"{destination} but haven't sent it yet: \"{message}\". "
                    f"Is this the correct person and message? Say yes to "
                    f"send it, or no to cancel.")
        verdict = {"ok": False, "reason": "vision check failed to run"}

    if not verdict.get("ok"):
        # Don't send — clear the composer so nothing garbled goes out.
        try:
            pyautogui.hotkey("ctrl", "a")
            pyautogui.press("backspace")
        except Exception:
            pass
        reason = verdict.get("reason") or "the screen didn't look right"
        _log(f"JARVIS: Discord send aborted — {reason}")
        if player:
            try:
                player.show_content("⚠️ DISCORD SEND ABORTED",
                                     f"To: {destination}\nMessage: {message}\n\nReason: {reason}")
            except Exception:
                pass
        return f"I stopped before sending — {reason}. Nothing was sent to {destination}."

    try:
        pyautogui.press("enter")  # actually send
    except Exception as e:
        return f"I typed the message but couldn't press Enter to send it: {e}"

    _log(f"JARVIS: Discord message sent to '{destination}'.")

    if player:
        try:
            player.show_content("💬 DISCORD MESSAGE SENT",
                                 f"To: {destination}\n\n{message}")
        except Exception:
            pass

    return f"Sent to {destination}: \"{message}\""
