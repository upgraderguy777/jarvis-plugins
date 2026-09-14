# ⚡ JARVIS (Fatihmakes) Plugins

A modular, drop-in plugin suite built for voice-controlled AI assistants (JARVIS). 

These plugins were completely **vibecoded** with one primary design philosophy in mind: **Zero Waste / Free-Tier Optimization**. Every plugin is heavily engineered to avoid token burn—leveraging native operating system APIs, accessibility trees, local fallbacks, and downscaled vision passes so you can run an advanced personal assistant without exhausting free API rate limits or incurring heavy costs.

Note: These plugins are made for Jarvis by the king Fatihmakes, I did not create the ai, I only made some custom plugins, and this is solo made but vibe coded.

These plugins were recently tested for compatibility in the model: **Mark 52**

---

## 🚀 Key Highlights & Philosophy

- 🆓 **Engineered for Free Tier Users:** Designed specifically for users relying on free API quotas. High-cost vision calls are aggressively downscaled, repetitive UI tasks use local OS automation (zero tokens), and debate loops feature early-exit consensus.
- 🔌 **Drop-in Architecture:** Built following the standard JARVIS loader format. Drop any `.py` plugin into your plugins directory, and JARVIS auto-discovers it.
- 🧠 **Multi-Modal & Hybrid:** Blends native Win32/WinRT system APIs with lightweight Gemini models (`gemini-3.5-flash-lite`, `gemini-2.5-flash`).

---

## 🛠️ Plugin Directory & Compatibility

| Plugin | Operating System | Primary Dependencies | Token Impact |
| :--- | :--- | :--- | :--- |
| **`notification_reader.py`** | Windows 10 (1607+) / 11 | `winsdk` *(auto-installed)* | **Zero Tokens** |
| **`screen_recorder.py`** | Windows (Full) / macOS & Linux (Voice only) | `opencv-python`, `mss`, `sounddevice` | **Zero Tokens** |
| **`screenshot_annotate.py`** | Cross-Platform (Windows, macOS, Linux) | `mss`, `Pillow`, `google-genai` | **Low** *(2-Pass Optimized)* |
| **`discord_messenger.py`** | Windows 10 / 11 | `pyautogui`, `pywinauto`, `pyperclip` | **Zero-to-Low** *(Cached/Local)* |
| **`magi_system.py`** | Cross-Platform (Windows, macOS, Linux) | `google-genai` | **Ultra-Low** *(Flash-Lite Tier)* |

---

## 📦 Detailed Plugin Breakdown

### 1. 🔔 Notification Reader (`notification_reader.py`)
* **OS Support:** Windows 10 (Build 1607+) and Windows 11 only.
* **Token Cost:** **0 Tokens** (Pure Windows Runtime API).
* **Auto-Install:** Automatically installs `winsdk` via `pip` on first launch if missing.

Reads toasts and alerts sitting in the Windows Action/Notification Center without needing app-specific API bot tokens or terms-of-service-violating self-bots.

* **Actions:**
  * `check`: Checks for new notifications that arrived since the last check.
  * `list`: Reads the last 10 notifications currently in the Action Center.
  * `status`: Verifies if Windows OS notification access permission is granted.
  * `watch`: Background thread watches for incoming notifications matching a specific keyword or application name (e.g., *"Let me know if John mentions the meeting on Slack"*).
  * `unwatch`: Clears active background alert watchers.
  * `watches`: Lists all current active pattern filters.

---

### 2. 🎥 Screen Recorder (`screen_recorder.py`)
* **OS Support:** 
  * **Windows:** Full feature set (Voice control + System-wide Win32 hotkeys).
  * **macOS / Linux:** Voice control supported (Hotkeys skipped due to Win32 dependency).
* **Token Cost:** **0 Tokens** (Pure mechanism, no AI vision needed).

Records high-framerate desktop video over time with optional microphone narration, outputting directly to the user's `Downloads` folder.

* **Control Modes:**
  * **Voice Commands:** *"Start recording my screen"*, *"Pause recording"*, *"Resume"*, *"Stop recording"*.
  * **Global Windows Hotkeys:** 
    * `Win + Alt + R` → Instant toggle Start / Stop.
    * `Win + Alt + P` → Instant toggle Pause / Resume.
* **Features:** Automatic audio muxing via `ffmpeg` (falls back gracefully to video-only if absent) and safety auto-stop limits.

---

### 3. 🎯 Screenshot & Annotate (`screenshot_annotate.py`)
* **OS Support:** Cross-Platform (Windows, macOS, Linux).
* **Token Cost:** **Low** (Downscales full-screen image + runs localized crops).

A visual teaching and UI-finding tool. When you can't find a button or menu item, JARVIS takes a screenshot, asks Gemini where the element is, draws numbered orange indicator rings around the control, provides spoken directions, and opens the image instantly in your default viewer.

* **Optimizations:**
  * **Two-Pass Refinement:** Uploads an initial downscaled image (1400px max) for the rough coordinate, then takes a micro-crop around that coordinate to pinpoint the button down to the exact pixel. Saves massive token bandwidth while avoiding coarse bounding box errors.

---

### 4. 💬 Discord Messenger (`discord_messenger.py`)
* **OS Support:** Windows 10 and 11 only (Controls the official Discord Desktop client).
* **Token Cost:** **Zero-to-Low** (Uses accessibility trees and local regex before ever calling an LLM).

Allows full hands-free Discord messaging to DMs or server channels, including functional `@everyone`, `@here`, and user `@mentions`.

* **Multi-Tier Zero-Token Architecture:**
  1. **Tier 1 (Saved Aliases):** Deep links (`discord://...`) jump directly to channels with zero search or API calls.
  2. **Tier 2 (Accessibility Tree Navigation):** Reads the Discord sidebar directly through the Windows UI Automation tree via `pywinauto`—instantly switching channels without AI vision.
  3. **Tier 3 (Vision Fallback):** Falls back to Gemini vision only when ambiguous channel searches occur.
  4. **Overload Fallback:** If Gemini hits a 429/503 rate-limit error, an internal regex parser extracts the message and queues it safely for a local confirmation prompt (`yes`/`no`).

---

### 5. 🖥️ MAGI System (`magi_system.py`)
* **OS Support:** Cross-Platform (Windows, macOS, Linux).
* **Token Cost:** **Ultra-Low** (Powered by `gemini-3.5-flash-lite`).

An authentic homage to *Neon Genesis Evangelion*'s MAGI supercomputer. Decisions are submitted to three distinct AI personas:
* **MELCHIOR-1 (The Scientist):** Pure logic, efficiency, empirical probability.
* **BALTHASAR-2 (The Mother):** Protection, safety, human care, risk aversion.
* **CASPER-3 (The Woman):** Personal ambition, authentic desire, raw instinct.

* **Deliberation Modes:**
  * `quick`: 1-round independent vote (all three units vote simultaneously in parallel).
  * `thinking`: Multi-round debate. Units review what the other two voted in round 1 and can maintain or revise their positions over up to 3 rounds.
  * `max`: Deep interactive debate loop. Units take turns making claims, countering or agreeing, and synthesizing new counter-claims.
  * **Early Consensus Exit:** In `max` mode, as soon as all three units agree on a stance (all APPROVE or all DENY), the debate terminates immediately to eliminate redundant tokens.

---

## 🔮 Coming Soon: Project ULTRON

In active development by **Gemind**:

> **The ULTRON Plugin:** An autonomous, unrestricted system plugin designed to unleash maximum capability with zero operational limits. 
> 
> * **Zero-Token Deep Research:** Features a specialized, hours-long autonomous research engine that browses, digests, and compiles complex technical research locally without eating through API token quotas.

---

## ⚙️ Installation

Press the green button and download as a zip, then extract and put the .py files inside the python folder
