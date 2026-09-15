# deepseek-gateway

OpenAI-совместимый API к **burngate** (`deepseek/deepseek-v4.1-flash`). Один файл, ноль зависимостей.

## Что это, простыми словами

**burngate** — это шлюз к разным провайдерам моделей с единой OpenAI-ручкой. У него есть модель **DeepSeek V4.1 Flash** и **GLM 5.3 Flash**.

**deepseek-gateway** — маленький сервер, который держит твой ключ burngate и раздаёт его дальше как обычный OpenAI API. На телефон, на комп, куда угодно. Для джанитора/таверны это просто `http://localhost:8787/v1`.

```
твой клиент (джанитор, код, чат)
   │  OpenAI-запрос (http://localhost:8787/v1)
   ▼
deepseek-gateway (этот скрипт)
   │  тот же запрос + Bearer <ключ из .env>
   ▼
burngate (https://burngate.space/api/v1)
```

Шлюз не переводит форматы — burngate и так умеет OpenAI-формат. Скрипт просто:

1. Принимает запросы на `:8787` (с CORS, чтобы работало из браузерных клиентов).
2. Подставляет твой ключ burngate и, если ключей несколько, перебирает их при `401`/`429`.
3. Разбирает потоковые ответы (SSE) обратно клиенту.
4. Умеет **бесконечный контекст**: когда история вырастает, сам сжимает старые сообщения в резюме.

## Модели

| модель | алиасы | что за зверь |
|---|---|---|
| `deepseek/deepseek-v4.1-flash` | `deepseek`, `deepseek-v4.1-flash` | DeepSeek V4.1 Flash — по умолчанию |
| `z-ai/glm-5.3-flash` | `glm`, `glm-5.3-flash` | GLM 5.3 Flash |

Можно писать короткий алиас — шлюз развернёт его в полный id.

## Установка

### Termux (Android)

```bash
pkg update -y
pkg install python git -y
git clone https://github.com/Fiopou/deepseek-gateway
cd deepseek-gateway
cp .env.example .env
```

Впиши ключ в `.env` (в `nano`: Ctrl+X → y → Enter):

```
BURNGATE_API_KEY=gk_твой_ключ
```

Проверь и запусти:

```bash
python gateway.py models                      # список моделей
python gateway.py serve --port 8787           # поднять шлюз
python gateway.py chat "привет" --stream      # чат прямо в терминале
```

Держи терминал открытым (или `tmux`), иначе шлюз умрёт.

### Windows

```bash
git clone https://github.com/Fiopou/deepseek-gateway
cd deepseek-gateway
python gateway.py serve --port 8787
```

## Подключение клиента (джанитор, таверна)

```
base_url: http://localhost:8787/v1
api_key:  любая строка (например burn)
model:    deepseek/deepseek-v4.1-flash
```

Если клиент на другом устройстве — вместо `localhost` IP машины со шлюзом (Termux: `ip addr show wlan0`).

## Команды

| команда | что делает |
|---|---|
| `python gateway.py serve --port 8787` | запустить шлюз |
| `python gateway.py chat` | чат в терминале (с авто-компактом) |
| `python gateway.py chat "промпт"` | разовый запрос |
| `python gateway.py chat "промпт" --stream` | стрим, мысли в stderr |
| `python gateway.py chat "промпт" --effort max` | задать reasoning_effort |
| `python gateway.py models` | список моделей burngate |

## Настройки окружения (и .env)

| переменная | зачем |
|---|---|
| `BURNGATE_API_KEY` | **ключ burngate** — обязателен (лежит в `.env`) |
| `BURNGATE_API_KEYS` | несколько ключей через запятую — перебор при ошибках |
| `BURNGATE_BASE` | базовый URL burngate (по умолч. `https://burngate.space/api/v1`) |
| `BURNGATE_EFFORT` | `reasoning_effort` по умолчанию (по умолч. `high`) |
| `GATEWAY_PORT` | порт шлюза (по умолч. 8787) |
| `GATEWAY_TOKEN` | пароль шлюза (Bearer) |
| `GATEWAY_MAX_CONTEXT` | порог сжатия истории, символов (0 = выключить) |
| `GATEWAY_PROBE=0` | отключить пробный запрос при старте |
| `GATEWAY_TIMEOUT` | таймаут запросов, сек (по умолч. 300) |

`reasoning_effort`: `max`, `xhigh`, `high`, `medium`, `low`, `minimal`, `none`.

## Частые проблемы

| проблема | решение |
|---|---|
| `Ключ burngate не принят` | проверь `BURNGATE_API_KEY` в `.env` рядом с `gateway.py` |
| `Rate limit burngate` | подожди или добавь второй ключ через `BURNGATE_API_KEYS` |
| `Failed to fetch` из браузерного клиента | CORS включён — проверь, что URL без `/chat/completions` на конце |
| ответ не по-русски / крякозябры | клиент шлёт не UTF-8; это на стороне клиента |

## Безопасность

- Ключ burngate хранится только в `.env`; `.env` добавлен в `.gitignore` — в репозиторий не попадает.
- Шлюз по умолчанию открыт для всей сети; если нужно — задай `GATEWAY_TOKEN`.
- Не коммить `.env` и не вставляй ключи в код.

## Лицензия

MIT. Делай что хочешь.