#!/usr/bin/sh

curl http://localhost:4000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt3",
    "messages": [
      {"role": "user", "content": "Say hello in one sentence."}
    ]
  }'
