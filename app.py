"""
Minimal OpenAI-compatible gateway on top of litellm.Router.

This is the entire server. It depends on litellm as an ordinary pinned
library (see pyproject.toml) -- litellm/proxy/ is never imported, never
edited, never deleted. The frozen contract this file builds against is:
  1. the config.yaml model_list/router_settings schema (config.py)
  2. litellm.Router(model_list=...).acompletion(**openai_body)

Routes are OpenAI wire-format and that contract must not change.
"""

import os

import litellm

if os.getenv("LITELLM_DEBUG", "false").lower() == "true":
    litellm._turn_on_debug()

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from config import load_config

CONFIG_PATH = os.environ.get("LITELLM_GATEWAY_CONFIG", "config.yaml")

cfg = load_config(CONFIG_PATH)
router = litellm.Router(model_list=cfg["model_list"], **cfg["router_kwargs"])

app = FastAPI(title="litellm-gateway-minimal")


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    try:
        response = await router.acompletion(**body)
    except litellm.exceptions.APIError as e:
        return JSONResponse(status_code=getattr(e, "status_code", 500), content={"error": str(e)})
    return response.model_dump() if hasattr(response, "model_dump") else response


@app.get("/v1/models")
async def list_models():
    return {
        "object": "list",
        "data": [
            {
                "id": m["model_name"],
                "object": "model",
                "owned_by": "litellm-gateway",
            }
            for m in cfg["model_list"]
        ],
    }


@app.get("/health/liveliness")
async def liveliness():
    return "I'm alive!"


@app.get("/modconf")
async def modconf():
    """Dumps the model_list as loaded from config.yaml, api_key redacted."""
    out = []
    for m in cfg["model_list"]:
        params = dict(m.get("litellm_params", {}))
        if "api_key" in params:
            params["api_key"] = "***"
        out.append({"model_name": m.get("model_name"), "litellm_params": params})
    return out


@app.get("/stats")
async def stats():
    groups = {m["model_name"] for m in cfg["model_list"]}
    out = {}
    for g in groups:
        # total/success/fail_calls are keyed by litellm_params["model"]
        # (the underlying provider model string), NOT by model_name (the
        # public group alias) -- sum across every deployment in the group.
        deployment_models = {
            m["litellm_params"]["model"]
            for m in cfg["model_list"]
            if m["model_name"] == g
        }
        itpm, otpm = await router.get_model_group_io_token_usage(g)
        out[g] = {
            "total_calls": sum(router.total_calls.get(dm, 0) for dm in deployment_models),
            "success_calls": sum(router.success_calls.get(dm, 0) for dm in deployment_models),
            "fail_calls": sum(router.fail_calls.get(dm, 0) for dm in deployment_models),
            "input_tpm": itpm,
            "output_tpm": otpm,
        }
    return out


def main() -> None:
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 4000)))


if __name__ == "__main__":
    main()

