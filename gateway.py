#!/usr/bin/env python3
# language: Python 3, target: Termux/Windows/Linux
# deepseek-gateway - OpenAI-совместимый шлюз к burngate (DeepSeek V4.1 Flash).
# Один файл, ноль зависимостей.
#
#   запрос клиента (джанитор/таверна/код) -> этот шлюз -> burngate
#
# Контракт burngate: POST https://burngate.space/api/v1/chat/completions
#                    GET  https://burngate.space/api/v1/models
# Авторизация: Authorization: Bearer <ключ gk_...>

import argparse
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BURNGATE_BASE = os.environ.get("BURNGATE_BASE", "https://burngate.space/api/v1").rstrip("/")
TIMEOUT = int(os.environ.get("GATEWAY_TIMEOUT", "300"))
UA = "deepseek-gateway/1.0"

# Модели burngate. Первая - модель по умолчанию (меняется через BURNGATE_MODEL или serve --model).
MODELS = [
    "deepseek/deepseek-v4.1-flash",
    "google/gemini-3.8-flash",
    "stealth/space-bunny-alpha",
    "xiaomi/mimo-v2.6-flash",
]

# Короткие имена для удобных клиентов -> полный id burngate.
MODEL_ALIASES = {
    "deepseek": "deepseek/deepseek-v4.1-flash",
    "deepseek-v4.1-flash": "deepseek/deepseek-v4.1-flash",
    "deepseek-v4.1": "deepseek/deepseek-v4.1-flash",
    "deepseek-4.1": "deepseek/deepseek-v4.1-flash",
    "deepseek-4.1-flash": "deepseek/deepseek-v4.1-flash",
    "deepseek-flash": "deepseek/deepseek-v4.1-flash",
    "gemini": "google/gemini-3.8-flash",
    "gemini-3.8-flash": "google/gemini-3.8-flash",
    "gemini-3.8": "google/gemini-3.8-flash",
    "gemini-flash": "google/gemini-3.8-flash",
    "bunny": "stealth/space-bunny-alpha",
    "space-bunny": "stealth/space-bunny-alpha",
    "space-bunny-alpha": "stealth/space-bunny-alpha",
    "mimo": "xiaomi/mimo-v2.6-flash",
    "mimo-v2.6-flash": "xiaomi/mimo-v2.6-flash",
}


def normalize_model(name):
    """Имя модели к виду алиасов: нижний регистр, пробелы/подчёркивания -> дефисы."""
    key = name.strip().lower().replace("_", "-").replace(" ", "-")
    while "--" in key:
        key = key.replace("--", "-")
    return key


_ENV_MODEL = normalize_model(os.environ.get("BURNGATE_MODEL", ""))
DEFAULT_MODEL = MODEL_ALIASES.get(_ENV_MODEL, _ENV_MODEL) if _ENV_MODEL else MODELS[0]
DEFAULT_EFFORT = os.environ.get("BURNGATE_EFFORT", "max").strip() or "max"
RATE_COOLDOWN = float(os.environ.get("GATEWAY_RATE_COOLDOWN", "20").strip() or "20")

INTERNAL_FIELDS = ("_session", "_key")

_COOLDOWNS = {}
_COOLDOWN_LOCK = threading.Lock()


def _is_rate_limit(msg):
    low = str(msg).lower()
    return "rate limit" in low or "too many" in low or "overloaded" in low


def _exc_msg(e):
    if isinstance(e, GatewayError):
        return e.body
    return f"network: {e}"


def _filter_opts(model, opts):
    """Gemini у burngate падает на ненулевых presence/frequency_penalty - выкидываем их."""
    if model.startswith("google/"):
        for key in ("presence_penalty", "frequency_penalty"):
            opts.pop(key, None)


def _set_cooldown(model, seconds=None):
    secs = RATE_COOLDOWN if seconds is None else seconds
    if secs <= 0:
        return
    with _COOLDOWN_LOCK:
        _COOLDOWNS[model] = max(_COOLDOWNS.get(model, 0), time.time() + secs)


def _cooldown_wait(model):
    with _COOLDOWN_LOCK:
        until = _COOLDOWNS.get(model, 0)
    delay = until - time.time()
    if delay > 0:
        time.sleep(min(delay, 90))


def load_dotenv(path):
    """Простой парсер .env: KEY=VALUE, без зависимостей. Не перекрывает уже заданное."""
    if not os.path.isfile(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                k, v = k.strip(), v.strip().strip("'\"")
                if k and v and k not in os.environ:
                    os.environ[k] = v
    except OSError:
        pass


ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")

load_dotenv(ENV_PATH)


def api_keys():
    """Один или несколько ключей burngate: BURNGATE_API_KEY / BURNGATE_API_KEYS через запятую."""
    raw = os.environ.get("BURNGATE_API_KEYS", "") or os.environ.get("BURNGATE_API_KEY", "")
    return [k.strip() for k in raw.split(",") if k.strip()]


def save_key(key, path=ENV_PATH):
    """Сохраняет ключ в .env, заменяя прежнее значение BURNGATE_API_KEY."""
    lines = []
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                lines = [ln for ln in f.read().splitlines() if not ln.strip().startswith("BURNGATE_API_KEY=")]
        except OSError:
            lines = []
    lines.append(f"BURNGATE_API_KEY={key}")
    try:
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(lines).strip() + "\n")
        return True
    except OSError:
        return False


def ensure_key():
    """Первый запуск: спрашивает ключ burngate и сохраняет его в .env."""
    if api_keys():
        return True
    if not sys.stdin or not sys.stdin.isatty():
        print("ERROR: нет ключа. Впиши BURNGATE_API_KEY=gk_... в .env рядом с gateway.py", file=sys.stderr)
        return False
    print("Ключ burngate не найден — настроим один раз.")
    print("Взять ключ: https://burngate.space -> API keys.")
    while True:
        try:
            import getpass
            key = getpass.getpass("Вставь ключ burngate (gk_...): ").strip()
        except Exception:
            key = input("Вставь ключ burngate (gk_...): ").strip()
        if key:
            break
        print("Пусто, попробуй ещё раз.")
    os.environ["BURNGATE_API_KEY"] = key
    if save_key(key):
        print(f"Ключ сохранён в {ENV_PATH}\nБольше спрашивать не буду. Запускай снова в любой момент.")
    else:
        print("Не удалось записать .env — ключ принят только на этот запуск.", file=sys.stderr)
    return True


def resolve_model(name):
    """Короткое имя или отображаемое название -> полный id burngate. Неизвестное возвращаем как есть.
    Понимает любой регистр, пробелы и подчёркивания: "Gemini 3.8 Flash" -> google/gemini-3.8-flash."""
    if not name:
        return DEFAULT_MODEL
    raw = name.strip()
    return MODEL_ALIASES.get(normalize_model(raw), raw)


class GatewayError(Exception):
    def __init__(self, code, body, retry_after=None):
        self.code = code
        self.body = body
        self.retry_after = retry_after
        super().__init__(f"HTTP {code}: {body[:200]}")

    def is_auth_error(self):
        return self.code in (401, 402, 403)

    def is_rate_limited(self):
        return self.code in (429, 529) or "rate limit" in self.body.lower() or "RateLimit" in self.body

    def is_network(self):
        return self.code == 0

    def retryable_with_next_key(self):
        return self.is_auth_error() or self.is_rate_limited() or self.is_network()


def _request(payload):
    """POST /chat/completions с перебором ключей при 401/402/403/429 и сетевых сбоях."""
    model = payload.get("model")
    _cooldown_wait(model)
    keys = api_keys()
    if not keys:
        raise GatewayError(0, "BURNGATE_API_KEY не задан: впиши ключ в .env рядом с gateway.py", None)
    if payload.get("_key") is not None:
        keys = [payload["_key"]]
    data = json.dumps({k: v for k, v in payload.items() if k not in INTERNAL_FIELDS}).encode("utf-8")
    url = f"{BURNGATE_BASE}/chat/completions"
    last = None
    for i, key in enumerate(keys):
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "User-Agent": UA,
        }
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            return urllib.request.urlopen(req, timeout=TIMEOUT)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "ignore")[:500]
            last = GatewayError(e.code, body, e.headers.get("retry-after"))
            if last.is_rate_limited():
                _set_cooldown(model)
            if last.retryable_with_next_key() and i < len(keys) - 1:
                if last.is_rate_limited():
                    time.sleep(1)
                continue
            raise last
        except urllib.error.URLError as e:
            last = GatewayError(0, f"network: {e.reason}", None)
            if i < len(keys) - 1:
                time.sleep(1)
                continue
            raise last
    raise last


def _iter_sse(resp):
    """SSE: yield (json, done)."""
    buf = b""
    for raw in resp:
        buf += raw
        while b"\n\n" in buf:
            chunk, buf = buf.split(b"\n\n", 1)
            for line in chunk.split(b"\n"):
                line = line.strip()
                if not line or not line.startswith(b"data:"):
                    continue
                item = line[5:].strip()
                if item == b"[DONE]":
                    yield None, True
                    return
                try:
                    yield json.loads(item.decode("utf-8")), False
                except json.JSONDecodeError:
                    continue
    yield None, True


def chat(model, messages, stream=False, **opts):
    """Одиночный вызов burngate. Возвращает dict (не-стрим) или итератор чанков."""
    payload = {"model": resolve_model(model), "messages": messages, "stream": bool(stream)}
    payload.update({k: v for k, v in opts.items() if v is not None})
    resp = _request(payload)
    if not stream:
        return json.loads(resp.read().decode("utf-8"))
    return _iter_sse(resp)


def list_models():
    """Список моделей burngate; при недоступности - локальный список."""
    req = urllib.request.Request(f"{BURNGATE_BASE}/models", headers={"User-Agent": UA})
    keys = api_keys()
    if keys:
        req.add_header("Authorization", f"Bearer {keys[0]}")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode("utf-8"))
            ids = [m.get("id") or m.get("model") or m for m in (data.get("data") or data.get("models") or [])]
            return ids or list(MODELS)
    except Exception:
        return list(MODELS)


def estimate_tokens(messages):
    """Грубая оценка: ~3 символа на токен."""
    return sum(len(str(m.get("content", ""))) // 3 for m in messages)


def _summarize(text):
    prompt = (
        "Сожми текст в краткое резюме. Сохрани все факты, код, имена, числа "
        "и незавершённые задачи. Числа, коды, имена и ключевые термины перечисли "
        "списком, ничего не теряя. Только резюме, без пояснений.\n\n" + text
    )
    resp = chat(DEFAULT_MODEL, [{"role": "user", "content": prompt}], max_tokens=4000, reasoning_effort=DEFAULT_EFFORT)
    return (resp.get("choices") or [{}])[0].get("message", {}).get("content") or ""


def compact_if_needed(messages, limit=None):
    """Авто-расширение контекста: при превышении порога сжимает историю
    в резюме (чанками, чтобы не упираться в таймауты)."""
    limit = limit or int(os.environ.get("GATEWAY_MAX_CONTEXT", "0"))
    if not limit or estimate_tokens(messages) <= limit:
        return messages
    chunk_size = int(os.environ.get("GATEWAY_COMPACT_CHUNK", "25000"))
    for _ in range(3):
        if estimate_tokens(messages) <= limit:
            break
        keep = messages[-4:]
        history = messages[:-4]
        text = "\n".join(f"{m.get('role')}: {str(m.get('content'))}" for m in history)
        chunks = [text[i:i + chunk_size] for i in range(0, len(text), chunk_size)]
        summaries = []
        for c in chunks:
            try:
                s = _summarize(c)
            except GatewayError:
                return messages
            if s:
                summaries.append(s)
        if not summaries:
            return messages
        messages = [{"role": "system", "content": "Резюме прошлого диалога: " + " ".join(summaries)}] + keep
    return messages


def _print_stream(gen):
    """Печатает SSE-поток, возвращает собранный текст ответа."""
    parts = []
    for chunk, done in gen:
        if done:
            break
        for c in chunk.get("choices", []):
            d = c.get("delta", {})
            if d.get("reasoning_content"):
                print(f"[think] {d['reasoning_content']}", file=sys.stderr, flush=True)
            if d.get("content"):
                parts.append(d["content"])
                print(d["content"], end="", flush=True)
    print()
    return "".join(parts)


def run_interactive(args):
    model = resolve_model(args.model) if args.model else DEFAULT_MODEL
    limit = int(os.environ.get("GATEWAY_MAX_CONTEXT", "0"))
    messages = []
    print(f"chat [burngate], model={model}, max_context={limit or 'off'}, /model <name> /new /quit")
    while True:
        try:
            line = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not line:
            continue
        if line in ("/quit", "/q", "/exit"):
            return
        if line == "/new":
            messages = []
            print("(history cleared)")
            continue
        if line.startswith("/model "):
            model = resolve_model(line.split(" ", 1)[1].strip())
            print(f"(model -> {model})")
            continue
        messages.append({"role": "user", "content": line})
        messages = compact_if_needed(messages, limit)
        try:
            gen = chat(model, messages, stream=True, reasoning_effort=args.effort)
            answer = _print_stream(gen)
            messages.append({"role": "assistant", "content": answer or "(пустой ответ)"})
        except GatewayError as e:
            _report(e)
            continue


def run_cli(args):
    if args.prompt is None:
        run_interactive(args)
        return
    model = resolve_model(args.model) if args.model else DEFAULT_MODEL
    messages = [{"role": "user", "content": args.prompt}]
    try:
        if args.stream:
            _print_stream(chat(model, messages, stream=True, reasoning_effort=args.effort))
        else:
            resp = chat(model, messages, reasoning_effort=args.effort)
            msg = (resp.get("choices") or [{}])[0].get("message", {})
            if msg.get("reasoning_content"):
                print(f"[think] {msg['reasoning_content']}", file=sys.stderr)
            print(msg.get("content") or "")
    except GatewayError as e:
        _report(e)
        sys.exit(2)


def _report(e):
    print(f"GATEWAY ERROR {e.code}: {e.body[:300]}", file=sys.stderr)
    if e.is_rate_limited():
        print("Rate limit burngate. Подожди или добавь второй ключ в .env.", file=sys.stderr)
    elif e.is_auth_error():
        print("Ключ burngate не принят. Проверь BURNGATE_API_KEY в .env.", file=sys.stderr)


class GatewayHandler(BaseHTTPRequestHandler):
    server_version = "DeepSeekGateway/1.0"
    max_context = int(os.environ.get("GATEWAY_MAX_CONTEXT", "0"))

    def _cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, x-api-key")
        self.send_header("Access-Control-Max-Age", "86400")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors_headers()
        self.end_headers()

    def _auth(self):
        token = os.environ.get("GATEWAY_TOKEN", "")
        if token:
            header = self.headers.get("Authorization", "")
            if header != f"Bearer {token}" and self.headers.get("x-api-key") != token:
                self.send_error(401, "Unauthorized")
                return False
        return True

    def _reply(self, code, obj):
        data = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self._cors_headers()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if not self._auth():
            return
        path = self.path.split("?")[0].rstrip("/")
        if path.endswith("/models"):
            models = list_models()
            for alias in MODEL_ALIASES:
                if alias not in models:
                    models.append(alias)
            self._reply(200, {"object": "list", "data": [{"id": m, "object": "model", "owned_by": "burngate"} for m in models]})
        else:
            self._reply(404, {"error": {"message": f"not found: {self.path}", "type": "invalid_request_error", "code": 404}})

    def do_POST(self):
        if not self._auth():
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
        except Exception:
            self._reply(400, {"error": {"message": "invalid json", "type": "invalid_request_error", "code": 400}})
            return
        path = self.path.split("?")[0].rstrip("/")
        if "chat/completions" not in path and path not in ("", "/v1", "/v1beta"):
            self._reply(404, {"error": {"message": f"not found: {self.path}", "type": "invalid_request_error", "code": 404}})
            return
        requested = body.get("model") or DEFAULT_MODEL
        model = resolve_model(requested)
        messages = body.get("messages") or []
        stream = bool(body.get("stream", False))
        opts = {k: v for k, v in body.items() if k not in ("model", "messages", "stream", "_session", "_key")}
        opts["reasoning_effort"] = DEFAULT_EFFORT
        _filter_opts(model, opts)
        if self.max_context and estimate_tokens(messages) > self.max_context:
            messages = compact_if_needed(messages, self.max_context)
        try:
            if stream:
                err_msg = None
                attempts = 0
                waits = (1, 2)
                gen = None
                while True:
                    fatal = False
                    try:
                        if gen is None:
                            gen = chat(model, messages, stream=True, **opts)
                        chunk, done = next(gen)
                    except StopIteration:
                        chunk, done = None, True
                    except (GatewayError, OSError) as e:
                        chunk, done = {"error": {"message": _exc_msg(e)}}, False
                        fatal = isinstance(e, GatewayError) and e.is_auth_error()
                    if not (isinstance(chunk, dict) and chunk.get("error")):
                        err_msg = None
                        break
                    err = chunk["error"]
                    err_msg = err.get("message") if isinstance(err, dict) else str(err)
                    if attempts == 0:
                        if fatal:
                            waits = ()
                        elif _is_rate_limit(err_msg):
                            _set_cooldown(model)
                            waits = (0, 0, 0)
                        else:
                            waits = (1, 2, 4)
                    if attempts >= len(waits):
                        break
                    if gen is not None:
                        try:
                            gen.close()
                        except Exception:
                            pass
                        gen = None
                    time.sleep(waits[attempts])
                    attempts += 1

                if err_msg is not None:
                    if gen is not None:
                        try:
                            gen.close()
                        except Exception:
                            pass
                    try:
                        resp = chat(model, messages, **opts)
                        text = (resp.get("choices") or [{}])[0].get("message", {}).get("content") or ""
                        self.send_response(200)
                        self._cors_headers()
                        self.send_header("Content-Type", "text/event-stream")
                        self.send_header("Cache-Control", "no-cache")
                        self.end_headers()
                        now = int(time.time())
                        out = {"id": resp.get("id") or "chatcmpl-fallback",
                               "object": "chat.completion.chunk", "created": now, "model": requested,
                               "choices": [{"index": 0, "delta": {"role": "assistant", "content": text}, "finish_reason": None}]}
                        self.wfile.write(f"data: {json.dumps(out)}\n\n".encode("utf-8"))
                        out2 = {"object": "chat.completion.chunk", "created": now, "model": requested,
                                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
                        self.wfile.write(f"data: {json.dumps(out2)}\n\n".encode("utf-8"))
                        self.wfile.write(b"data: [DONE]\n\n")
                        self.wfile.flush()
                    except GatewayError as e:
                        self._reply(502, {"error": {"message": str(e.body)[:400], "type": "stream_error", "code": 502}})
                    except Exception as e:
                        self._reply(502, {"error": {"message": str(e)[:400], "type": "stream_error", "code": 502}})
                    return

                self.send_response(200)
                self._cors_headers()
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()

                def emit(item):
                    out = dict(item)
                    out["model"] = requested
                    self.wfile.write(f"data: {json.dumps(out)}\n\n".encode("utf-8"))
                    self.wfile.flush()

                if isinstance(chunk, dict) and not done:
                    emit(chunk)
                while True:
                    try:
                        item, done = next(gen)
                    except StopIteration:
                        break
                    except (GatewayError, OSError):
                        break
                    if done:
                        break
                    if isinstance(item, dict) and item.get("error"):
                        break
                    emit(item)
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
            else:
                attempts = 0
                waits = (1, 2)
                while True:
                    try:
                        resp = chat(model, messages, **opts)
                        break
                    except GatewayError as e:
                        if attempts == 0:
                            if e.is_rate_limited():
                                _set_cooldown(model)
                                waits = (0, 0, 0)
                            elif e.is_network():
                                waits = (1, 2)
                            else:
                                raise
                        if attempts >= len(waits):
                            raise
                        time.sleep(waits[attempts])
                        attempts += 1
                self._reply(200, resp)
        except GatewayError as e:
            self._reply(e.code if e.code else 502, {"error": {"message": e.body[:400], "type": "api_error", "code": e.code or 502}})
        except Exception as e:
            self._reply(500, {"error": {"message": str(e)[:400], "type": "api_error", "code": 500}})


def run_gateway(args):
    global DEFAULT_MODEL
    if getattr(args, "model", None):
        DEFAULT_MODEL = resolve_model(args.model)
    port = args.port
    GatewayHandler.max_context = args.max_context
    httpd = ThreadingHTTPServer(("0.0.0.0", port), GatewayHandler)
    print(f"deepseek-gateway on 0.0.0.0:{port}  (POST /v1/chat/completions, GET /v1/models)")
    print(f"upstream: {BURNGATE_BASE} | model: {DEFAULT_MODEL} | keys: {len(api_keys())}")
    print(f"auth: {'GATEWAY_TOKEN required' if os.environ.get('GATEWAY_TOKEN') else 'open'}")
    if os.environ.get("GATEWAY_PROBE", "1") != "0":
        _probe()
    httpd.serve_forever()


def _probe():
    """Пробный запрос при старте: показывает, что upstream жив и кто отвечает."""
    model = os.environ.get("GATEWAY_PROBE_MODEL", DEFAULT_MODEL)
    try:
        resp = chat(model, [{"role": "user", "content": "Who are you? Exact model and company. Max 15 words."}],
                    max_tokens=4000, reasoning_effort=DEFAULT_EFFORT)
        who = (resp.get("choices") or [{}])[0].get("message", {}).get("content") or "(пустой ответ)"
        print(f"[probe] {model} -> {who.strip()[:110]}")
    except GatewayError as e:
        print(f"[probe] {model} -> ОШИБКА {e.code}: {e.body[:120]}")
    except Exception as e:
        print(f"[probe] ошибка: {e}")


def main():
    # Windows: консоль cp1251 не кодирует юникод/эмодзи из ответов. Форсим UTF-8.
    for _s, _r in ((sys.stdout, sys.stdout.reconfigure), (sys.stderr, sys.stderr.reconfigure)):
        try:
            _r(encoding="utf-8", errors="replace")
        except Exception:
            pass

    ap = argparse.ArgumentParser(description="gateway.py - burngate client + OpenAI-compatible gateway")
    sub = ap.add_subparsers(dest="cmd")
    c = sub.add_parser("chat", help="разовый запрос")
    c.add_argument("prompt", nargs="?", default=None)
    c.add_argument("-m", "--model", default=None)
    c.add_argument("--stream", action="store_true")
    c.add_argument("--effort", default=DEFAULT_EFFORT,
                   help="reasoning_effort: max/xhigh/high/medium/low/minimal/none (по умолч. %s)" % DEFAULT_EFFORT)
    g = sub.add_parser("serve", help="поднять OpenAI-совместимый шлюз")
    g.add_argument("--port", type=int, default=None)
    g.add_argument("--model", default=None,
                   help="модель по умолчанию: deepseek (по умолч.), gemini или полный id")
    g.add_argument("--max-context", type=int, default=None,
                   help="порог авто-компакта в символах (0 = выключить)")
    sub.add_parser("models", help="список моделей burngate")
    args = ap.parse_args()

    if not ensure_key():
        sys.exit(1)

    if args.cmd == "models":
        for m in list_models():
            print(m)
    elif args.cmd == "serve":
        if args.port is None:
            args.port = int(os.environ.get("GATEWAY_PORT", "8787"))
        if args.max_context is None:
            args.max_context = int(os.environ.get("GATEWAY_MAX_CONTEXT", "0"))
        run_gateway(args)
    else:
        args.effort = getattr(args, "effort", DEFAULT_EFFORT)
        run_cli(args)


if __name__ == "__main__":
    main()