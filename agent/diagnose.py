import os, json, time
from collect import collect
from google import genai

MODELS = [
    os.environ.get("GEMINI_MODEL", "gemini-3.8-flash"),
    "gemini-3.7-flash",
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
]

PROMPT = """You are an on-call SRE agent. From the incident evidence below, return ONLY JSON:
{"root_cause": str, "confidence": number 0-1, "evidence_used": [str], "proposed_fix": str, "verify_with": str}
Evidence:
"""

def diagnose():
    ev = collect()
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    last = None
    for model in MODELS:
        for attempt in range(2):
            try:
                r = client.models.generate_content(
                    model=model,
                    contents=PROMPT + json.dumps(ev, indent=2),
                    config={"response_mime_type": "application/json"},
                )
                d = json.loads(r.text)
                d["model_used"] = model
                return ev, d
            except Exception as e:
                last = e
                print(f"[{model}] failed: {str(e)[:80]}")
                time.sleep(2)
    raise RuntimeError(f"all models failed: {last}")

if __name__ == "__main__":
    ev, d = diagnose()
    print(json.dumps(d, indent=2))