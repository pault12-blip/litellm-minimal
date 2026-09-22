# litellm-minimal

 Minimal OpenAI-compatible gateway on top of `litellm.Router`. 
 No fork, no proxy internals.

## Costs

 FOCUS 1.2 export + checkpoint reconciliation are impelemented.
 Implemented via text files, no DB dependencies
    

## Endpoints

- `POST /v1/chat/completions`
- `GET /v1/models`
- `GET /health/liveliness`

## Setup

    mkdir -p ~/src && cd ~/src
    git clone https://github.com/BerriAI/litellm.git litellm
    git clone https://github.com/pault12-blip/litellm-minimal.git
    cd litellm-minimal

    python3 -m venv .venv && source .venv/bin/activate
    pip install fastapi uvicorn pyyaml

Do not `pip install litellm` or `pip install -e .`.

## Config

`config.yaml`:

    model_list:
      - model_name: gpt-4o-mini
        litellm_params:
          model: openai/gpt-4o-mini
          api_key: os.environ/OPENAI_API_KEY

    router_settings: {}

## Run

`start.sh`:

    #!/bin/sh

    export PYTHONPATH=../litellm:$PYTHONPATH

    #export LITELLM_DEBUG=true

    /usr/bin/python3 app.py

Binds `0.0.0.0:${PORT:-4000}`.

## Call

    curl http://localhost:4000/v1/chat/completions \
      -H "Content-Type: application/json" \
      -d '{"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}]}'

## Example

    litellm-minimal$ ./start.sh
    stopping: /usr/bin/python3 /litellm-minimal/app.py (pid 984944)
    starting: /usr/bin/python3 /litellm-minimal/app.py (debug=false)
    pid 985976

    litellm-minimal$ ./start.sh status
    running: /usr/bin/python3 /litellm-minimal/app.py (pid 985976)
    health: "I'm alive!"

    litellm-minimal$ ./t.sh

    {"id":"chatcmpl-EPwSoHSgpf7WXwkkLUFz8RLQOpEbs","created":1789851642,"model":"gpt-4o-mini-2024-07-18","object":"chat.completion","system_fingerprint":"fp_f70887b4d3","choices":[{"finish_reason":"stop","index":0,"message":{"content":"Hello! How can I assist you today?","role":"assistant","tool_calls":null,"function_call":null,"provider_specific_fields":{"refusal":null},"annotations":[]},"provider_specific_fields":{}}],"usage":{"completion_tokens":9,"prompt_tokens":13,"total_tokens":22,"completion_tokens_details":{"accepted_prediction_tokens":0,"audio_tokens":0,"reasoning_tokens":0,"rejected_prediction_tokens":0},"prompt_tokens_details":{"audio_tokens":0,"cached_tokens":0}},"moderation":null,"service_tier":"default"}

    litellm-minimal$ ./stats.sh

    *** stats
    {"gpt-4o-mini":{"total_calls":1,"success_calls":1,"fail_calls":0,"input_tpm":13,"output_tpm":16384}}
    *** modconf
    [{"model_name":"gpt-4o-mini","litellm_params":{"model":"openai/gpt-4o-mini","api_key":"***","itpm":1000000,"otpm":1000000}}]

    cat litellm-minimal/failures.json

    {"ts": 1789914647.194932, "model_name": "mini", "deployment_model": "", "error_type": "RateLimitError", "status_code": 429, "message": "litellm.RateLimitError: Model rate limit exceeded. OTPM limit=1000000, current usage=1024000. Received Model Group=mini\nAvailable Model Group Fallbacks=None"}
    {"ts": 1789914648.7923481, "model_name": "mini", "deployment_model": "", "error_type": "RateLimitError", "status_code": 429, "message": "litellm.RateLimitError: Model rate limit exceeded. OTPM limit=1000000, current usage=1024000. Received Model Group=mini\nAvailable Model Group Fallbacks=None"}
    {"ts": 1789935834.2116854, "model_name": "mini", "deployment_model": "openai/gpt-5.4-mini", "error_type": "InternalServerError", "status_code": 500, "message": "litellm.InternalServerError: InternalServerError: OpenAIException - Missing credentials. Please pass an `api_key`, `workload_identity`, `admin_api_key`, or set the `OPENAI_API_KEY` or `OPENAI_ADMIN_KEY` environment variable.. Received Model Group=mini\nAvailable Model Group Fallbacks=None LiteLLM Retried: 2 times, LiteLLM Max Retries: 2"}

## License

LICENSE IS MIT.

litellm remains under its own license.

