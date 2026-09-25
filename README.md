# deepseek-gateway

OpenAI-совместимый API к **burngate** (DeepSeek V4.1 Flash или Gemini 3.8 Flash). Один файл, ноль зависимостей.

## Что это, простыми словами

**burngate** — это шлюз к разным провайдерам моделей с единой OpenAI-ручкой. У него есть модели **DeepSeek V4.1 Flash** и **Gemini 3.8 Flash** (а ещё Space Bunny Alpha и MiMo V2.6 Flash).

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
5. Гонит **всё с `reasoning_effort=max`**: и ответы, и служебные запросы (проба при старте, сжатие истории). Клиент переопределить не может — только через `BURNGATE_EFFORT`.
6. **Сам лечит сбои upstream**: ретраит стрим (до 4 попыток), при rate limit ждёт кулдаун, а если стрим совсем не завёлся — повторяет запрос обычным способом и отдаёт ответ клиенту как SSE. Для Gemini выкидывает `presence_penalty`/`frequency_penalty` — на них upstream падает (клиенты вроде джанитора шлют их всегда).

## Модели

| модель | алиасы | что за зверь |
|---|---|---|
| `deepseek/deepseek-v4.1-flash` | `deepseek`, `deepseek-v4.1-flash` | DeepSeek V4.1 Flash — по умолчанию |
| `google/gemini-3.8-flash` | `gemini`, `gemini-3.8-flash` | Gemini 3.8 Flash |
| `stealth/space-bunny-alpha` | `bunny`, `space-bunny` | Space Bunny Alpha |
| `xiaomi/mimo-v2.6-flash` | `mimo` | MiMo V2.6 Flash |

Можно писать короткий алиас — шлюз развернёт его в полный id. Клиент выбирает модель в каждом запросе полем `model` (`deepseek`, `gemini`, ...). Регистр, пробелы и подчёркивания не важны: `Gemini 3.8 Flash`, `gemini`, `GEMINI-3.8-FLASH` — всё указывает на одну модель.

Модель по умолчанию (когда клиент не указал модель) — deepseek. Как сменить:

- `BURNGATE_MODEL=gemini` в `.env`, или
- `python gateway.py serve --model gemini`.

## Установка

### Termux (Android)

```bash
pkg update -y
pkg install python git -y
git clone https://github.com/Fiopou/deepseek-gateway
cd deepseek-gateway
```

Просто запусти — при первом запуске он сам попросит ключ и запомнит его:

```bash
python gateway.py serve --port 8787           # спросит ключ один раз, потом поднимет шлюз
```

Вставь ключ `gk_...` (спрячется при вводе) — он сохранится в `.env` рядом со скриптом, и больше спрашивать не будет.

Другие команды:

```bash
python gateway.py models                      # список моделей
python gateway.py chat "привет" --stream      # чат прямо в терминале
```

> Если ключ нужно ввести заново (например, сменил) — удали строку `BURNGATE_API_KEY` из `.env` или отредактируй файл вручную: `nano .env`.

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
model:    deepseek/deepseek-v4.1-flash   # или gemini
```

Если клиент на другом устройстве — вместо `localhost` IP машины со шлюзом (Termux: `ip addr show wlan0`).

## Команды

| команда | что делает |
|---|---|
| `python gateway.py serve --port 8787` | запустить шлюз |
| `python gateway.py serve --model gemini` | шлюз с gemini по умолчанию |
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
| `BURNGATE_MODEL` | модель по умолчанию: `deepseek` (по умолч.) или `gemini`, либо полный id |
| `BURNGATE_EFFORT` | `reasoning_effort` для всех запросов шлюза — форсится поверх клиента (по умолч. `max`) |
| `GATEWAY_PORT` | порт шлюза (по умолч. 8787) |
| `GATEWAY_TOKEN` | пароль шлюза (Bearer) |
| `GATEWAY_MAX_CONTEXT` | порог сжатия истории, символов (0 = выключить) |
| `GATEWAY_RATE_COOLDOWN` | пауза на модель после rate limit burngate, сек (по умолч. `20`, 0 = выключить) |
| `GATEWAY_PROBE=0` | отключить пробный запрос при старте |
| `GATEWAY_TIMEOUT` | таймаут запросов, сек (по умолч. 300) |

`reasoning_effort`: `max`, `xhigh`, `high`, `medium`, `low`, `minimal`, `none`.

## Частые проблемы

| проблема | решение |
|---|---|
| `Ключ burngate не принят` | проверь `BURNGATE_API_KEY` в `.env` рядом с `gateway.py` |
| `Rate limit burngate` | шлюз сам ставит паузу 20с на модель и повторяет; если часто — подожди или добавь второй ключ через `BURNGATE_API_KEYS` |
| `Failed to fetch` из браузерного клиента | CORS включён — проверь, что URL без `/chat/completions` на конце |
| ответ не по-русски / крякозябры | клиент шлёт не UTF-8; это на стороне клиента |

## Безопасность

- Ключ burngate хранится только в `.env`; `.env` добавлен в `.gitignore` — в репозиторий не попадает.
- Шлюз по умолчанию открыт для всей сети; если нужно — задай `GATEWAY_TOKEN`.
- Не коммить `.env` и не вставляй ключи в код.

## Лицензия

MIT. Делай что хочешь.