"""Sends a prompt to a free LLM (Gemini or Groq) and returns its JSON answer."""
import json
import logging
import os
import re
import time

import requests

log = logging.getLogger("llm")

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta"
GROQ_URL = "https://api.groq.com/openai/v1"

state = {"model": None, "last_call": 0}


class LLMError(Exception):
    """The LLM could not give us a usable answer."""


def choose_provider(cfg):
    """Return (provider name, api key) for the first provider we have a key for.

    Returns (None, None) when there is no key: the offline writer is used instead.
    """
    wanted = (cfg["llm"]["provider"] or "none").lower()
    if wanted == "none":
        return None, None
    order = ["gemini", "groq"] if wanted == "gemini" else ["groq", "gemini"]
    for provider in order:
        key = os.getenv("GEMINI_API_KEY" if provider == "gemini" else "GROQ_API_KEY")
        if key:
            return provider, key
    return None, None


def list_models(provider, key):
    """Ask the provider which models this key can use."""
    if provider == "gemini":
        response = requests.get(GEMINI_URL + "/models", params={"pageSize": 200}, headers={"x-goog-api-key": key}, timeout=20)
        response.raise_for_status()
        names = []
        for model in response.json().get("models", []):
            if "generateContent" in model.get("supportedGenerationMethods", []):
                names.append(model["name"].replace("models/", ""))
        return names
    response = requests.get(GROQ_URL + "/models", headers={"Authorization": "Bearer " + key}, timeout=20)
    response.raise_for_status()
    return [model["id"] for model in response.json().get("data", [])]


def choose_model(cfg, provider, key):
    """Pick the first model from config.yaml that is really available.

    Model names change often. If none of ours exist any more, we fall back to
    any available text model so the pipeline keeps working.
    """
    if state["model"]:
        return state["model"]
    preferred = cfg["llm"]["models"][provider]
    try:
        available = list_models(provider, key)
    except Exception as error:
        log.warning("could not list models (%s); trying %s", error, preferred[0])
        state["model"] = preferred[0]
        return state["model"]

    for name in preferred:
        if name in available:
            state["model"] = name
            break
    if not state["model"]:
        not_for_text = ["image", "tts", "audio", "live", "embedding", "whisper", "guard", "robotics"]
        for name in available:
            if not any(word in name.lower() for word in not_for_text):
                state["model"] = name
                log.warning("preferred models not available; using %s", name)
                break
    if not state["model"]:
        raise LLMError("No usable text model found for this " + provider + " key")
    log.info("LLM: %s / %s", provider, state["model"])
    return state["model"]


def call_gemini(key, model, system, user, temperature):
    """One request to Gemini. Returns the HTTP response."""
    body = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": {"temperature": temperature, "maxOutputTokens": 4096, "responseMimeType": "application/json"},
    }
    return requests.post(GEMINI_URL + "/models/" + model + ":generateContent",
                         headers={"x-goog-api-key": key}, json=body, timeout=60)


def call_groq(key, model, system, user, temperature):
    """One request to Groq. Returns the HTTP response."""
    body = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": temperature,
        "response_format": {"type": "json_object"},
    }
    return requests.post(GROQ_URL + "/chat/completions", headers={"Authorization": "Bearer " + key}, json=body, timeout=60)


def read_answer(provider, data):
    """Pull the generated text out of the provider's response."""
    try:
        if provider == "gemini":
            text = ""
            for part in data["candidates"][0]["content"]["parts"]:
                text += part.get("text", "")
            return text
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError):
        raise LLMError("Unexpected response: " + str(data)[:200])


def read_json(text):
    """Turn the model's text into a dictionary (tolerates ```json fences around it)."""
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
    raise LLMError("Model did not return valid JSON")


def ask_llm(cfg, system, user):
    """Send the prompt and return the answer as a dictionary.

    - waits between calls so we stay inside the free-tier rate limit
    - retries up to 3 times when the server is busy or the answer is unusable
    Returns (answer, model name). Raises LLMError if it still fails.
    """
    provider, key = choose_provider(cfg)
    if provider is None:
        raise LLMError("No LLM API key configured")
    model = choose_model(cfg, provider, key)
    seconds_between_calls = 60 / cfg["llm"]["requests_per_minute"]
    last_error = None

    for attempt in range(1, 4):
        wait = seconds_between_calls - (time.time() - state["last_call"])
        if wait > 0:
            time.sleep(wait)
        state["last_call"] = time.time()

        try:
            if provider == "gemini":
                response = call_gemini(key, model, system, user, cfg["llm"]["temperature"])
            else:
                response = call_groq(key, model, system, user, cfg["llm"]["temperature"])

            if response.status_code in (429, 500, 502, 503, 504):
                last_error = "HTTP " + str(response.status_code) + " (rate limit or server busy)"
                log.warning("%s, attempt %d of 3; waiting before retry", last_error, attempt)
                time.sleep(10 * attempt)
                continue
            if response.status_code != 200:
                raise LLMError("HTTP " + str(response.status_code) + ": " + response.text[:200])

            return read_json(read_answer(provider, response.json())), model
        except (requests.RequestException, LLMError) as error:
            last_error = str(error)
            log.warning("LLM call failed, attempt %d of 3: %s", attempt, error)

    raise LLMError("LLM failed after 3 attempts: " + str(last_error))
