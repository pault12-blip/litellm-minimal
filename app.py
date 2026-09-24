"""
Minimal OpenAI-compatible gateway on top of litellm.Router.

This is the entire server. It depends on litellm as an ordinary pinned
library (see pyproject.toml) -- litellm/proxy/ is never imported, never
edited, never deleted. The frozen contract this file builds against is:
  1. the config.yaml model_list/router_settings schema (config.py)
  2. litellm.Router(model_list=...).acompletion(**openai_body)

Routes are OpenAI wire-format and that contract must not change.
"""

import asyncio
import json
import os
import time
import uuid

import litellm

if os.getenv("LITELLM_DEBUG", "false").lower() == "true":
    litellm._turn_on_debug()

try:
    from custom_callbacks import custom_handler_instance
    litellm.callbacks = [custom_handler_instance]
except ModuleNotFoundError:
    pass

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from config import load_config

CONFIG_PATH = os.environ.get("LITELLM_GATEWAY_CONFIG", "config.yaml")
FAILURES_LOG_PATH = os.environ.get("LITELLM_GATEWAY_FAILURES_LOG", "failures.jsonl")

cfg = load_config(CONFIG_PATH)
router = litellm.Router(model_list=cfg["model_list"], **cfg["router_kwargs"])

app = FastAPI(title="litellm-gateway-minimal")


def _record_failure(model_name: str, deployment_model: str | None, error: Exception) -> None:
    entry = {
        "ts": time.time(),
        "model_name": model_name,
        "deployment_model": deployment_model,
        "error_type": type(error).__name__,
        "status_code": getattr(error, "status_code", None),
        "message": str(error),
    }
    with open(FAILURES_LOG_PATH, "a") as f:
        f.write(json.dumps(entry) + "\n")


async def _try_direct_fallbacks(body: dict, model_name: str, already_failed_models: set[str]):
    """Router gave up (e.g. a 402, which litellm treats as non-retryable
    regardless of sibling deployments) even though other deployments exist
    in this model_name group. Walk the remaining deployments directly via
    litellm.acompletion, bypassing Router's internal retry/cooldown gate,
    which owns exactly this decision and is the one thing we don't touch.
    """
    deployments = [
        m for m in cfg["model_list"]
        if m["model_name"] == model_name
        and m["litellm_params"]["model"] not in already_failed_models
    ]
    last_error: Exception | None = None
    for deployment in deployments:
        params = dict(deployment["litellm_params"])
        deployment_model = params.pop("model")
        params.pop("itpm", None)
        params.pop("otpm", None)
        params.pop("tpm", None)
        params.pop("rpm", None)
        try:
            call_kwargs = {**body, **params, "model": deployment_model}
            return await litellm.acompletion(**call_kwargs)
        except Exception as e:
            _record_failure(model_name, deployment_model, e)
            last_error = e
    if last_error is not None:
        raise last_error
    raise RuntimeError(f"no fallback deployments left for model_name={model_name!r}")


async def _fire_silent_mirror(body: dict, primary_model_name: str, silent_model_name: str, correlation_id: str) -> None:
    """Fire-and-forget: same messages, routed to the silent deployment
    through the same router.acompletion() surface used everywhere else in
    this project. Never litellm.Router's own silent_model dispatch --
    config.py has already stripped that key before Router ever saw it.
    Errors here are captured by custom_callbacks.py's failure hook, same
    as the primary call, not swallowed.
    """
    silent_body = {**body, "model": silent_model_name, "metadata": {**body.get("metadata", {})}}
    silent_body["metadata"]["drift_correlation_id"] = correlation_id
    silent_body["metadata"]["drift_role"] = "silent"
    try:
        await router.acompletion(**silent_body)
    except Exception:
        pass  # already recorded by the failure callback; nothing more to do here


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    model_name = body.get("model")

    silent_model_name = cfg["silent_pairs"].get(model_name)
    if silent_model_name is not None:
        correlation_id = str(uuid.uuid4())
        body.setdefault("metadata", {})
        body["metadata"]["drift_correlation_id"] = correlation_id
        body["metadata"]["drift_role"] = "primary"
        asyncio.create_task(_fire_silent_mirror(body, model_name, silent_model_name, correlation_id))

    try:
        response = await router.acompletion(**body)
    except Exception as e:
        failed_model = getattr(e, "model", None)
        _record_failure(model_name, failed_model, e)
        already_failed = {failed_model} if failed_model else set()
        try:
            response = await _try_direct_fallbacks(body, model_name, already_failed)
        except Exception as e2:
            return JSONResponse(status_code=getattr(e2, "status_code", 500), content={"error": str(e2)})
    return response.model_dump() if hasattr(response, "model_dump") else response


@app.get("/failures")
async def failures(limit: int = 50):
    if not os.path.exists(FAILURES_LOG_PATH):
        return []
    with open(FAILURES_LOG_PATH) as f:
        lines = f.readlines()
    return [json.loads(line) for line in lines[-limit:]]


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
