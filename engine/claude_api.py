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


def ask(system, messages, max_tokens=32000, effort="medium", model=None, on_chars=None):
    """Returns (text, usage dict). on_chars(n) is told how many characters (thinking + answer) have
    streamed in so far, for a progress bar."""
    import anthropic
    client = anthropic.Anthropic()
    try:
        with client.messages.stream(model=model or MODEL, max_tokens=max_tokens, system=system,
                                    messages=messages,
                                    extra_body={"thinking": {"type": "adaptive"},
                                                "output_config": {"effort": effort}}) as stream:
            if on_chars:
                n = 0
                for event in stream:
                    if event.type == "content_block_delta":
                        dl = event.delta
                        n += len(getattr(dl, "text", "") or getattr(dl, "thinking", "") or "")
                        try:
                            on_chars(n)
                        except Exception:
                            pass
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


def converse(system, messages, tools, run_tool, effort="low", max_rounds=8, model=None,
             max_tokens=16000, client=None):
    """A chat turn where Claude may use tools. run_tool(name, input) -> (result text, is_error).
    `messages` is extended in place. Returns (final reply text, [(tool name, input, result, is_error)])."""
    import anthropic
    client = client or anthropic.Anthropic()
    used = []
    for _ in range(max_rounds):
        try:
            with client.messages.stream(model=model or MODEL, max_tokens=max_tokens, system=system,
                                        messages=messages, tools=tools,
                                        extra_body={"thinking": {"type": "adaptive"},
                                                    "output_config": {"effort": effort}}) as stream:
                msg = stream.get_final_message()
        except anthropic.AuthenticationError:
            raise ClaudeError("Claude didn't accept the API key. Check it in Settings.")
        except anthropic.RateLimitError:
            raise ClaudeError("Claude is rate-limiting this key right now. Wait a minute and try again.")
        except anthropic.APIConnectionError:
            raise ClaudeError("Couldn't reach Claude. Check the internet connection and try again.")
        except anthropic.BadRequestError as e:
            body = getattr(e, "body", None) or {}
            detail = (body.get("error") or {}).get("message") if isinstance(body, dict) else None
            raise ClaudeError(f"Claude refused the request: {detail or e}")
        messages.append({"role": "assistant", "content": [b.model_dump(exclude_none=True) for b in msg.content]})
        calls = [b for b in msg.content if b.type == "tool_use"]
        text = "".join(b.text for b in msg.content if b.type == "text").strip()
        if msg.stop_reason != "tool_use" or not calls:
            return text, used
        results = []
        for c in calls:
            try:
                out, is_err = run_tool(c.name, c.input)
            except Exception as e:                       # a tool bug becomes a message, not a crash
                out, is_err = f"That didn't work: {e}", True
            used.append((c.name, c.input, out, is_err))
            results.append({"type": "tool_result", "tool_use_id": c.id, "content": out, "is_error": is_err})
        messages.append({"role": "user", "content": results})
    return "I took too many steps on that one. Could you say it more specifically?", used
