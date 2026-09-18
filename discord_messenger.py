"""
JARVIS plugin — Discord Messenger & Autonomous Chat Takeover.

This plugin provides complete voice-driven control over the Discord
desktop app on Windows, supporting two core operational modes:

1. DISCORD MESSAGING (voice → sent DM/message):
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
   they work on their own.

   Destinations can be a person (DM) OR a server + channel combo, e.g.
   "go to [server], go to #general, and say hi" — each hop (server name,
   then channel name) is its own Ctrl+K search-and-jump (or deep-link alias
   if configured, or sidebar accessibility tree lookup).

2. DISCORD CHAT TAKEOVER (voice → autonomous screen-vision auto-reply):
   "JARVIS, take over this Discord chat" or "Discord'da konuşmayı devral"
   or "take over the chat with Bob on Discord and tell him I'm busy" —
   JARVIS can either navigate to the requested contact/channel on Discord
   or monitor the currently open conversation, watching the screen every
   few seconds. When the other party sends a new message, JARVIS reads it
   directly from the screen, generates a natural reply mirroring the user's
   casual texting style, and sends it through the real Discord input box.

   Discord Chat Takeover is separate and specialized:
   - Layout-Aware: Unlike mobile messaging apps where outgoing messages
     are right-aligned, Discord desktop places all messages vertically
     on the left with avatars and usernames. The takeover vision model
     is specifically customized for Discord's UI layout.
   - Separate State: Operates independently with its own background loop,
     engine, stop trigger, and HUD panel, without interfering with generic
     screen-takeover plugins.
   - Dual Engine: Operates over a persistent Gemini Live audio session
     harvesting output transcripts for instant responses, with automatic
     fallback to stateless gemini-2.5-flash REST calls.
   - Focus Guard: Ensures replies are only pasted when Discord is active,
     holding replies if the user switches to other applications.

Windows + Discord DESKTOP APP only (drives the user's own client directly).
"""

import asyncio
import base64
import io
import json
import re
import sys
import threading
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# ── Message send pending confirmation state ──────────────────────────────────
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
    """Plain keyword match, no Gemini call — confirming shouldn't cost tokens."""
    t = text.strip().lower().strip(".!?")
    if t in _YES_WORDS or any(t.startswith(w) for w in _YES_WORDS):
        return "yes"
    if t in _NO_WORDS or any(t.startswith(w) for w in _NO_WORDS):
        return "no"
    return None


# ── Saved channel aliases (zero-token, zero-ambiguity deep links) ───────────
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
    ids = re.findall(r"\d{15,25}", link_or_ids)
    if len(ids) < 2:
        return False
    guild_id, channel_id = ids[-2], ids[-1]
    try:
        import os
        os.startfile(f"discord://discord.com/channels/{guild_id}/{channel_id}")
        return True
    except Exception:
        return False


# ── Model names & limits ─────────────────────────────────────────────────────
_TEXT_MODEL = "gemini-flash-latest"
_VISION_MODEL = "gemini-2.5-flash"
_LIVE_MODEL = "models/gemini-2.5-flash-native-audio-preview-12-2025"
_REST_MODEL = "gemini-2.5-flash"

_RETRY_ATTEMPTS = 3
_RETRY_BASE_DELAY_S = 1.5
_MAX_SCREENSHOT_WIDTH = 960
SKIP_VISION_CHECK = False

_BLANK_STDDEV_THRESHOLD = 10
_BLANK_MAX_WAIT_S = 8
_BLANK_POLL_S = 0.6

_SEARCH_SETTLE_S = 0.9
_CONVO_LOAD_S = 1.1
_SERVER_LOAD_S = 1.4
_TYPE_SETTLE_S = 0.4
_MENTION_SETTLE_S = 0.7
_WINDOW_WAIT_TRIES = 10

# Takeover-specific settings
_POLL_SECONDS = 10
_DEFAULT_MINUTES = 10
_PIXEL_DELTA = 15
_CHANGED_RATIO = 0.002
_MAX_MISSES = 4
_MAX_ERRORS = 5
_MAX_REPLY_CHARS = 500
_MAX_BURST = 3
_BURST_GAP = 2.0
_START_DEBOUNCE = 20
_IMG_MAX_W = 1280
_JPEG_Q = 82
_PANEL_EXCHANGES = 10

# ── Discord chat takeover system prompt ───────────────────────────────────────
_DISCORD_LIVE_SYSTEM = (
    "You are a silent chat-takeover engine answering on behalf of the user "
    "specifically in the DISCORD DESKTOP APP. Every turn you receive one "
    "screenshot of the user's screen showing Discord.\n"
    "In Discord: all messages (both incoming and outgoing) appear in the chat "
    "pane on the left, with user avatars, usernames, and timestamps. "
    "Outgoing messages are the user's OWN messages (matching the user account "
    "shown in Discord or sent earlier in this session). Incoming messages are "
    "messages sent by the OTHER person (in DMs) or other server members.\n"
    "Rules — produce exactly ONE of these three outputs per turn, and "
    "nothing else, ever:\n"
    "1. If NO Discord chat conversation is visible in the screenshot, say "
    "exactly: NOSCREEN — a Discord chat means an active Discord window showing "
    "a DM or server text channel with message history and the 'Message @...' "
    "or 'Message #...' input bar at the bottom. Terminals, code editors, "
    "browsers, or Discord settings/server-discovery screens do NOT count.\n"
    "2. If a Discord chat is visible but the newest message at the bottom of "
    "the chat history was sent by the USER (outgoing), or it is an incoming "
    "message you have ALREADY replied to earlier in this session, say "
    "exactly: STANDBY. (Trust your memory of what you already replied this "
    "session: if you already answered the newest incoming message, output "
    "STANDBY even if your sent reply is not yet visible on screen).\n"
    "3. If the newest message in the Discord conversation is an incoming "
    "message from someone else that you have NOT replied to yet, say ONLY "
    "the reply itself — written AS THE USER: same language as the conversation, "
    "mimicking the texting style and tone of the user. The reply is 1 to 3 "
    "chat messages: when more than one, separate them with the single word "
    "NEXTMSG (that word is never sent — it only splits the messages, which "
    "are then sent one after another). Each message is at most 2 sentences "
    "and typically 4-8 words — NEVER a bare one-or-two word reply unless the "
    "moment truly calls for it; go longer when the incoming message deserves "
    "it. Write like real casual Discord chatting between friends or server "
    "members: relaxed wording, mirror the other person's current mood and "
    "energy (playful if they joke, calm and warm if they're upset), and use "
    "almost no punctuation — no commas, no period at the end; a question or "
    "exclamation mark only when it really helps. Use 2-3 messages only when "
    "it fits the style (e.g. a reaction, then a follow-up question). No "
    "preamble, no explanation, no signatures, never mention being an AI.\n"
    "Whatever you say is pasted VERBATIM into Discord and sent to the other "
    "person — so never say anything that is not meant to be sent, except the "
    "exact single words NOSCREEN or STANDBY and the separator word NEXTMSG.\n"
    "LANGUAGE RULE: these instructions being in English is IRRELEVANT — the "
    "reply must ALWAYS be written in the language used in the conversation "
    "itself (the other person's messages). Never switch languages between "
    "replies unless the conversation itself switches."
)

PLUGIN = {
    "name": "discord_messenger",
    "description": (
        "Controls the DISCORD DESKTOP APP for both composing/sending messages "
        "and performing autonomous chat takeover on Discord.\n"
        "1. SENDING MESSAGES: Sends a message to a person (DM) or server channel, "
        "including real @-pings and multi-hop navigation. Trigger this for requests "
        "combining Discord + recipient/destination + message content (e.g. "
        "'message John on Discord and tell him I\\'m late', 'tell bob hi on discord', "
        "'discord\\'da Ayşe\\'ye ... yaz', 'go to study server #general and say hi', "
        "'ping everyone ...'). Also handles pending yes/no send confirmations.\n"
        "2. DISCORD CHAT TAKEOVER: Automatically takes over an ongoing Discord chat, "
        "monitoring incoming messages and replying in the user's casual texting style. "
        "Unlike generic screen takeovers, this tool is Discord-specific: it brings Discord "
        "to the front, can optionally navigate to a specific contact or channel first, "
        "correctly understands Discord's left-aligned chat stream layout, and runs its own "
        "independent takeover loop. Trigger for requests like 'Discord\\'da konuşmayı devral', "
        "'take over this Discord chat', 'take over the chat with Bob on Discord', "
        "'Discord konuşmasını geri al', 'stop Discord takeover', 'is Discord takeover running'.\n"
        "Use action='send' to send a message (default), action='takeover_start' (or 'start') "
        "to begin takeover, action='takeover_stop' (or 'stop') to end takeover, or "
        "action='takeover_status' (or 'status') to check status. You can also pass the spoken "
        "command verbatim in 'request' or 'query'."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "enum": [
                    "send",
                    "takeover_start",
                    "takeover_stop",
                    "takeover_status",
                    "start",
                    "stop",
                    "status",
                ],
                "description": (
                    "Action to perform: 'send' (default) to send a single message; "
                    "'takeover_start' (or 'start') to begin Discord chat takeover; "
                    "'takeover_stop' (or 'stop') to stop Discord takeover; "
                    "'takeover_status' (or 'status') to check takeover state."
                ),
            },
            "request": {
                "type": "STRING",
                "description": (
                    "The user's full request verbatim, in their own language. "
                    "For sending: contains recipient/destination and message content. "
                    "For takeover: spoken instructions (e.g. 'take over chat with Bob on Discord', "
                    "'tell him I am busy'), contact/channel to navigate to if specified, "
                    "or confirmation replies ('yes'/'no')."
                ),
            },
            "query": {
                "type": "STRING",
                "description": "Alias for request, typically used by chat takeover callers.",
            },
            "duration_minutes": {
                "type": "NUMBER",
                "description": "Optional safety time limit in minutes for Discord chat takeover (default 10).",
            },
        },
    },
}

# ── Discord takeover module state ─────────────────────────────────────────────
_takeover_state = {
    "thread": None,
    "stop": None,
    "exchanges": [],
    "engine": "",
    "ended": "",
    "started_at": 0,
    "target": "",
}
_takeover_lock = threading.Lock()


def _takeover_running() -> bool:
    t = _takeover_state["thread"]
    return bool(t and t.is_alive())


# ── Config & API key helpers ─────────────────────────────────────────────────
def _config() -> dict:
    try:
        return json.loads((BASE_DIR / "config" / "api_keys.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


def _get_gemini_key():
    try:
        from memory.config_manager import get_gemini_key
        key = get_gemini_key()
        if key:
            return key
    except Exception:
        pass
    return _config().get("gemini_api_key")


# ── Vision / Transient error utilities ───────────────────────────────────────
def _is_screen_blank(image) -> bool:
    try:
        from PIL import ImageStat
        stddev = ImageStat.Stat(image.convert("L")).stddev[0]
        return stddev < _BLANK_STDDEV_THRESHOLD
    except Exception:
        return False


def _wait_for_real_content(max_wait: float = _BLANK_MAX_WAIT_S):
    import pyautogui
    waited = 0.0
    shot = pyautogui.screenshot()
    while _is_screen_blank(shot) and waited < max_wait:
        time.sleep(_BLANK_POLL_S)
        waited += _BLANK_POLL_S
        shot = pyautogui.screenshot()
    return shot


def _is_transient_error(e) -> bool:
    msg = str(e).lower()
    return any(s in msg for s in ("503", "unavailable", "overloaded", "429", "rate limit", "resource_exhausted"))


def _generate_with_retry(client, model, contents, attempts=_RETRY_ATTEMPTS):
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


# ── Window management & typing ───────────────────────────────────────────────
def _ensure_dpi_aware():
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
        _wait_for_real_content()
        return True
    except Exception:
        return False


def _active_window_id():
    try:
        import pygetwindow as gw
        w = gw.getActiveWindow()
        if w is None:
            return None
        return getattr(w, "_hWnd", None) or getattr(w, "title", None) or None
    except Exception:
        return None


def _is_discord_active():
    try:
        import pygetwindow as gw
        w = gw.getActiveWindow()
        if w is None:
            return True
        title = (w.title or "").lower()
        return "discord" in title
    except Exception:
        return True


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
                    pyautogui.press("enter")
                except Exception:
                    pass
                time.sleep(0.2)
            else:
                time.sleep(0.2)
        else:
            if seg:
                _type_text(str(seg))


# ── Parsing for Message Sending ──────────────────────────────────────────────
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
    parts = []
    for seg in segments:
        if isinstance(seg, dict):
            parts.append(f"@{seg.get('mention', '')}")
        else:
            parts.append(str(seg))
    return "".join(parts)


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
    text = request.strip()
    if not text:
        return {}
    low = text.lower()

    m_say = _SAY_SPLIT_RE.search(text)
    if m_say and m_say.start() > 0 and text[m_say.end():].strip():
        head, message = text[:m_say.start()], text[m_say.end():].strip()
    else:
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


# ── Navigation & Channel Picking ─────────────────────────────────────────────
def _find_and_click_channel_by_name(channel: str) -> bool:
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


def _vision_pick_channel_result(channel: str, server: str, api_key: str) -> dict:
    from google import genai
    from google.genai import types as gtypes

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


def _navigate_discord_destination(server: str, channel: str, contact: str, api_key: str, log_fn=None) -> tuple:
    """Navigates Discord to the specified contact (DM) or server + channel.
    Returns (success: bool, error_message: str, destination_name: str)."""
    import pyautogui

    if server:
        hops = [server] + ([channel] if channel else [])
        destination = f"#{channel} in {server}" if channel else server
    else:
        hops = [contact]
        destination = contact

    if log_fn:
        log_fn(f"JARVIS: Navigating Discord to '{destination}'.")

    if not _focus_discord():
        return False, "I couldn't find or open the Discord desktop app — is it installed?", destination

    alias_link = _find_alias_link(server, channel) if (server and channel) else None
    if alias_link and _open_alias_link(alias_link):
        if log_fn:
            log_fn(f"JARVIS: Jumped directly to saved alias for '{destination}'.")
        time.sleep(_SERVER_LOAD_S)
        _wait_for_real_content()
    else:
        # Hop 1: server or contact
        pyautogui.hotkey("ctrl", "k")
        time.sleep(0.4)
        _type_text(hops[0])
        time.sleep(_SEARCH_SETTLE_S)
        pyautogui.press("enter")
        time.sleep(_SERVER_LOAD_S if server else _CONVO_LOAD_S)
        _wait_for_real_content()

        # Hop 2: channel within server
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
                    pyautogui.press("escape")
                    reason = pick.get("reason") or "couldn't confidently find that channel in that server"
                    if log_fn:
                        log_fn(f"JARVIS: Discord channel hop aborted — {reason}")
                    return False, f"I couldn't confidently find #{channel} in {server} ({reason}).", destination

    pyautogui.press("escape")
    time.sleep(0.15)
    return True, "", destination


# ── Vision Verification Before Sending Message ───────────────────────────────
def _verify_before_send(destination: str, segments, api_key: str) -> dict:
    from google import genai
    from google.genai import types as gtypes

    shot = _wait_for_real_content()
    w, h = shot.size
    if w > _MAX_SCREENSHOT_WIDTH:
        shot = shot.resize((_MAX_SCREENSHOT_WIDTH, int(h * _MAX_SCREENSHOT_WIDTH / w)))
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
        attempts=1,
    )
    text = (resp.text or "").strip()
    if "{" in text and "}" in text:
        text = text[text.find("{"): text.rfind("}") + 1]
    try:
        return json.loads(text)
    except Exception:
        return {"ok": False, "reason": "couldn't verify the screen state"}


# ── Discord Chat Takeover: Engines & Utilities ────────────────────────────────
def _grab_screen():
    """Return (raw_rgb_np, jpeg_bytes) of the primary monitor.
    Uses mss for fast capture, falling back to pyautogui if mss is unavailable."""
    import numpy as np
    import PIL.Image

    try:
        import mss
        with mss.mss() as sct:
            monitors = sct.monitors
            target = monitors[1] if len(monitors) > 1 else monitors[0]
            shot = sct.grab(target)
            raw = np.frombuffer(shot.rgb, dtype=np.uint8)
            img = PIL.Image.frombytes("RGB", shot.size, shot.rgb)
    except Exception:
        import pyautogui
        shot = pyautogui.screenshot()
        raw = np.array(shot)
        img = shot

    img.thumbnail((_IMG_MAX_W, _IMG_MAX_W), PIL.Image.BILINEAR)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=_JPEG_Q)
    return raw, buf.getvalue()


def _screen_changed(prev, cur) -> bool:
    import numpy as np
    if prev is None or prev.shape != cur.shape:
        return True
    delta = np.abs(prev.astype(np.int16) - cur.astype(np.int16))
    return bool(np.mean(delta > _PIXEL_DELTA) > _CHANGED_RATIO)


def _split_messages(verdict: str) -> list:
    parts = [p.strip() for p in re.split(r"\s*NEXTMSG\s*", verdict) if p.strip()]
    return parts[:_MAX_BURST]


def _history_note(recent: list) -> str:
    if not recent:
        return ""
    listed = "; ".join(f'"{r}"' for r in recent)
    return (f" (Memory aid — Discord replies you already sent, oldest first: "
            f"{listed}. Never answer an incoming message you already "
            f"answered, even if these replies are not visible on the "
            f"screenshot: in that case output STANDBY.)")


def _casualize(msg: str) -> str:
    out = msg.replace(", ", " ")
    if out.endswith(".") and not out.endswith(".."):
        out = out[:-1]
    return " ".join(out.split())


def _send_reply(reply: str, os_name: str) -> None:
    import pyautogui
    import pyperclip

    old_clip = None
    try:
        old_clip = pyperclip.paste()
    except Exception:
        pass

    pyperclip.copy(reply)
    pyautogui.hotkey("command" if os_name == "mac" else "ctrl", "v")
    time.sleep(0.35)
    pyautogui.press("enter")
    time.sleep(0.2)

    if old_clip is not None:
        try:
            pyperclip.copy(old_clip)
        except Exception:
            pass


class _LiveEngine:
    def __init__(self, api_key: str, system_prompt: str):
        self._api_key = api_key
        self._system = system_prompt
        self._client = None
        self._cm = None
        self._session = None
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever,
                                        daemon=True, name="discord-takeover-live")
        self._thread.start()

    def check(self, jpg: bytes, recent: list) -> str:
        fut = asyncio.run_coroutine_threadsafe(
            self._check_async(jpg, recent), self._loop)
        return fut.result(timeout=90)

    def close(self) -> None:
        try:
            asyncio.run_coroutine_threadsafe(
                self._close_session(), self._loop).result(timeout=5)
        except Exception:
            pass
        try:
            self._loop.call_soon_threadsafe(self._loop.stop)
        except Exception:
            pass

    async def _connect(self):
        if self._session is not None:
            return
        from google import genai
        from google.genai import types as gtypes

        if self._client is None:
            self._client = genai.Client(api_key=self._api_key,
                                        http_options={"api_version": "v1beta"})
        kwargs = dict(
            response_modalities=["AUDIO"],
            output_audio_transcription={},
            system_instruction=self._system,
        )
        try:
            kwargs["context_window_compression"] = (
                gtypes.ContextWindowCompressionConfig(sliding_window=gtypes.SlidingWindow())
            )
        except AttributeError:
            pass
        model = (_config().get("discord_takeover_live_model")
                 or _config().get("chat_takeover_live_model")
                 or getattr(sys.modules.get("main"), "LIVE_MODEL", None)
                 or _LIVE_MODEL)
        self._cm = self._client.aio.live.connect(
            model=model, config=gtypes.LiveConnectConfig(**kwargs))
        self._session = await asyncio.wait_for(self._cm.__aenter__(), 25)

    async def _close_session(self):
        if self._cm is not None:
            try:
                await self._cm.__aexit__(None, None, None)
            except Exception:
                pass
        self._cm = self._session = None

    async def _check_async(self, jpg: bytes, recent: list) -> str:
        try:
            return await self._turn(jpg, recent)
        except Exception:
            await self._close_session()
            return await self._turn(jpg, recent)

    async def _turn(self, jpg: bytes, recent: list) -> str:
        await self._connect()
        note = _history_note(recent)
        await asyncio.wait_for(
            self._session.send_client_content(
                turns={"parts": [
                    {"inline_data": {
                        "mime_type": "image/jpeg",
                        "data": base64.b64encode(jpg).decode("ascii")}},
                    {"text": "Discord screenshot check — respond now with exactly "
                             "one of your three outputs (NOSCREEN / STANDBY "
                             "/ the reply), even if nothing changed since "
                             "the last screenshot. Reply language = the "
                             "conversation's own language, NOT English by "
                             "default." + note},
                ]},
                turn_complete=True),
            timeout=20)

        transcript = []

        async def _drain():
            async for resp in self._session.receive():
                sc = getattr(resp, "server_content", None)
                if sc and sc.output_transcription and sc.output_transcription.text:
                    transcript.append(sc.output_transcription.text)

        await asyncio.wait_for(_drain(), timeout=60)
        try:
            await asyncio.wait_for(_drain(), timeout=1.5)
        except asyncio.TimeoutError:
            pass

        return "".join(transcript).strip()


class _RestEngine:
    def __init__(self, api_key: str, user_instruction: str):
        self._api_key = api_key
        self._instruction = user_instruction

    def check(self, jpg: bytes, recent: list) -> str:
        from google import genai
        from google.genai import types as gtypes

        prompt = (
            "You are an AI that has temporarily taken over the USER's side of "
            "a Discord conversation on the Discord desktop app. The image is "
            "a screenshot of the user's screen showing Discord.\n"
            "In Discord, all messages appear in the chat pane on the left with "
            "avatars and usernames. Outgoing messages are sent by the user; "
            "incoming messages are sent by the other person or server members.\n"
            + (f'Extra instruction from the user: "{self._instruction}"\n'
               if self._instruction else "")
            + (_history_note(recent).strip() + "\n" if recent else "")
            + "Return ONLY minified JSON — no markdown fences — with keys:\n"
            ' "chat_visible": true/false — true ONLY for a real Discord '
            "conversation with message history and input bar ('Message @...' / 'Message #...'); "
            "terminals, code editors, settings, and other apps do NOT count; "
            "when not CERTAIN, use false,\n"
            ' "needs_reply": true ONLY if the newest message at the bottom of '
            "the Discord conversation is an incoming message from someone else "
            "that has not been answered,\n"
            ' "reply": when needs_reply is true, the reply written AS THE '
            "USER — same language as the conversation, mimic the user's "
            "texting style; 1 to 3 chat messages separated by the single "
            "word NEXTMSG, each at most 2 sentences and typically 4-8 words "
            "(never a bare 1-2 word reply unless truly fitting), casual "
            "Discord texting tone mirroring the other person's mood, almost no "
            "punctuation (no commas, no trailing period); no signatures, no "
            "AI mentions — else null."
        )
        client = genai.Client(api_key=self._api_key)
        resp = client.models.generate_content(
            model=_REST_MODEL,
            contents=[gtypes.Part.from_bytes(data=jpg, mime_type="image/jpeg"), prompt],
        )
        text = (resp.text or "").strip()
        if "{" in text and "}" in text:
            text = text[text.find("{"): text.rfind("}") + 1]
        data = json.loads(text)
        if not data.get("chat_visible"):
            return "NOSCREEN"
        reply = (data.get("reply") or "").strip()
        if not data.get("needs_reply") or not reply:
            return "STANDBY"
        return reply

    def close(self) -> None:
        pass


# ── Takeover Loop ─────────────────────────────────────────────────────────────
def _discord_takeover_loop(user_instruction: str, duration_s: float, player,
                          stop_evt: threading.Event, target_desc: str = "") -> None:
    import pyautogui

    def _log(msg: str) -> None:
        if player:
            try:
                player.write_log(msg)
            except Exception:
                pass

    def _panel() -> None:
        if not player:
            return
        lines = []
        for them, mine in _takeover_state["exchanges"][-_PANEL_EXCHANGES:]:
            if them:
                lines.append(f"→ Them: {them}")
            lines.append(f"← JARVIS: {mine}")
            lines.append("")
        header = f"💬 DISCORD TAKEOVER{f' — {target_desc}' if target_desc else ''}"
        try:
            player.show_content(header, "\n".join(lines).strip())
        except Exception:
            pass

    api_key = _get_gemini_key()
    cfg = _config()
    os_name = cfg.get("os_system", "windows").lower()
    ended = "finished"
    engine = None

    try:
        focus_id = None
        system = _DISCORD_LIVE_SYSTEM
        if user_instruction:
            system += (f'\nExtra instruction from the user for how to reply: '
                       f'"{user_instruction}"')

        pref_engine = cfg.get("discord_takeover_engine") or cfg.get("chat_takeover_engine", "live")
        if pref_engine == "rest":
            engine = _RestEngine(api_key, user_instruction)
            _takeover_state["engine"] = "rest"
        else:
            engine = _LiveEngine(api_key, system)
            _takeover_state["engine"] = "live"

        target_note = f" in '{target_desc}'" if target_desc else ""
        _log(f"JARVIS: Discord chat takeover armed ({_takeover_state['engine']} engine){target_note} — "
             f"monitoring Discord and auto-replying every {_POLL_SECONDS}s.")

        recent_replies = []
        last_burst = ""
        seen_chat = False
        prev_raw = None
        misses = 0
        errors = 0
        deadline = time.time() + duration_s

        while not stop_evt.is_set() and time.time() < deadline:
            try:
                raw, jpg = _grab_screen()

                if prev_raw is not None and not _screen_changed(prev_raw, raw):
                    stop_evt.wait(_POLL_SECONDS)
                    continue
                prev_raw = raw

                verdict = engine.check(jpg, recent_replies).strip().strip('"\'')
                errors = 0
                upper = verdict.upper().rstrip(".!")

                if not verdict or upper.startswith("STANDBY"):
                    if not seen_chat and verdict:
                        _log("JARVIS: Discord chat in sight — monitoring the conversation now.")
                    seen_chat = True
                    misses = 0

                elif upper.startswith("NOSCREEN"):
                    if not seen_chat:
                        if misses == 0:
                            _log("JARVIS: No Discord chat visible yet — waiting for you to open Discord.")
                        misses = 1
                    else:
                        misses += 1
                        if misses >= _MAX_MISSES:
                            ended = "the Discord chat window disappeared from the screen"
                            _log("JARVIS: Discord chat takeover ended — I can no longer see the conversation.")
                            break

                elif len(verdict) > _MAX_REPLY_CHARS:
                    _log(f"JARVIS: Skipped an over-long Discord engine output ({len(verdict)} chars).")

                elif verdict == last_burst:
                    pass

                else:
                    seen_chat = True
                    misses = 0
                    sent = []
                    parts = _split_messages(verdict)
                    for i, part in enumerate(parts):
                        if i and stop_evt.wait(_BURST_GAP):
                            break
                        cur_win = _active_window_id()
                        if focus_id is None:
                            focus_id = cur_win
                            if focus_id is not None:
                                _log("JARVIS: Focus guard locked onto Discord window.")
                        elif cur_win not in (None, focus_id) and not _is_discord_active():
                            _log("JARVIS: Reply held — focus is not on Discord. Switch back to Discord to continue.")
                            break
                        part = _casualize(part)
                        _send_reply(part, os_name)
                        sent.append(part)
                        _takeover_state["exchanges"].append(("", part))
                    if sent:
                        last_burst = verdict
                        recent_replies.append(" / ".join(sent))
                        del recent_replies[:-3]
                        prev_raw = None
                        _log(f"JARVIS: Discord auto-replied ({len(sent)} message"
                             f"{'s' if len(sent) > 1 else ''}) — "
                             f"“{' | '.join(sent)[:120]}”")
                        _panel()

            except pyautogui.FailSafeException:
                ended = "failsafe (mouse in screen corner)"
                _log("JARVIS: Discord chat takeover aborted by failsafe corner.")
                break
            except Exception as e:
                errors += 1
                _log(f"JARVIS: Discord takeover hiccup ({errors}/{_MAX_ERRORS}): {e}")
                if errors >= _MAX_ERRORS:
                    if _takeover_state["engine"] == "live":
                        try:
                            engine.close()
                        except Exception:
                            pass
                        engine = _RestEngine(api_key, user_instruction)
                        _takeover_state["engine"] = "rest"
                        errors = 0
                        _log("JARVIS: Live engine unavailable — switched to Discord REST fallback engine.")
                    else:
                        ended = f"too many consecutive errors (last: {e})"
                        break

            stop_evt.wait(_POLL_SECONDS)

        if not stop_evt.is_set() and time.time() >= deadline:
            ended = "time limit reached"
        if stop_evt.is_set():
            ended = "stopped by the user"
    finally:
        if engine is not None:
            try:
                engine.close()
            except Exception:
                pass
        _takeover_state["ended"] = ended
        _log(f"JARVIS: Discord chat takeover over ({ended}) — "
             f"{len(_takeover_state['exchanges'])} replies sent this run.")


# ── Takeover Intent & Target Parsing ──────────────────────────────────────────
_TAKEOVER_STOP_PATTERNS = [
    r"\b(?:stop\s+takeover|stop\s+replying|take\s+(?:the\s+conversation\s+|chat\s+)?back|i'll\s+take\s+it\s+from\s+here)\b",
    r"\b(?:konu[sş]ma(?:y[ıi]|s[ıi]n[ıi])?\s+geri\s+al|sohbeti\s+geri\s+al|devri\s+b[ıi]rak|devralmay[ıi]\s+b[ıi]rak|takeover['\s]?ı?\s+durdur)\b",
    r"\b(?:stop\s+discord\s+takeover|discord\s+takeover['\s]?ı?\s+durdur)\b",
]

_TAKEOVER_STATUS_PATTERNS = [
    r"\b(?:takeover\s+status|is\s+(?:the\s+)?(?:discord\s+)?takeover\s+running|devir\s+durumu|devralma\s+durumu)\b",
    r"\b(?:takeover\s+durumu|takeover\s+ne\s+durumda|discord\s+takeover\s+aktif\s+mi)\b",
]

_TAKEOVER_START_PATTERNS = [
    r"\b(?:take\s*over|devral|devral[ıi]r\s*m[ıi]s[ıi]n|sen\s+devam\s+et|konu[sş]ma(?:y[ıi]|s[ıi]n[ıi])?\s+devral|sohbeti\s+devral)\b",
    r"\b(?:answer\s+(?:him|her|them)\s+for\s+me|reply\s+for\s+me|auto\s*reply)\b",
    r"\b(?:take\s+over\s+this\s+(?:chat|conversation|discord))\b",
]


def _classify_intent(action: str, request: str) -> str:
    act = (action or "").strip().lower()
    req = (request or "").strip().lower()

    if act in ("takeover_stop", "stop"):
        return "stop"
    if act in ("takeover_status", "status"):
        return "status"
    if act in ("takeover_start", "start"):
        return "start"
    if act == "send":
        return "send"

    for p in _TAKEOVER_STOP_PATTERNS:
        if re.search(p, req):
            return "stop"
    for p in _TAKEOVER_STATUS_PATTERNS:
        if re.search(p, req):
            return "status"
    for p in _TAKEOVER_START_PATTERNS:
        if re.search(p, req):
            return "start"

    return "send"


def _has_specific_takeover_target(text: str) -> bool:
    t = text.lower()
    t = re.sub(r"\b(?:in|on)?\s*discord(?:'da|'ta|'de|'te)?\b", "", t)
    t = re.sub(r"\b(?:take\s*over|devral|konu[sş]ma(?:y[ıi]|s[ıi]n[ıi])?|sohbeti?|this|chat|conversation)\b", "", t)
    t = t.strip()
    return bool(re.search(r"\b(?:with|ile|[#@]|server|sunucu|channel|kanal)\b", t)
                or (len(t.split()) > 0 and not re.match(r"^(?:please|hey|can you|could you|sen devam et)?$", t)))


def _parse_takeover_request(request: str, api_key: str) -> dict:
    from google import genai
    prompt = (
        "The user wants JARVIS to take over a Discord conversation and auto-reply. "
        "Extract any specific destination to navigate to, and any instructions for "
        "how to reply.\n"
        f'REQUEST: "{request}"\n'
        "Return ONLY minified JSON, no markdown fences, with keys:\n"
        ' "contact": name of person for DM, or null if not specified or server channel,\n'
        ' "server": Discord server name, or null if not specified or DM,\n'
        ' "channel": channel name within that server, or null,\n'
        ' "instruction": extra instructions for how to reply (e.g. "tell them I am in a meeting", '
        '"be casual"), or null if no extra instructions were given.'
    )
    client = genai.Client(api_key=api_key)
    resp = _generate_with_retry(client, _TEXT_MODEL, prompt)
    text = (resp.text or "").strip()
    if "{" in text and "}" in text:
        text = text[text.find("{"): text.rfind("}") + 1]
    return json.loads(text)


def _fallback_parse_takeover_request(request: str) -> dict:
    text = request.strip()
    instruction = ""
    m_say = _SAY_SPLIT_RE.search(text)
    if m_say and m_say.start() > 0:
        instruction = text[m_say.end():].strip()
        head = text[:m_say.start()]
    else:
        head = text

    contact = None
    m_with = re.search(r"\bwith\s+([a-zA-Z0-9_' -]{2,30})\b", head, re.IGNORECASE)
    if m_with:
        cand = re.sub(r"\b(?:on\s+)?discord\b", "", m_with.group(1), flags=re.IGNORECASE).strip()
        if _looks_like_a_name(cand):
            contact = cand
    else:
        m_ile = re.search(r"([a-zA-Z0-9_' -]{2,30})\s+ile\b", head, re.IGNORECASE)
        if m_ile:
            cand = re.sub(r"\b(?:on\s+)?discord(?:'da|'de)?\b", "", m_ile.group(1), flags=re.IGNORECASE).strip()
            if _looks_like_a_name(cand):
                contact = cand

    server = None
    channel = None
    m_srv = re.search(r"\bserver\b", head, re.IGNORECASE)
    if m_srv:
        sm = _SERVER_PHRASE_RE.search(head[:m_srv.end()])
        if sm:
            cand = _clean_phrase(sm.group(1))
            if _looks_like_a_name(cand):
                server = cand
        ch_scope = head[m_srv.end():]
    else:
        ch_scope = head
    if re.search(r"\b(chat|channel)\b", ch_scope, re.IGNORECASE):
        cm = _CHANNEL_PHRASE_RE.search(ch_scope)
        if cm:
            cand = _clean_phrase(cm.group(1))
            if _looks_like_a_name(cand) and cand.lower() not in ("take over", "take over this"):
                channel = cand

    return {"contact": contact, "server": server, "channel": channel, "instruction": instruction}


# ── Main Entry Point ─────────────────────────────────────────────────────────
def run(parameters: dict, player=None, session_memory=None) -> str:
    raw_action = (parameters.get("action") or "").strip().lower()
    request = (parameters.get("request") or parameters.get("query") or "").strip()
    duration_minutes = parameters.get("duration_minutes")

    _ensure_dpi_aware()

    def _log(msg: str) -> None:
        if player:
            try:
                player.write_log(msg)
            except Exception:
                pass

    intent = _classify_intent(raw_action, request)

    # ── 1. Discord Chat Takeover: STOP ────────────────────────────────────────
    if intent == "stop":
        with _takeover_lock:
            if not _takeover_running():
                return "There is no Discord chat takeover running right now, sir."
            _takeover_state["stop"].set()
        _takeover_state["thread"].join(timeout=5)
        n = len(_takeover_state["exchanges"])
        target = _takeover_state.get("target")
        loc = f" in {target}" if target else ""
        return (f"The Discord conversation{loc} is yours again, sir. I sent {n} "
                f"repl{'y' if n == 1 else 'ies'} while I had it.")

    # ── 2. Discord Chat Takeover: STATUS ──────────────────────────────────────
    if intent == "status":
        if _takeover_running():
            n = len(_takeover_state["exchanges"])
            eng = _takeover_state.get("engine") or "live"
            target = _takeover_state.get("target")
            loc = f" in {target}" if target else ""
            return (f"Discord chat takeover is active{loc} on the {eng} engine — "
                    f"{n} repl{'y' if n == 1 else 'ies'} sent so far.")
        last = _takeover_state.get("ended")
        return ("No Discord chat takeover is running at the moment."
                + (f" The last one ended: {last}." if last else ""))

    # ── 3. Discord Chat Takeover: START ───────────────────────────────────────
    if intent == "start":
        with _takeover_lock:
            if _takeover_running():
                if time.time() - _takeover_state.get("started_at", 0) < _START_DEBOUNCE:
                    return ("(Duplicate start call ignored — the Discord chat takeover is "
                            "already active and was already announced. Do NOT announce it again; "
                            "stay silent.)")
                return ("I'm already handling that Discord conversation, sir — say "
                        "the word if you want it back.")

            api_key = _get_gemini_key()
            if not api_key:
                return "I can't take over the Discord chat — no Gemini API key is configured."

            try:
                import pyautogui  # noqa: F401
                import pygetwindow  # noqa: F401
                import pyperclip  # noqa: F401
                import PIL  # noqa: F401
            except ImportError as e:
                return (f"A required library is missing for Discord takeover: {e}. "
                        f"Please install it with: pip install pyautogui pygetwindow pyperclip pillow mss")

            try:
                minutes = float(duration_minutes or _DEFAULT_MINUTES)
            except (TypeError, ValueError):
                minutes = _DEFAULT_MINUTES
            minutes = max(1.0, min(minutes, 120.0))

            target_desc = ""
            user_instruction = ""

            # Check if user specified a specific contact or channel
            if request and _has_specific_takeover_target(request):
                try:
                    spec = _parse_takeover_request(request, api_key)
                except Exception as e:
                    if _is_transient_error(e):
                        spec = _fallback_parse_takeover_request(request)
                    else:
                        spec = {}

                contact = (spec.get("contact") or "").strip() or None
                server = (spec.get("server") or "").strip() or None
                channel = (spec.get("channel") or "").strip() or None
                user_instruction = (spec.get("instruction") or "").strip()

                if contact or server:
                    ok, err_msg, dest_name = _navigate_discord_destination(
                        server, channel, contact, api_key, log_fn=_log
                    )
                    if not ok:
                        return f"I stopped before taking over — {err_msg}"
                    target_desc = dest_name
                else:
                    if not _focus_discord():
                        return "I couldn't find or open the Discord desktop app — is it installed?"
            else:
                if not _focus_discord():
                    return "I couldn't find or open the Discord desktop app — is it installed?"

            _takeover_state["exchanges"] = []
            _takeover_state["ended"] = ""
            _takeover_state["engine"] = ""
            _takeover_state["target"] = target_desc
            _takeover_state["started_at"] = time.time()
            _takeover_state["stop"] = threading.Event()
            _takeover_state["thread"] = threading.Thread(
                target=_discord_takeover_loop,
                args=(user_instruction, minutes * 60, player, _takeover_state["stop"], target_desc),
                daemon=True,
                name="discord-chat-takeover",
            )
            _takeover_state["thread"].start()

        target_str = f" in {target_desc}" if target_desc else ""
        return (f"Taking over the Discord conversation{target_str}, sir. "
                f"I'll watch the chat and answer incoming messages in your style for up to "
                f"{minutes:.0f} minutes. Say the word when you want it back.")

    # ── 4. Standard Discord Send: Check Pending Manual Confirmation ───────────
    if not request:
        return "Who do you want me to message on Discord, or would you like me to take over a conversation?"

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
            _clear_pending()

    # ── 5. Standard Discord Send: Parse & Send Message ─────────────────────────
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

    ok, err_msg, destination = _navigate_discord_destination(
        server, channel, contact, api_key, log_fn=_log
    )
    if not ok:
        return f"I stopped before sending — {err_msg} Nothing was sent."

    import pyautogui

    try:
        # Type the message into the composer
        pyautogui.press("escape")
        time.sleep(0.15)
        _type_segments(segments)
        time.sleep(_TYPE_SETTLE_S)
    except Exception as e:
        return f"Something went wrong controlling Discord: {e}"

    try:
        verdict = _verify_before_send(destination, segments, api_key)
    except Exception as e:
        if _is_transient_error(e):
            _save_pending(destination, message)
            _log(f"JARVIS: Vision check unavailable, asking for manual confirmation on '{destination}'.")
            return (f"Gemini's vision check is unavailable right now, so I "
                    f"can't verify the screen myself. I've typed this to "
                    f"{destination} but haven't sent it yet: \"{message}\". "
                    f"Is this the correct person and message? Say yes to "
                    f"send it, or no to cancel.")
        verdict = {"ok": False, "reason": "vision check failed to run"}

    if not verdict.get("ok"):
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
        pyautogui.press("enter")
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