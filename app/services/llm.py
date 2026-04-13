import time
import anthropic
from app.core.config import cfg
from app.services.prompt import SYSTEM_PROMPT

# Retryable HTTP status codes
_RETRYABLE = {429, 500, 502, 503, 504, 529}


def generate_speech(user_prompt: str, max_retries: int = 4, backoff_base: float = 2.0) -> str:
    """Call Claude to generate a therapeutic speech with ElevenLabs tags.
    Retries on overloaded (529), rate limit (429), and server errors (5xx)
    with exponential backoff."""
    client = anthropic.Anthropic(api_key=cfg.ANTHROPIC_API_KEY)

    last_exc = None

    for attempt in range(1, max_retries + 1):
        try:
            message = client.messages.create(
                model=cfg.CLAUDE_MODEL,
                max_tokens=4096,
                system=SYSTEM_PROMPT,
                messages=[
                    {"role": "user", "content": user_prompt}
                ],
            )

            text = ""
            for block in message.content:
                if block.type == "text":
                    text += block.text

            return text.strip()

        except Exception as e:
            last_exc = e
            status = getattr(e, "status_code", None)

            if status in _RETRYABLE:
                if attempt < max_retries:
                    sleep_s = backoff_base ** attempt
                    if status == 429:
                        sleep_s = backoff_base ** (attempt + 1)
                    print(
                        f"[LLM] Claude API error {status}. "
                        f"Retrying attempt {attempt}/{max_retries} after {sleep_s:.1f}s..."
                    )
                    time.sleep(sleep_s)
                    continue
                else:
                    print(f"[LLM] Claude API error {status} after {max_retries} attempts. Giving up.")
                    raise

            # Connection errors (no status code)
            if status is None and ("connect" in str(e).lower() or "timeout" in str(e).lower()):
                if attempt < max_retries:
                    sleep_s = backoff_base ** attempt
                    print(
                        f"[LLM] Claude connection error. "
                        f"Retrying attempt {attempt}/{max_retries} after {sleep_s:.1f}s..."
                    )
                    time.sleep(sleep_s)
                    continue
                else:
                    print(f"[LLM] Claude connection error after {max_retries} attempts. Giving up.")
                    raise

            # Non-retryable error (400, 401, etc) -- raise immediately
            raise

    if last_exc:
        raise last_exc
    raise RuntimeError("Unknown LLM error; no exception captured.")