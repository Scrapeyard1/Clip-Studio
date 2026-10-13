"""One place that talks to Claude, so every caller gets the same settings and error messages.

Current Claude models always think before answering ("adaptive thinking"); thinking counts against
max_tokens. `effort` sets how much they think: "medium" is plenty for planning edits and keeps
answers fast and cheap. Responses are streamed so long answers never hit a request timeout.
"""
import json
import os

MODEL = os.environ.get("CLIPSTUDIO_MODEL", "claude-sonnet-5-5")


class ClaudeError(RuntimeError):
    pass


def ask(system, messages, max_tokens=32000, effort="medium", model=None):
    """Returns (text, usage dict)."""
    import anthropic
    client = anthropic.Anthropic()
    try:
        with client.messages.stream(model=model or MODEL, max_tokens=max_tokens, system=system,
                                    messages=messages,
                                    extra_body={"thinking": {"type": "adaptive"},
                                                "output_config": {"effort": effort}}) as stream:
            msg = stream.get_final_message()
    except anthropic.AuthenticationError:
        raise ClaudeError("Claude didn't accept the API key. Check it in Settings.")
    except anthropic.BadRequestError as e:
        body = getattr(e, "body", None) or {}
        detail = (body.get("error") or {}).get("message") if isinstance(body, dict) else None
        raise ClaudeError(f"Claude refused the request: {detail or e}")
    except anthropic.RateLimitError:
        raise ClaudeError("Claude is rate-limiting this key right now. Wait a minute and press Retry.")
    except anthropic.APIConnectionError:
        raise ClaudeError("Couldn't reach Claude. Check the internet connection and press Retry.")
    text = "".join(b.text for b in msg.content if b.type == "text")
    usage = {"input": msg.usage.input_tokens, "output": msg.usage.output_tokens}
    if msg.stop_reason == "max_tokens":
        raise ClaudeError(f"Claude's answer was cut off at {max_tokens} tokens before it finished. Press Retry.")
    if not text.strip():
        raise ClaudeError("Claude returned no answer text. Press Retry.")
    return text, usage


def extract_json(text):
    """The JSON object inside an answer (models sometimes wrap it in a sentence or code fence)."""
    a, b = text.find("{"), text.rfind("}")
    if a < 0 or b <= a:
        raise ValueError("the answer had no JSON object in it")
    return json.loads(text[a:b + 1])
