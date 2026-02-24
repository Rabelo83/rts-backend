#!/usr/bin/env bash
# run_local.sh — start the Flask app locally with Ollama as the LLM
# Usage: bash run_local.sh

set -a
source .env.local
set +a

echo "Starting RTS backend on http://localhost:5000"
echo "  LLM: ${OPENAI_BASE_URL} / model: ${OPENAI_MODEL}"
echo "  BusTime API key: ${BUS_API_KEY:0:4}****"
echo ""

flask --app app run --port 5000 --debug
