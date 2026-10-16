"""AgentCore Runtime entry point.

BedrockAgentCoreApp automatically exposes `/invocations` and `/ping` per the AgentCore Runtime protocol.
Inbound auth (Cognito JWT) is handled at the platform layer by AgentCore Runtime's Inbound Identity,
so the container does not re-verify the signature; this focuses on handing the user's question to the Strands Agent and returning the answer.

Call contract:
  request body: {"prompt": "Which department used the most credits last week?", "session_id": "..."}
  response:     {"reply": "...", "session_id": "..."}
"""
from __future__ import annotations

from bedrock_agentcore.runtime import BedrockAgentCoreApp

try:
    from agent import build_agent
except ImportError:
    from backend.agent.agent import build_agent  # type: ignore

app = BedrockAgentCoreApp()
_agent = build_agent()


@app.entrypoint
def invoke(payload: dict) -> dict:
    prompt = (payload or {}).get("prompt") or (payload or {}).get("input") or ""
    if not prompt:
        return {"reply": "Please provide a usage-related question, e.g. which department used the most credits this month?"}
    result = _agent(prompt)
    # The strands Agent returns a result object; str() gets the final text
    reply = str(result)
    out = {"reply": reply}
    if payload and payload.get("session_id"):
        out["session_id"] = payload["session_id"]
    return out


if __name__ == "__main__":
    app.run()
