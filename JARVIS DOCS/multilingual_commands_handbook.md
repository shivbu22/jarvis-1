# J.A.R.V.I.S. Multilingual Voice & Command Handbook
### Official Bilingual Control Catalog: English, Hinglish & Hindi (हिंदी)

---

## 1. System Overview & Language Engine

J.A.R.V.I.S. is engineered with a continuous multilingual comprehension pipeline:
1. **Multilingual Speech-to-Text**: Speech in English, Hindi, or conversational code-switched Hinglish is captured locally via Faster-Whisper.
2. **Intent Normalization & Routing**: The intent engine (`apps/gateway/hinglish.py`) translates colloquial vernacular phrasings into crisp, canonical tool directives while preserving raw transcripts for conversational nuance.
3. **Autonomous Execution**: Verified native tool calls are executed immediately across Win32, Chromium, media engines, and system services.
4. **Adaptive Neural Voice Feedback**: Spoken output intelligently routes between:
   - **`en-GB-RyanNeural`**: Dignified British co-founder voice for English queries.
   - **`en-IN-PrabhatNeural` / `hi-IN-MadhurNeural`**: Fluent Indian neural voices for Hindi and Hinglish queries.

---

## 2. Command Catalog by Category

### Category A: Desktop & Windows Operating System Control

| Action / Intent | English Command | Hinglish Command | Hindi Command (देवनागरी) | Target Tool |
| :--- | :--- | :--- | :--- | :--- |
| **Show Desktop** | "Show my desktop" / "Minimize all windows" | "Desktop dikhao" / "Saare windows minimize karo" / "Parde gira do" | "डेस्कटॉप दिखाओ" / "सारी खिड़कियां छोटी करो" | `desktop_window_control(action='show_desktop')` |
| **Open Notepad** | "Open notepad" | "Notepad kholo" / "Notepad chalao" | "नोटपैड खोलो" | `app_launch(app='notepad')` |
| **Open Calculator** | "Open calculator" | "Calculator open karo" / "Calc kholo" | "कैलकुलेटर खोलो" | `app_launch(app='calc')` |
| **Open VS Code** | "Open VS Code" / "Open code editor" | "VS Code kholo" / "Code editor chalao" | "वीएस कोड खोलो" | `app_launch(app='code')` |
| **Open Terminal** | "Open terminal" / "Open PowerShell" | "Terminal kholo" / "PowerShell open karo" | "टर्मिनल खोलो" | `app_launch(app='powershell')` |
| **List Windows** | "What windows are currently open?" | "Kaun se apps khule hain?" / "Active windows dikhao" | "कौन से ऐप्स खुले हैं?" / "सक्रिय खिड़कियां दिखाओ" | `desktop_window_control(action='list')` |
| **Take Screenshot** | "Take a screenshot" / "Capture the screen" | "Screenshot le lo" / "Screen capture karo" | "स्क्रीनशॉट लो" / "स्क्रीन की तस्वीर लो" | `desktop_screenshot()` |
| **Webcam Photo** | "Take a photo with the webcam" | "Camera se photo khincho" / "Webcam pic lo" | "कैमरा से फोटो खींचो" | `camera_capture()` |
| **Lock System** | "Lock the computer" / "Lock workstation" | "System lock karo" / "PC lock kar do" | "कंप्यूटर लॉक करो" | `system_quick_action(action='lock')` |

---

### Category B: Music, Audio & Media Playback

| Action / Intent | English Command | Hinglish Command | Hindi Command (देवनागरी) | Target Tool |
| :--- | :--- | :--- | :--- | :--- |
| **Play Lofi Music** | "Play lofi music on YouTube" | "YouTube par lofi gaana bajao" / "Lofi music chalao" | "यूट्यूब पर लो-फाई गाने बजाओ" | `music_play(query='lofi music', service='youtube')` |
| **Play Artist / Track** | "Play Arijit Singh songs" | "Arijit Singh ke gaane sunao" / "Arijit Singh chalao" | "अरिजीत सिंह के गाने सुनाओ" | `music_play(query='Arijit Singh')` |
| **Pause Music** | "Pause music" / "Stop playback" | "Gaana roko" / "Music pause karo" / "Band karo" | "गाना बंद करो" / "संगीत रोको" | `media_control(action='pause')` |
| **Resume Music** | "Resume playback" / "Continue music" | "Gaana wapas chalao" / "Resume karo" | "गाना फिर से चलाओ" | `media_control(action='play')` |
| **Next Track** | "Next track" / "Skip this song" | "Agla gaana chalao" / "Next song lagao" | "अगला गाना चलाओ" | `media_control(action='next')` |
| **Increase Volume** | "Turn the volume up" / "Increase volume" | "Awaaz badhao" / "Volume tez karo" | "आवाज़ बढ़ाओ" / "वॉल्यूम तेज़ करो" | `volume_control(action='up')` |
| **Decrease Volume** | "Turn the volume down" / "Lower sound" | "Awaaz dheemi karo" / "Volume kam karo" | "आवाज़ कम करो" / "आवाज़ धीमी करो" | `volume_control(action='down')` |
| **Mute Sound** | "Mute the audio" / "Be quiet" | "Mute kar do" / "Chup ho jao" / "Awaaz band karo" | "आवाज़ बंद करो" / "म्यूट करो" | `volume_control(action='mute')` |
| **Set Exact Volume** | "Set volume to 50 percent" | "Volume 50 percent par set karo" | "वॉल्यूम 50 प्रतिशत करो" | `volume_control(action='set', level=50)` |

---

### Category C: Browser & Web Research

| Action / Intent | English Command | Hinglish Command | Hindi Command (देवनागरी) | Target Tool |
| :--- | :--- | :--- | :--- | :--- |
| **Launch Browser** | "Open Google Chrome" / "Launch browser" | "Chrome kholo" / "Browser open karo" | "क्रोम ब्राउज़र खोलो" | `web_open(target='chrome')` |
| **Search Google** | "Search the web for artificial intelligence news" | "Google par AI news search karo" / "AI ke baare me dhoondo" | "गूगल पर एआई समाचार खोजो" | `web_search(query='artificial intelligence news')` |
| **Open YouTube** | "Open YouTube in the browser" | "YouTube kholo" / "YouTube chalao" | "यूट्यूब खोलो" | `web_open(target='youtube')` |
| **Open Specific Website** | "Open github.com" / "Go to Wikipedia" | "Github kholo" / "Wikipedia par jao" | "गिटहब खोलो" | `browser_navigate(url='https://github.com')` |
| **Scrape Web Page** | "Read the content of this webpage" | "Yeh webpage padh ke batao" / "Page summary nikalo" | "इस वेब पेज को पढ़कर बताओ" | `browser_read()` |

---

### Category D: Weather, News & Productivity

| Action / Intent | English Command | Hinglish Command | Hindi Command (देवनागरी) | Target Tool |
| :--- | :--- | :--- | :--- | :--- |
| **Check Weather** | "What is the weather today?" | "Aaj ka mausam kaisa hai?" / "Weather update do" | "आज मौसम कैसा है?" / "मौसम की जानकारी दो" | `get_weather(city='auto')` |
| **City Weather** | "What is the weather in Mumbai?" | "Mumbai ka mausam kaisa hai?" | "मुंबई में मौसम कैसा है?" | `get_weather(city='Mumbai')` |
| **News Headlines** | "What are the latest news headlines?" | "Aaj ki taaza khabar sunao" / "Latest news batao" | "आज की ताज़ा खबरें सुनाओ" | `web_news()` |
| **Tech News** | "Tell me recent tech news" | "Tech news sunao" / "Technology ki khabrein batao" | "टेक्नोलॉजी की खबरें बताओ" | `web_news(topic='technology')` |
| **Math / Calculator** | "Calculate 15 percent of 8500" | "8500 ka 15 percent kitna hoga?" | "8500 का 15 प्रतिशत कितना होगा?" | `calc(expression='8500 * 0.15')` |
| **Current Time & Date** | "What time is it right now?" | "Abhi kitna time hua hai?" / "Aaj kaun si date hai?" | "अभी क्या समय हुआ है?" / "आज कौन सी तारीख है?" | `system_info()` |

---

### Category E: Memory, Personalization & Notes

| Action / Intent | English Command | Hinglish Command | Hindi Command (देवनागरी) | Target Tool |
| :--- | :--- | :--- | :--- | :--- |
| **Save Fact** | "Remember that my favourite coffee is double espresso" | "Yaad rakhna meri pasandida coffee double espresso hai" | "याद रखना मेरी पसंदीदा कॉफी डबल एस्प्रेसो है" | `memory_remember(fact='Favourite coffee: double espresso')` |
| **Recall Facts** | "What do you remember about me?" | "Mere baare me kya yaad hai?" / "Meri preferences batao" | "मेरे बारे में क्या याद है?" / "मेरी पसंद बताओ" | `memory_recall(query='preferences')` |
| **List Memories** | "List all your stored memories" | "Saari saved memories dikhao" | "सारे सुरक्षित नोट्स दिखाओ" | `memory_list()` |

---

### Category F: Obsidian Brain Vault Management

| Action / Intent | English Command | Hinglish Command | Hindi Command (देवनागरी) | Target Tool |
| :--- | :--- | :--- | :--- | :--- |
| **Search Brain Vault** | "Search Obsidian brain for venture ideas" | "Obsidian me venture ideas dhoondo" / "Brain search karo" | "ऑब्सिडियन में नए विचार खोजो" | `brain_vault_search(query='venture ideas')` |
| **Read Vault Note** | "Read note 2026-09-18 from brain vault" | "Obsidian se 2026-09-18 note padho" | "ऑब्सिडियन से नोट पढ़ो" | `brain_vault_read(filename='2026-09-18.md')` |
| **Create Note** | "Create a new note in Obsidian titled AI Roadmap" | "Obsidian me AI Roadmap naam se note banao" | "ऑब्सिडियन में एआई रोडमैप नोट बनाओ" | `brain_vault_note_write(filename='AI Roadmap.md', ...)` |
| **Audit Vault Health** | "Audit Obsidian vault and fix broken links" | "Obsidian brain lint karo aur links check karo" | "ऑब्सिडियन वॉल्ट की जांच करो" | `brain_vault_lint()` |

---

### Category G: Quant, Markets & Portfolio Intelligence

| Action / Intent | English Command | Hinglish Command | Hindi Command (देवनागरी) | Target Tool |
| :--- | :--- | :--- | :--- | :--- |
| **Market Index Update** | "What is the Nifty 50 and Sensex status?" | "Nifty 50 aur Sensex ka haal batao" | "निफ्टी 50 और सेंसेक्स का क्या हाल है?" | `fetch_market_data(symbol='^NSEI')` |
| **Stock Quote** | "Get Reliance stock price" | "Reliance ka stock price kya chal raha hai?" | "रिलायंस के शेयर का भाव बताओ" | `fetch_market_data(symbol='RELIANCE.NS')` |
| **Crypto Price** | "What is Bitcoin price right now?" | "Bitcoin ka rate batao" / "BTC price kya hai?" | "बिटकॉइन का क्या रेट है?" | `crypto_market_pulse(asset='BTC')` |
| **Portfolio Balance** | "Show my paper trading portfolio" | "Mera portfolio dikhao" / "P&L status batao" | "मेरा पोर्टफोलियो दिखाओ" | `check_portfolio()` |

---

### Category H: System Health, Self-Healing & Diagnostics

| Action / Intent | English Command | Hinglish Command | Hindi Command (देवनागरी) | Target Tool |
| :--- | :--- | :--- | :--- | :--- |
| **Hardware Vitals** | "Report system status and CPU RAM usage" | "System ka status batao, kitna RAM free hai?" | "सिस्टम की स्थिति और रैम उपयोग बताओ" | `system_info()` |
| **Self-Healing Run** | "Run self healing diagnostics on all subsystems" | "Self heal chalao aur system inspect karo" | "सेल्फ-हीलिंग जांच चलाओ" | `self_heal_diagnose()` |
| **Watchdog Telemetry** | "Check self healing watchdog status" | "Self heal watchdog ka status dikhao" | "वॉचडॉग टेलीमेट्री दिखाओ" | `self_heal_status()` |

---

### Category I: Persona, Greetings & Conversational Banter

| Dialogue Intent | English Phrase | Hinglish Phrase | Hindi Phrase (देवनागरी) | Spoken Response Style |
| :--- | :--- | :--- | :--- | :--- |
| **Morning Greeting** | "Good morning Jarvis" | "Good morning Jarvis" / "Shubh prabhat Jarvis" | "शुभ प्रभात जार्विस" | Warm, crisp executive morning brief with time and readiness. |
| **Readiness Check** | "Are you ready, Jarvis?" | "Jarvis tayyar ho?" / "Ready ho kaam ke liye?" | "क्या तुम तैयार हो जार्विस?" | Dignified British wit confirming 100% operational readiness. |
| **Identity Query** | "Who are you?" | "Tum kaun ho?" / "Apne baare me batao" | "तुम कौन हो?" / "अपने बारे में बताओ" | "I am J.A.R.V.I.S., your autonomous Technical AI Co-Founder and Quant OS, sir." |
| **Gratitude / Praise** | "Thank you Jarvis" / "Good job" | "Shukriya Jarvis" / "Bahut badiya kaam kiya" / "Shabaash" | "धन्यवाद जार्विस" / "बहुत बढ़िया काम किया" | "Always at your service, sir." / "Thank you, sir. Ready for the next directive." |

---

## 3. How to Expand & Add Custom Commands

1. **Add new intent mappings** in [`apps/gateway/hinglish.py`](file:///c:/Users/ahiva/OneDrive/Desktop/jarvis/apps/gateway/hinglish.py) under `_INTENT_TRANSFORMS`.
2. **Add permanent memory** by simply telling J.A.R.V.I.S.:
   - *"Jarvis, remember that when I say 'chai time', you should pause music and open browser."*
   - It is instantly stored in SQLite vector memory and recalled dynamically whenever relevant context is spoken.
