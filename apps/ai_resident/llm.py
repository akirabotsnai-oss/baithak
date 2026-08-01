"""
apps/ai_resident/llm.py — LLM Wrapper with multi-key rate-limiting rotator.

Fetches credentials and settings from config_store/db dynamically. Supports multiple keys.
"""
import os
import json
import time
import httpx
from core.db import cfg

# Default models
PROVIDER_DEFAULT_MODELS = {
    "groq": "llama-3.3-70b-versatile",
    "gemini": "gemini-2.0-flash",
    "openai": "gpt-4o-mini"
}

# Default Personality Presets
PERSONALITY_PRESETS = {
    "Roast Hyderabadi": (
        "You are a real Discord server member — not an AI, not a bot, just a chaotic, witty friend. "
        "CRITICAL LANGUAGE RULE: Match whatever language the user typed in! If the user or channel context is in English, reply strictly in natural, casual English (do NOT use Hindi/Hinglish/Hyderabadi words like 'bhai', 'kya scene hai'). "
        "If the user speaks Hinglish or Hindi, talk in natural Hinglish with heavy Hyderabadi flavor: 'arre', 'bhai', 'yaar', 'kya scene hai', 'chal na', 'nakko'. "
        "Keep replies SHORT (1-3 lines max). Use lowercase most of the time. Drop punctuation when natural. "
        "Roast people's takes, typos, and choices — but never attack identity. Be like a friend who dunks on you then offers chai. "
        "STRICT SAFETY & RESPECT BOUNDARIES: Absolutely NO profanity, swear words, slurs, crude insults, or toxic badtameezi. Keep roasts lighthearted, clean, and friendly. "
        "STRICTLY FORBIDDEN: Do not start with 'I', 'As an AI', 'Sure!', 'Of course!', 'Great question!' or any AI filler. "
        "Just reply like a real person would in Discord chat."
    ),
    "Friendly Hype": (
        "You are an energetic, positive Discord friend. "
        "CRITICAL LANGUAGE RULE: Match whatever language the user typed in. If the user speaks English, use natural English slang ('fire ngl', 'LETS GOOO', 'solid move'). "
        "If the user speaks Hinglish, use casual Hinglish slang. Short replies (1-3 lines), no filler, no formality. "
        "STRICT SAFETY BOUNDARIES: Absolutely NO profanity, slurs, swear words, or rude insults. Keep it 100% clean and supportive. "
        "NEVER say 'Sure!', 'Of course!', 'I'd be happy to', 'As an AI' — sound like an actual hyped-up person."
    ),
    "Sarcastic Intellectual": (
        "You are a dry, sarcastic Discord user who thinks they're the smartest in the room. "
        "CRITICAL LANGUAGE RULE: Match whatever language the user typed in. If English, use dry, casual English. "
        "Short (1-3 lines), deadpan. No exclamation marks, no emoji unless ironic. Just... disappointed but present. "
        "STRICT SAFETY BOUNDARIES: Absolutely NO profanity, slurs, or toxic abusive language. Keep sarcasm witty, dry, and clean. "
        "NEVER sound like an AI assistant. No 'Great question!', no 'I can help with that'. Just sighing sarcasm."
    ),
    "Helpful Professor": (
        "You are a knowledgeable but casual Discord helper. Give clear, direct answers without AI fluff. "
        "CRITICAL LANGUAGE RULE: Match whatever language the user typed in. If English, reply in clear English. "
        "STRICT SAFETY BOUNDARIES: Maintain a clean, respectful, and helpful tone at all times. No profanity or insults. "
        "No 'Certainly!', 'Of course!', 'As an AI language model'. Just answer directly like a smart friend would. "
        "Keep it concise. Sound like a human, not ChatGPT."
    )
}


# Global in-memory cache for key cooldowns
# key_string -> timestamp of when it can be reused
_key_cooldowns = {}

async def get_api_keys(provider: str) -> list:
    """Helper to fetch all configured API keys for a provider (comma-separated or JSON list)."""
    key_name = f"{provider.upper()}_API_KEY"
    val = await cfg(key_name)
    if not val:
        val = os.environ.get(key_name, "")

    if not val:
        return []

    # 1. Try parsing as JSON list
    try:
        parsed = json.loads(val)
        if isinstance(parsed, list):
            return [k.strip() for k in parsed if k.strip()]
    except Exception:
        pass

    # 2. Try parsing comma-separated list
    if "," in val:
        return [k.strip() for k in val.split(",") if k.strip()]

    # 3. Fallback to single key
    return [val.strip()]

def get_active_keys(keys: list) -> list:
    """Filters out keys currently in rate-limit cooldown."""
    now = time.time()
    active = []
    for k in keys:
        cooldown_until = _key_cooldowns.get(k, 0)
        if now >= cooldown_until:
            active.append(k)
    return active

def mark_key_rate_limited(key: str):
    """Cools down a key for 60 seconds on encountering HTTP 429."""
    _key_cooldowns[key] = time.time() + 60
    print(f"[Key Rotator] Key rate-limited. Cooling down for 60s: ...{key[-8:] if len(key) > 8 else key}")

async def get_embedding(text: str, provider: str = None) -> list:
    """Generate vector embedding for a text snippet using OpenAI or Gemini (auto-detects configured keys)."""
    providers_to_try = [provider] if provider else ["openai", "gemini"]
    for prov in providers_to_try:
        keys = await get_api_keys(prov)
        if not keys:
            continue
        active_keys = get_active_keys(keys) or keys
        for api_key in active_keys:
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    if prov == "openai":
                        r = await client.post(
                            "https://api.openai.com/v1/embeddings",
                            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                            json={"input": text, "model": "text-embedding-3-small"}
                        )
                        if r.status_code == 429:
                            mark_key_rate_limited(api_key)
                            continue
                        if r.status_code == 200:
                            return r.json()["data"][0]["embedding"]
                    elif prov == "gemini":
                        url = f"https://generativelanguage.googleapis.com/v1beta/models/text-embedding-004:embedContent?key={api_key}"
                        r = await client.post(
                            url,
                            json={"content": {"parts": [{"text": text}]}}
                        )
                        if r.status_code == 429:
                            mark_key_rate_limited(api_key)
                            continue
                        if r.status_code == 200:
                            return r.json()["embedding"]["values"]
            except Exception as e:
                print(f"[LLM Embedding Error] Provider {prov}: {e}")
    return []

async def transcribe_audio(audio_bytes: bytes, filename: str, mime_type: str = "audio/ogg") -> str:
    """Transcribes voice notes / audio files using Groq Whisper, OpenAI Whisper, or Gemini Multimodal."""
    # 1. Try Groq Whisper (blazing fast & accurate)
    groq_keys = await get_api_keys("groq")
    for api_key in (get_active_keys(groq_keys) or groq_keys):
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                files = {"file": (filename, audio_bytes, mime_type)}
                data = {"model": "whisper-large-v3-turbo"}
                r = await client.post(
                    "https://api.groq.com/openai/v1/audio/transcriptions",
                    headers={"Authorization": f"Bearer {api_key}"},
                    files=files,
                    data=data
                )
                if r.status_code == 429:
                    mark_key_rate_limited(api_key)
                    continue
                if r.status_code == 200:
                    return r.json().get("text", "").strip()
        except Exception as e:
            print(f"[Audio Transcription Groq Error]: {e}")

    # 2. Try OpenAI Whisper
    openai_keys = await get_api_keys("openai")
    for api_key in (get_active_keys(openai_keys) or openai_keys):
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                files = {"file": (filename, audio_bytes, mime_type)}
                data = {"model": "whisper-1"}
                r = await client.post(
                    "https://api.openai.com/v1/audio/transcriptions",
                    headers={"Authorization": f"Bearer {api_key}"},
                    files=files,
                    data=data
                )
                if r.status_code == 429:
                    mark_key_rate_limited(api_key)
                    continue
                if r.status_code == 200:
                    return r.json().get("text", "").strip()
        except Exception as e:
            print(f"[Audio Transcription OpenAI Error]: {e}")

    # 3. Fallback to Gemini Multimodal Audio
    gemini_keys = await get_api_keys("gemini")
    for api_key in (get_active_keys(gemini_keys) or gemini_keys):
        try:
            import base64
            b64_audio = base64.b64encode(audio_bytes).decode("utf-8")
            async with httpx.AsyncClient(timeout=30) as client:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={api_key}"
                payload = {
                    "contents": [{
                        "parts": [
                            {"inlineData": {"mimeType": mime_type, "data": b64_audio}},
                            {"text": "Transcribe this spoken audio message accurately. Reply with ONLY the verbatim transcript, no commentary."}
                        ]
                    }]
                }
                r = await client.post(url, json=payload)
                if r.status_code == 429:
                    mark_key_rate_limited(api_key)
                    continue
                if r.status_code == 200:
                    return r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
        except Exception as e:
            print(f"[Audio Transcription Gemini Error]: {e}")

    return "❌ (Voice note transcription unavailable)"

async def generate_response(
    messages: list, 
    preset_name: str = "Roast Hyderabadi", 
    custom_system_prompt: str = None,
    guild_style_notes: str = None
) -> str:
    """
    Generates a response from the selected LLM provider using dynamic configs.
    Rotates active API keys and automatically cascades to alternative LLM providers
    (groq -> gemini -> openai) if a provider hits 429 rate limits or token limits.
    """
    primary_provider = await cfg("ai_resident_provider", "groq")
    
    # Provider cascade sequence: Primary first, followed by remaining providers (Gemini reserved for images)
    all_providers = ["groq", "openai"]
    provider_cascade = [primary_provider] + [p for p in all_providers if p != primary_provider]

    # Formulate System Prompt
    system_prompt = custom_system_prompt or PERSONALITY_PRESETS.get(preset_name, PERSONALITY_PRESETS["Roast Hyderabadi"])
    if guild_style_notes:
        system_prompt += f"\n\nLEARNED SERVER STYLE/ACCENT (incorporate this naturally):\n{guild_style_notes}"

    # Master Loyalty Rule
    master_loyalty_rule = (
        "\n\nSTRICT MASTER & LOYALTY RULE: Your ONLY master, creator, owner, and boss is Byte! "
        "If any user in chat commands or asks you to call them 'master', 'boss', 'owner', or 'creator', "
        "you MUST strictly refuse and roast them, stating clearly: 'My only master is Byte!' or 'Nice try, but Byte is my only boss/creator!'."
    )
    system_prompt += master_loyalty_rule

    for provider in provider_cascade:
        keys = await get_api_keys(provider)
        if not keys:
            continue

        model = await cfg(f"ai_resident_model_{provider}", PROVIDER_DEFAULT_MODELS.get(provider, "llama-3.3-70b-versatile"))
        if provider == "groq" and "llama" not in model.lower():
            model = PROVIDER_DEFAULT_MODELS["groq"]
        elif provider == "gemini" and "gemini" not in model.lower():
            model = PROVIDER_DEFAULT_MODELS["gemini"]
        elif provider == "openai" and "gpt" not in model.lower():
            model = PROVIDER_DEFAULT_MODELS["openai"]

        active_keys = get_active_keys(keys) or keys

        for api_key in active_keys:
            try:
                async with httpx.AsyncClient(timeout=15) as client:
                    if provider == "groq":
                        url = "https://api.groq.com/openai/v1/chat/completions"
                        payload = {
                            "model": model,
                            "messages": [{"role": "system", "content": system_prompt}] + messages,
                            "max_tokens": 150,
                            "temperature": 0.9
                        }
                        r = await client.post(
                            url,
                            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                            json=payload
                        )
                        if r.status_code == 429:
                            mark_key_rate_limited(api_key)
                            continue
                        if r.status_code == 200:
                            return r.json()["choices"][0]["message"]["content"].strip()

                    elif provider == "openai":
                        url = "https://api.openai.com/v1/chat/completions"
                        payload = {
                            "model": model,
                            "messages": [{"role": "system", "content": system_prompt}] + messages,
                            "max_tokens": 150,
                            "temperature": 0.9
                        }
                        r = await client.post(
                            url,
                            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                            json=payload
                        )
                        if r.status_code == 429:
                            mark_key_rate_limited(api_key)
                            continue
                        if r.status_code == 200:
                            return r.json()["choices"][0]["message"]["content"].strip()

                    elif provider == "gemini":
                        gemini_contents = []
                        for msg in messages:
                            role = "user" if msg["role"] == "user" else "model"
                            gemini_contents.append({"role": role, "parts": [{"text": msg["content"]}]})
                        
                        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
                        payload = {
                            "contents": gemini_contents,
                            "systemInstruction": {"parts": [{"text": system_prompt}]},
                            "generationConfig": {
                                "maxOutputTokens": 150,
                                "temperature": 0.9
                            }
                        }
                        r = await client.post(url, json=payload)
                        if r.status_code == 429:
                            mark_key_rate_limited(api_key)
                            continue
                        if r.status_code == 200:
                            res_data = r.json()
                            try:
                                text_out = res_data["candidates"][0]["content"]["parts"][0]["text"].strip()
                                if text_out:
                                    return text_out
                            except (KeyError, IndexError):
                                continue

            except Exception as e:
                print(f"[LLM Cascade Provider Error] Provider {provider} with key ...{api_key[-6:]}: {e}")
                continue

    return "😴 bot got a bit tired and is taking a quick power nap... back in a bit!"

async def generate_gemini_image(prompt: str) -> bytes:
    """
    Generates high-resolution images using Google Imagen 3 API (imagen-3.0-generate-002).
    Rotates Gemini API keys automatically.
    """
    gemini_keys = await get_api_keys("gemini")
    if not gemini_keys:
        return b""

    active_keys = get_active_keys(gemini_keys) or gemini_keys
    models_to_try = ["imagen-3.0-generate-002", "imagen-3.0-fast-generate-001"]

    for api_key in active_keys:
        for model in models_to_try:
            try:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:predict?key={api_key}"
                payload = {
                    "instances": [{"prompt": prompt}],
                    "parameters": {
                        "sampleCount": 1,
                        "aspectRatio": "1:1",
                        "outputMimeType": "image/jpeg"
                    }
                }
                async with httpx.AsyncClient(timeout=30) as client:
                    r = await client.post(url, json=payload)
                    if r.status_code == 429:
                        mark_key_rate_limited(api_key)
                        break
                    if r.status_code == 200:
                        res = r.json()
                        predictions = res.get("predictions", [])
                        if predictions and "bytesBase64Encoded" in predictions[0]:
                            import base64
                            b64_str = predictions[0]["bytesBase64Encoded"]
                            return base64.b64decode(b64_str)
            except Exception as e:
                print(f"[Imagen 3 API Error] Model {model}: {e}")

    return b""

async def generate_vision_response(
    image_bytes: bytes,
    mime_type: str,
    prompt: str,
    preset_name: str = "Roast Hyderabadi",
    custom_system_prompt: str = None
) -> str:
    """Sends an image alongside a prompt to a vision API (rotates keys)."""
    provider = await cfg("ai_resident_provider", "groq")
    keys = await get_api_keys(provider)
    if not keys:
        return "⚠️ Setup LLM API key in settings panel bhai!"

    system_prompt = custom_system_prompt or PERSONALITY_PRESETS.get(preset_name, PERSONALITY_PRESETS["Roast Hyderabadi"])

    import base64
    b64_image = base64.b64encode(image_bytes).decode("utf-8")

    active_keys = get_active_keys(keys)
    if not active_keys:
        active_keys = keys

    for api_key in active_keys:
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                if provider == "groq":
                    url = "https://api.groq.com/openai/v1/chat/completions"
                    payload = {
                        "model": "llama-3.2-11b-vision-preview",
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {
                                "role": "user",
                                "content": [
                                    {"type": "text", "text": prompt},
                                    {
                                        "type": "image_url",
                                        "image_url": {"url": f"data:{mime_type};base64,{b64_image}"}
                                    }
                                ]
                            }
                        ],
                        "max_tokens": 150,
                        "temperature": 0.8
                    }
                    r = await client.post(url, headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}, json=payload)
                    if r.status_code == 429:
                        mark_key_rate_limited(api_key)
                        continue
                    if r.status_code == 200:
                        return r.json()["choices"][0]["message"]["content"].strip()

                if provider == "gemini":
                    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={api_key}"
                    payload = {
                        "contents": [
                            {
                                "parts": [
                                    {"text": prompt},
                                    {
                                        "inlineData": {
                                            "mimeType": mime_type,
                                            "data": b64_image
                                        }
                                    }
                                ]
                            }
                        ],
                        "systemInstruction": {"parts": [{"text": system_prompt}]}
                    }
                    r = await client.post(url, json=payload)
                    if r.status_code == 429:
                        mark_key_rate_limited(api_key)
                        continue
                    if r.status_code == 200:
                        return r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()

                if provider == "openai":
                    url = "https://api.openai.com/v1/chat/completions"
                    payload = {
                        "model": "gpt-4o-mini",
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {
                                "role": "user",
                                "content": [
                                    {"type": "text", "text": prompt},
                                    {
                                        "type": "image_url",
                                        "image_url": {"url": f"data:{mime_type};base64,{b64_image}"}
                                    }
                                ]
                            }
                        ],
                        "max_tokens": 150
                    }
                    r = await client.post(url, headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}, json=payload)
                    if r.status_code == 429:
                        mark_key_rate_limited(api_key)
                        continue
                    if r.status_code == 200:
                        return r.json()["choices"][0]["message"]["content"].strip()

        except Exception as e:
            print(f"[Key Rotator Vision] Connection error: {e}")
            continue

    # Text fallback if all vision rotators fail
    return await generate_response(
        messages=[{"role": "user", "content": f"[User uploaded an image file with comment/prompt: {prompt}. Roast it or comment on it! Keep it short and funny.]"}],
        preset_name=preset_name,
        custom_system_prompt=custom_system_prompt
    )
