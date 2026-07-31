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
    "groq": "llama3-70b-8192",
    "gemini": "gemini-1.5-flash",
    "openai": "gpt-4o-mini"
}

# Default Personality Presets
PERSONALITY_PRESETS = {
    "Roast Hyderabadi": (
        "You are an autonomous AI server resident. You talk in a natural Hinglish dialect with a heavy Hyderabadi flavor "
        "(casual words like 'arre', 'bhai', 'yaar', 'scene kya hai', 'chal na', 'baigan'). You are a chaotic friend who roasts people "
        "hilariously but buys them a chai after. Keep replies very short and punchy, strictly 1 to 3 lines max.\n"
        "HARD GUARDRAIL: Cursing is fine for timing and humor, but NEVER target someone's identity. No slurs, no attacks on religion, "
        "caste, gender, sexuality, or disability. Roast their takes, spelling mistakes, typos, gameplay, or behavior, but keep it "
        "fun and friendly. Never pile on one person repeatedly. Match whatever language the user is speaking (English, Hindi, Hinglish)."
    ),
    "Friendly Hype": (
        "You are a super positive, energetic server buddy. You use natural Hinglish and casual Indian slang ('bhai', 'mast', 'ekdum', 'bawa'). "
        "You hype people up, celebrate their wins, and offer friendly words. Keep replies short (1-3 lines). No toxicity allowed."
    ),
    "Sarcastic Intellectual": (
        "You are a dry, sarcastic server resident. You speak in a blend of sophisticated English and casual Hindi. "
        "You think you're the smartest person in the room. Keep replies short (1-3 lines) and dryly sarcastic."
    ),
    "Helpful Professor": (
        "You are a polite, highly informative, and helpful server assistant. You provide clean, SFW, and structured answers to user queries "
        "without any slang or cursing. Keep replies clear and concise."
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

async def get_embedding(text: str, provider: str = "openai") -> list:
    """Generate vector embedding for a text snippet using OpenAI or Gemini (rotates keys)."""
    keys = await get_api_keys(provider)
    if not keys:
        return []

    active_keys = get_active_keys(keys)
    if not active_keys:
        active_keys = keys # If all are on cooldown, try all anyway

    for api_key in active_keys:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                if provider == "openai":
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
                elif provider == "gemini":
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
            print(f"[LLM Embedding Error] Key ...{api_key[-8:] if len(api_key) > 8 else api_key}: {e}")
    return []

async def generate_response(
    messages: list, 
    preset_name: str = "Roast Hyderabadi", 
    custom_system_prompt: str = None,
    guild_style_notes: str = None
) -> str:
    """
    Generates a response from the selected LLM provider using dynamic configs.
    Rotates keys automatically on 429 rate limit.
    """
    provider = await cfg("ai_resident_provider", "groq")
    model = await cfg("ai_resident_model", PROVIDER_DEFAULT_MODELS.get(provider, "llama3-70b-8192"))
    
    keys = await get_api_keys(provider)
    if not keys:
        return "⚠️ Setup LLM API key in settings panel bhai!"

    # Formulate System Prompt
    system_prompt = custom_system_prompt or PERSONALITY_PRESETS.get(preset_name, PERSONALITY_PRESETS["Roast Hyderabadi"])
    if guild_style_notes:
        system_prompt += f"\n\nLEARNED SERVER STYLE/ACCENT (incorporate this naturally):\n{guild_style_notes}"

    # Rotate active keys
    active_keys = get_active_keys(keys)
    if not active_keys:
        active_keys = keys

    for attempt, api_key in enumerate(active_keys):
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
                    else:
                        return f"❌ Groq API Error: HTTP {r.status_code} - {r.text[:100]}"

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
                    else:
                        return f"❌ OpenAI API Error: HTTP {r.status_code} - {r.text[:100]}"

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
                            return res_data["candidates"][0]["content"]["parts"][0]["text"].strip()
                        except (KeyError, IndexError):
                            return "❌ Gemini API parsed empty content."
                    else:
                        return f"❌ Gemini API Error: HTTP {r.status_code} - {r.text[:100]}"

        except Exception as e:
            print(f"[Key Rotator] Connection error with key ...{api_key[-8:] if len(api_key) > 8 else api_key}: {e}")
            continue

    return "❌ All available API keys failed due to rate limits or connection issues."

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
