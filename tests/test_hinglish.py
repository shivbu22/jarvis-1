"""Tests for Hinglish Language Pack and Multi-lingual Intent Routing."""

from apps.gateway.hinglish import (
    is_hinglish_or_hindi,
    normalize_hinglish_intent,
    resolve_tts_voice,
)


def test_is_hinglish_detection():
    # Pure English
    assert not is_hinglish_or_hindi("Open the browser and show me Google.")
    assert not is_hinglish_or_hindi("List all files in the current workspace.")

    # Devanagari Hindi
    assert is_hinglish_or_hindi("नमस्ते जार्विस, आप कैसे हैं?")
    assert is_hinglish_or_hindi("ब्राउज़र खोलो")

    # Latin-script Hinglish
    assert is_hinglish_or_hindi("Jarvis, browser kholo aur YouTube chalao")
    assert is_hinglish_or_hindi("Kaun se apps khule hain?")
    assert is_hinglish_or_hindi("Camera se photo khincho")
    assert is_hinglish_or_hindi("Volume thoda badhao")


def test_normalize_hinglish_intent():
    # Category A: Desktop & Windows
    assert normalize_hinglish_intent("browser kholo") == "open the browser"
    assert normalize_hinglish_intent("kaun se apps khule hain") == "list active desktop windows"
    assert normalize_hinglish_intent("screenshot lelo") == "take a desktop screenshot"
    assert normalize_hinglish_intent("desktop dikhao") == "show my desktop and minimize all windows"
    assert normalize_hinglish_intent("notepad kholo") == "open notepad"
    assert normalize_hinglish_intent("system lock karo") == "lock the computer"
    assert normalize_hinglish_intent("कंप्यूटर लॉक करो") == "lock the computer"
    assert normalize_hinglish_intent("VS code kholo") == "open VS Code"

    # Category B: Music & Media
    assert normalize_hinglish_intent("gaana bajao") == "play music on YouTube"
    assert normalize_hinglish_intent("lofi gaana bajao") == "play lofi music on YouTube"
    assert normalize_hinglish_intent("gaana wapas chalao") == "resume music playback"
    assert normalize_hinglish_intent("awaz badhao") == "increase system volume"
    assert normalize_hinglish_intent("awaz kam karo") == "decrease system volume"
    assert normalize_hinglish_intent("volume 50 percent set karo") == "set system volume to 50 percent"

    # Category C: Browser & Web
    assert normalize_hinglish_intent("github kholo") == "open github.com"
    assert normalize_hinglish_intent("yeh webpage padh ke batao") == "read the content of this webpage"
    assert normalize_hinglish_intent("open google.com") == "open google.com"

    # Category D: Weather & News
    assert normalize_hinglish_intent("aaj ka mausam kaisa hai") == "check the weather"
    assert normalize_hinglish_intent("Mumbai ka mausam kaisa hai") == "what is the weather in Mumbai"
    assert normalize_hinglish_intent("taaza khabar sunao") == "tell me the latest news headlines"

    # Category F: Obsidian
    assert normalize_hinglish_intent("obsidian me venture ideas dhoondo") == "search obsidian brain vault for venture ideas"

    # Category G: Quant & Markets
    assert normalize_hinglish_intent("nifty 50 aur sensex ka haal batao") == "fetch market data for Indian indices"
    assert normalize_hinglish_intent("bitcoin ka rate batao") == "what is Bitcoin price right now"
    assert normalize_hinglish_intent("mera portfolio dikhao") == "show my paper trading portfolio"

    # Category H: System Health
    assert normalize_hinglish_intent("system ka status batao kitna ram free hai") == "report system status and hardware vitals"

    # Category I: Persona & Banter
    assert normalize_hinglish_intent("shubh prabhat jarvis") == "Good morning Jarvis"
    assert normalize_hinglish_intent("jarvis tayyar ho") == "Are you ready, Jarvis?"
    assert normalize_hinglish_intent("tum kaun ho") == "Who are you?"


def test_resolve_tts_voice():
    default = "en-GB-RyanNeural"
    hinglish = "en-IN-PrabhatNeural"

    assert resolve_tts_voice("All systems are operational, sir.", default, hinglish) == default
    assert resolve_tts_voice("Bilkul sir, browser open kar diya hai.", default, hinglish) == hinglish
    assert resolve_tts_voice("नमस्ते सर, सब ठीक है।", default, hinglish) == hinglish
