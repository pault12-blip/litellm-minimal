#!/usr/bin/sh

curl http://localhost:4000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "ngrok",
    "messages": [
      {"role": "user", "content": "write fibbonachi in perl."}
    ]
  }'
