import os
from uuid import uuid4

from dotenv import load_dotenv
from flask import Flask, Response, jsonify, render_template, request, stream_with_context
from langchain_core.messages import AIMessage, HumanMessage
from langchain_openai import ChatOpenAI


load_dotenv()

app = Flask(__name__)

DEFAULT_MODEL = "deepseek-ai/DeepSeek-V3.2"
MODEL_OPTIONS = [
    "deepseek-ai/DeepSeek-V3.2",
    "Qwen/Qwen3.6-27B",
]
URL_OPTIONS = {
    "global": os.getenv("SILICONFLOW_URL", ""),
    "cn": os.getenv("SILICONFLOW_URL_CN", ""),
}
DEFAULT_BASE_URL = URL_OPTIONS["cn"] or URL_OPTIONS["global"]


@app.after_request
def disable_cache(response):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


def build_llm(model: str, base_url: str) -> ChatOpenAI:
    api_key = os.getenv("SILICONFLOW_API_KEY")
    if not api_key:
        raise ValueError("Missing SILICONFLOW_API_KEY in .env")

    return ChatOpenAI(
        model=model or DEFAULT_MODEL,
        api_key=api_key,
        base_url=base_url or DEFAULT_BASE_URL,
        temperature=0.7,
    )


def build_messages(history: list[dict]) -> list:
    messages = []
    for item in history:
        role = item.get("role")
        content = (item.get("content") or "").strip()
        if not content:
            continue
        if role == "user":
            messages.append(HumanMessage(content=content))
        elif role == "assistant":
            messages.append(AIMessage(content=content))
    return messages


def stringify_content(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(item.get("text", ""))
            else:
                parts.append(str(item))
        return "\n".join(part for part in parts if part)
    return str(content)


def normalize_content(content) -> str:
    return stringify_content(content).lstrip("\r\n")


@app.get("/")
def index():
    return render_template(
        "index.html",
        default_model=DEFAULT_MODEL,
        model_options=MODEL_OPTIONS,
        global_url=URL_OPTIONS["global"],
        cn_url=URL_OPTIONS["cn"],
        default_url=DEFAULT_BASE_URL,
    )


@app.get("/api/config")
def config():
    return jsonify(
        {
            "defaultModel": DEFAULT_MODEL,
            "modelOptions": MODEL_OPTIONS,
            "urlOptions": URL_OPTIONS,
            "defaultUrl": DEFAULT_BASE_URL,
        }
    )


@app.post("/api/chat")
def chat():
    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()
    model = (data.get("model") or DEFAULT_MODEL).strip()
    base_url = (data.get("baseUrl") or DEFAULT_BASE_URL).strip()
    history = data.get("history") or []

    if not message:
        return jsonify({"error": "message is required"}), 400

    if model not in MODEL_OPTIONS:
        return jsonify({"error": "unsupported model"}), 400

    valid_urls = {value for value in URL_OPTIONS.values() if value}
    if base_url not in valid_urls:
        return jsonify({"error": "unsupported url"}), 400

    try:
        llm = build_llm(model, base_url)
        messages = build_messages(history)
        messages.append(HumanMessage(content=message))
        response = llm.invoke(messages)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    return jsonify(
        {
            "id": str(uuid4()),
            "role": "assistant",
            "content": normalize_content(response.content),
        }
    )


@app.post("/api/chat/stream")
def chat_stream():
    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()
    model = (data.get("model") or DEFAULT_MODEL).strip()
    base_url = (data.get("baseUrl") or DEFAULT_BASE_URL).strip()
    history = data.get("history") or []

    if not message:
        return jsonify({"error": "message is required"}), 400

    if model not in MODEL_OPTIONS:
        return jsonify({"error": "unsupported model"}), 400

    valid_urls = {value for value in URL_OPTIONS.values() if value}
    if base_url not in valid_urls:
        return jsonify({"error": "unsupported url"}), 400

    def generate():
        try:
            llm = build_llm(model, base_url)
            messages = build_messages(history)
            messages.append(HumanMessage(content=message))

            first_chunk = True
            for chunk in llm.stream(messages):
                text = stringify_content(chunk.content)
                if first_chunk:
                    text = text.lstrip("\r\n")
                    first_chunk = False
                if text:
                    yield text
        except Exception as exc:
            yield f"\n[ERROR] {exc}"

    return Response(
        stream_with_context(generate()),
        mimetype="text/plain; charset=utf-8",
    )


if __name__ == "__main__":
    app.run(debug=True, host="127.0.0.1", port=5000)
