# litellm-minimal

Minimal OpenAI-compatible gateway on top of `litellm.Router`. No fork, no proxy internals.

## Endpoints

- `POST /v1/chat/completions`
- `GET /v1/models`
- `GET /health/liveliness`

## Setup

    mkdir -p ~/src && cd ~/src
    git clone https://github.com/BerriAI/litellm.git litellm
    git clone https://github.com/<your-org>/litellm-minimal.git
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

    export PYTHONPATH=/u2/LLL/litellm:$PYTHONPATH
    #export LITELLM_DEBUG=true

    /usr/bin/python3 app.py

Binds `0.0.0.0:${PORT:-4000}`.

## Call

    curl http://localhost:4000/v1/chat/completions \
      -H "Content-Type: application/json" \
      -d '{"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}]}'

## License

See `LICENSE`. litellm remains under its own license.

