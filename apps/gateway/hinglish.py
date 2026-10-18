"""Hinglish Language Pack & Multi-lingual Intent Routing for J.A.R.V.I.S.

Provides:
1. Devanagari & Latin-script Hinglish detection.
2. Colloquial intent pre-processing and translation into canonical tool directives.
3. Intelligent speech voice selection between authentic British Ryan and Indian Neural voices.
"""

from __future__ import annotations

import re
from typing import Any

# Devanagari unicode range: \u0900 - \u097F
_DEVANAGARI_REGEX = re.compile(r"[\u0900-\u097F]")

# Common Hinglish conversational words, pronouns, verbs, and question particles
_HINGLISH_MARKERS = {
    "karo", "karna", "karein", "kijiye", "kar", "karta", "karti", "kardo", "karva",
    "kholo", "kholna", "khol", "kholiye", "kholdo",
    "band", "roko", "rok", "bandh", "rokdo", "bandkaro",
    "chalao", "chalana", "chala", "chalado", "bajao", "bajana", "baja", "bajado",
    "dikhao", "dekhna", "dekho", "batao", "batana", "sunao", "sunana", "dhoondo", "khojo",
    "kaisa", "kaisi", "kaise", "kyun", "kya", "kab", "kahan", "kaun", "kitna", "kitni",
    "hai", "hain", "tha", "thi", "the", "hoga", "hogi", "honge", "chahiye", "chahta",
    "accha", "achha", "theek", "shukriya", "dhanyawad", "namaste", "alvida", "shabaash",
    "aaj", "kal", "abhi", "jaldi", "thoda", "bahut", "zara", "turant",
    "awaz", "awaaz", "tasveer", "photo", "khincho", "badhao", "ghatao", "kam", "tez", "dheemi",
    "likho", "likhna", "padho", "padhna", "bhejo", "bhejna", "yaad", "rakhna", "rakho",
    "kaam", "pucho", "samjhao", "madad", "parda", "parde", "saaf", "gaana", "gaane", "khabar", "mausam",
    "bhav", "haal",
}

# Regex for common voice intent transformations from colloquial Hinglish/Hindi to tool directives
_INTENT_TRANSFORMS = [
    # Category A: Desktop & Windows Operating System Control
    (re.compile(r"\b(?:desktop|screen)\s+(?:dikhao|show\s*karo|saaf\s*karo)\b|(?:\b(?:saare|all)\s*windows\s*(?:minimize|chhoti)\s*karo\b)|\b(?:parde\s*gira\s*do)\b|डेस्कटॉप\s*दिखाओ|सारी\s*खिड़कियां\s*छोटी\s*करो", re.I), "show my desktop and minimize all windows"),
    (re.compile(r"\b(?:notepad)\s+(?:kholo|open\s*karo|chalao)\b|नोटपैड\s*खोलो", re.I), "open notepad"),
    (re.compile(r"\b(?:calculator|calc)\s+(?:kholo|open\s*karo|chalao)\b|कैलकुलेटर\s*खोलो", re.I), "open calculator"),
    (re.compile(r"\b(?:vscode|vs\s*code|code\s*editor)\s+(?:kholo|open\s*karo|chalao)\b|वीएस\s*कोड\s*खोलो", re.I), "open VS Code"),
    (re.compile(r"\b(?:terminal|powershell|cmd)\s+(?:kholo|open\s*karo|chalao)\b|टर्मिनल\s*खोलो", re.I), "open terminal"),
    (re.compile(r"\b(?:active|khule\s*huye|open)\s*(?:windows|apps|applications)\s*(?:dikhao|list\s*karo|batao)\b|\b(?:kaun\s*se\s*apps\s*khule\s*hain)\b|सक्रिय\s*खिड़कियां\s*दिखाओ|कौन\s*से\s*ऐप्स\s*खुले\s*हैं", re.I), "list active desktop windows"),
    (re.compile(r"\b(?:screenshot|screen\s*shot)\s*(?:lo|lelo|khencho|khincho|karo)\b|स्क्रीनशॉट\s*लो|स्क्रीन\s*की\s*तस्वीर\s*लो", re.I), "take a desktop screenshot"),
    (re.compile(r"\b(?:camera|webcam)\s*(?:se\s*photo|tasveer|pic)\s*(?:lo|khincho|khencho)\b|कैमरा\s*से\s*फोटो\s*(?:लो|खींचो)", re.I), "capture a frame from the camera"),
    (re.compile(r"\b(?:system|pc|computer|workstation)\s*(?:lock\s*karo|lock\s*kar\s*do|band\s*karo)\b|कंप्यूटर\s*लॉक\s*करो|सिस्टम\s*लॉक\s*करो", re.I), "lock the computer"),

    # Category B: Music, Audio & Media Playback
    (re.compile(r"\b(?:lofi|lo-fi)\s*(?:gaana|music|song)\s*(?:bajao|chalao|sunao)\b|यूट्यूब\s*पर\s*लो-?फाई\s*गाने\s*बजाओ", re.I), "play lofi music on YouTube"),
    (re.compile(r"\b(?:youtube|spotify)?\s*(?:par|pe)?\s*(.+?)\s*(?:ka\s*)?(?:gaana|song|music|track)\s*(?:bajao|chalao|sunao)\b", re.I), r"play \1 on YouTube"),
    (re.compile(r"\b(?:gaana|gaane|music|song)\s*(?:bajao|chalao|sunao)\b|गाना\s*(?:बजाओ|चलाओ|सुनाओ)", re.I), "play music on YouTube"),
    (re.compile(r"\b(?:gaana|music|playback)\s*(?:roko|band\s*karo|pause\s*karo)\b|गाना\s*बंद\s*करो|संगीत\s*रोको", re.I), "pause music playback"),
    (re.compile(r"\b(?:gaana|music|playback)\s*(?:wapas|phir\s*se)?\s*(?:chalao|resume\s*karo)\b|गाना\s*फिर\s*से\s*चलाओ", re.I), "resume music playback"),
    (re.compile(r"\b(?:agla|next)\s*(?:gaana|song|track)\s*(?:chalao|bajao|lagao)?\b|अगला\s*गाना", re.I), "play next song"),
    (re.compile(r"\b(?:awaz|awaaz|volume)\s*(?:badhao|tez\s*karo|increase\s*karo|up\s*karo)\b|आवाज़\s*बढ़ाओ|वॉल्यूम\s*बढ़ाओ|वॉल्यूम\s*तेज़\s*करो", re.I), "increase system volume"),
    (re.compile(r"\b(?:awaz|awaaz|volume)\s*(?:kam\s*karo|dheemi\s*karo|decrease\s*karo|down\s*karo)\b|आवाज़\s*कम\s*करो|आवाज़\s*धीमी\s*करो|वॉल्यूम\s*कम\s*करो", re.I), "decrease system volume"),
    (re.compile(r"\b(?:mute\s*karo|awaz\s*band\s*karo|chup\s*ho\s*jao)\b|आवाज़\s*बंद\s*करो|म्यूट\s*करो", re.I), "mute system volume"),
    (re.compile(r"\b(?:volume|awaaz)\s*(\d+)\s*(?:percent|%|pratishat)\s*(?:par|pe)?\s*(?:set\s*karo|karo)\b|वॉल्यूम\s*(\d+)\s*प्रतिशत\s*करो", re.I), r"set system volume to \1 percent"),

    # Category C: Browser & Web Research
    (re.compile(r"\b(?:browser|chrome|edge)\s+(?:kholo|open\s*karo|chalao)\b|क्रोम\s*(?:ब्राउज़र\s*)?खोलो", re.I), "open the browser"),
    (re.compile(r"\b(?:youtube)\s+(?:chalao|kholo|open\s*karo)\b|यूट्यूब\s*खोलो", re.I), "open YouTube in browser"),
    (re.compile(r"\b(?:github)\s+(?:kholo|open\s*karo|chalao)\b|गिटहब\s*खोलो", re.I), "open github.com"),
    (re.compile(r"\b(?:wikipedia)\s+(?:par\s*jao|kholo|chalao)\b|विकिपीडिया\s*खोलो", re.I), "open wikipedia.org"),
    (re.compile(r"\b(?:yeh\s*)?(?:webpage|web\s*page|page)\s*(?:padh\s*ke\s*batao|padho|summary\s*nikalo)\b|इस\s*वेब\s*पेज\s*को\s*पढ़कर\s*बताओ", re.I), "read the content of this webpage"),
    (re.compile(r"\b(?:google|web|internet)\s*(?:par|pe)?\s*(.+?)\s*(?:search\s*karo|dhoondo|khojo)\b|गूगल\s*पर\s*(.+?)\s*खोजो", re.I), r"search the web for \1"),

    # Category D: Weather, News & Productivity
    (re.compile(r"\b(?!aaj\b|kal\b)([a-zA-Z\u0900-\u097F]+?)\s*(?:ka\s*mausam|mein\s*mausam)\s*(?:kaisa\s*hai|batao)\b|([a-zA-Z\u0900-\u097F]+?)\s*में\s*मौसम\s*कैसा\s*है", re.I), r"what is the weather in \1"),
    (re.compile(r"\b(?:aaj\s*ka\s*)?(?:mausam|weather)\s*(?:kaisa\s*hai|batao|update\s*do)\b|आज\s*मौसम\s*कैसा\s*है|मौसम\s*की\s*जानकारी\s*दो", re.I), "check the weather"),
    (re.compile(r"\b(?:aaj\s*ki\s*)?(?:taaza\s*)?(?:khabar|news|headlines)\s*(?:sunao|batao)\b|आज\s*की\s*ताज़ा\s*खबरें\s*सुनाओ", re.I), "tell me the latest news headlines"),
    (re.compile(r"\b(?:tech|technology)\s*(?:news|ki\s*khabar|ki\s*khabrein)\s*(?:sunao|batao)\b|टेक्नोलॉजी\s*की\s*खबरें\s*बताओ", re.I), "tell me recent tech news"),
    (re.compile(r"\b(\d+)\s*ka\s*(\d+)\s*(?:percent|%|pratishat)\s*(?:kitna\s*hoga|nikalo|batao)\b|(\d+)\s*का\s*(\d+)\s*प्रतिशत\s*कितना\s*होगा", re.I), r"calculate \2 percent of \1"),
    (re.compile(r"\b(?:abhi\s*)?(?:kitna\s*time\s*hua\s*hai|kya\s*time\s*hai|time\s*batao|aaj\s*kaun\s*si\s*date\s*hai)\b|अभी\s*क्या\s*समय\s*हुआ\s*है|आज\s*कौन\s*सी\s*तारीख\s*है", re.I), "what time is it right now"),

    # Category E: Memory, Personalization & Notes
    (re.compile(r"\b(?:yeh\s*)?(?:yaad\s*rakhna|yaad\s*rakho|note\s*kar\s*lo|save\s*karo)\s*(?:ki|:)?\s*(.+)$|याद\s*रखना\s*(.+)", re.I), r"remember: \1"),
    (re.compile(r"\b(?:mere\s*baare\s*mein\s*kya\s*(?:pata|yaad)\s*hai|mere\s*notes\s*dikhao|meri\s*preferences\s*batao)\b|मेरे\s*बारे\s*में\s*क्या\s*याद\s*है|मेरी\s*पसंद\s*बताओ", re.I), "what do you remember about me"),
    (re.compile(r"\b(?:saari\s*)?(?:saved\s*)?(?:memories|notes)\s*(?:dikhao|list\s*karo)\b|सारे\s*सुरक्षित\s*नोट्स\s*दिखाओ", re.I), "list all stored memories"),

    # Category F: Obsidian Brain Vault Management
    (re.compile(r"\b(?:obsidian|brain\s*vault)\s*(?:me|mein)?\s*(.+?)\s*(?:search\s*karo|dhoondo)\b|ऑब्सिडियन\s*में\s*(.+?)\s*खोजो", re.I), r"search obsidian brain vault for \1"),
    (re.compile(r"\b(?:obsidian|brain\s*vault)\s*se\s*(.+?)\s*note\s*padho\b|ऑब्सिडियन\s*से\s*नोट\s*पढ़ो", re.I), r"read note \1 from brain vault"),
    (re.compile(r"\b(?:obsidian|brain\s*vault)\s*(?:me|mein)?\s*(.+?)\s*(?:naam\s*se\s*note\s*banao|note\s*likho)\b|ऑब्सिडियन\s*में\s*(.+?)\s*नोट\s*बनाओ", re.I), r"create a note in obsidian brain vault titled \1"),
    (re.compile(r"\b(?:obsidian\s*brain\s*lint\s*karo|links\s*check\s*karo)\b|ऑब्सिडियन\s*वॉल्ट\s*की\s*जांच\s*करो", re.I), "audit obsidian vault and fix broken links"),

    # Category G: Quant, Markets & Portfolio Intelligence
    (re.compile(r"\b(?:nifty(?:\s*50)?(?:\s*(?:aur|and|\&)\s*sensex)?|sensex|indian\s*market|stock\s*market)\s*(?:ka\s*haal(?:\s*batao)?|kaisa\s*hai|update\s*(?:batao|do)|ka\s*update)\b|निफ्टी\s*50\s*(?:और|व)\s*सेंसेक्स\s*का\s*(?:क्या\s*)?हाल\s*(?:है|बताओ)?", re.I), "fetch market data for Indian indices"),
    (re.compile(r"\b(?:bitcoin|btc)\s*(?:ka\s*rate|price\s*kya\s*hai|bhav|ka\s*price)(?:\s*batao|\s*kya\s*hai)?\b|बिटकॉइन\s*का\s*(?:क्या\s*)?रेट\s*(?:है|बताओ)?", re.I), "what is Bitcoin price right now"),
    (re.compile(r"\b(.+?)\s*(?:ka\s*stock\s*price|ka\s*bhav|ka\s*rate|ka\s*price)\s*(?:kya\s*(?:chal\s*raha\s*)?hai|batao)\b|(.+?)\s*के\s*शेयर\s*का\s*भाव\s*बताओ", re.I), r"get stock price for \1"),
    (re.compile(r"\b(?:mera\s*)?(?:portfolio|p&l\s*status)\s*(?:dikhao|batao)\b|मेरा\s*पोर्टफोलियो\s*दिखाओ", re.I), "show my paper trading portfolio"),

    # Category H: System Health, Self-Healing & Diagnostics
    (re.compile(r"\b(?:system|laptop|machine|hardware)\s*(?:ka\s*status(?:\s*batao)?|ki\s*health|kaisa\s*chal\s*raha\s*hai|ka\s*haal)?(?:[,\s]*(?:kitna\s*ram\s*free\s*hai|ram\s*usage|cpu\s*ram\s*status|status\s*batao))\b|सिस्टम\s*की\s*स्थिति\s*(?:और\s*रैम\s*उपयोग\s*)?बताओ", re.I), "report system status and hardware vitals"),
    (re.compile(r"\b(?:self\s*heal|system\s*repair)\s*(?:karo|chalao|diagnose\s*karo)\b|सेल्फ-हीलिंग\s*जांच\s*चलाओ", re.I), "run self-healing system diagnostics"),
    (re.compile(r"\b(?:self\s*heal\s*)?watchdog\s*(?:ka\s*status\s*dikhao|telemetry\s*batao)\b|वॉचडॉग\s*टेलीमेट्री\s*दिखाओ", re.I), "check self-healing watchdog status"),

    # Category I: Persona, Greetings & Conversational Banter
    (re.compile(r"\b(?:shubh\s*prabhat|good\s*morning)\s*(?:jarvis)?\b|शुभ\s*प्रभात\s*जार्विस", re.I), "Good morning Jarvis"),
    (re.compile(r"\b(?:jarvis\s*)?(?:tayyar\s*ho|ready\s*ho\s*kaam\s*ke\s*liye)\b|क्या\s*तुम\s*तैयार\s*हो\s*जार्विस", re.I), "Are you ready, Jarvis?"),
    (re.compile(r"\b(?:tum\s*kaun\s*ho|apne\s*baare\s*me\s*batao)\b|तुम\s*कौन\s*हो|अपने\s*बारे\s*में\s*बताओ", re.I), "Who are you?"),
    (re.compile(r"\b(?:shukriya\s*jarvis|bahut\s*badiya\s*kaam\s*kiya|shabaash|dhanyawad)\b|धन्यवाद\s*जार्विस|बहुत\s*बढ़िया\s*काम\s*किया", re.I), "Thank you Jarvis, good job"),
]


def is_hinglish_or_hindi(text: str) -> bool:
    """Detect if text contains Devanagari Hindi or Latin-script Hinglish."""
    if not text:
        return False
    # Check Devanagari script first
    if _DEVANAGARI_REGEX.search(text):
        return True
    # Tokenize into words
    words = set(re.findall(r"[a-zA-Z]+", text.lower()))
    matches = words.intersection(_HINGLISH_MARKERS)
    # If 2 or more Hinglish markers match, or 1 strong marker in a short phrase
    if len(matches) >= 2 or (len(words) <= 5 and len(matches) >= 1):
        return True
    return False


def normalize_hinglish_intent(text: str) -> str:
    """Normalize colloquial Hinglish voice command into a clean directive for LLMs."""
    cleaned = text.strip()
    for pattern, replacement in _INTENT_TRANSFORMS:
        if pattern.search(cleaned):
            cleaned = pattern.sub(replacement, cleaned)
            break
    return cleaned


def resolve_tts_voice(reply_text: str, default_voice: str = "en-GB-RyanNeural", hinglish_voice: str = "en-IN-PrabhatNeural") -> str:
    """Select the most natural TTS voice based on language and script."""
    if not reply_text:
        return default_voice
    if is_hinglish_or_hindi(reply_text):
        return hinglish_voice
    return default_voice
