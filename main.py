"""TDS GA2 - ONE FastAPI app that answers Q3, Q4, Q5, Q6, Q7, Q8, Q10, Q11, Q12.

Layout of this file
  1. setup (app, Redis, metrics, log buffer)
  2. helpers (CORS policy, rate limiter, config merge, invoice extraction)
  3. ONE middleware (request id, counter, rate limit, CORS, timing, logging)
  4. endpoints, in question order
"""
import base64
import hmac
import json
import math
import re
import time
import uuid
from collections import OrderedDict, defaultdict, deque
from datetime import datetime, timezone

import jwt
import redis
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, generate_latest
from pydantic import BaseModel

import config

# ----------------------------------------------------------------------
# 1. SETUP
# ----------------------------------------------------------------------
app = FastAPI(title="TDS GA2")
START = time.monotonic()

# Redis is only used by Q6. It runs inside the same container (see Dockerfile).
redis_client = redis.Redis.from_url(
    config.REDIS_URL,
    decode_responses=True,
    socket_connect_timeout=2,
    socket_timeout=2,
)

# Q8: Prometheus counter. The library exposes it as "http_requests_total".
http_requests_total = Counter("http_requests_total", "Total HTTP requests")

# Q8: last 500 structured log entries, served by /logs/tail
log_buffer: deque = deque(maxlen=500)

# Q11 / Q12: rate-limit buckets and idempotency memory (kept in this process)
rate_buckets: dict = defaultdict(deque)
idempotency_store: "OrderedDict[str, dict]" = OrderedDict()


# ----------------------------------------------------------------------
# 2. HELPERS
# ----------------------------------------------------------------------
def cors_origin(path: str, origin: str | None) -> str | None:
    """Return the value for Access-Control-Allow-Origin, or None for 'no header'."""
    if path == "/stats":  # Q3: strict - only the assigned origin (+ the exam page)
        allowed = {config.Q3_ALLOWED_ORIGIN, *config.EXAM_ORIGINS}
    elif path == "/ping":  # Q12: strict - only the assigned origin (+ the exam page)
        allowed = {config.Q12_ALLOWED_ORIGIN, *config.EXAM_ORIGINS}
    else:  # everything else: open to any page (Q5, Q7, Q11 need the exam page)
        return "*"
    return origin if origin in allowed else None


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def check_rate(prefix: str, client: str, limit: int) -> int:
    """Sliding window. Returns 0 if allowed, else seconds to wait (Retry-After)."""
    window = config.RATE_WINDOW_SECONDS
    now = time.monotonic()
    bucket = rate_buckets[(prefix, client)]
    while bucket and now - bucket[0] >= window:
        bucket.popleft()
    if len(bucket) >= limit:
        return max(1, math.ceil(window - (now - bucket[0])))
    bucket.append(now)
    if len(rate_buckets) > 20000:  # housekeeping: forget idle clients
        for k in [k for k, v in rate_buckets.items() if not v]:
            del rate_buckets[k]
    return 0


def rate_limit_response(request: Request, path: str) -> Response | None:
    if path == "/orders":  # Q11 - only counted when a client id is sent
        client = request.headers.get("X-Client-Id")
        if not client:
            return None
        prefix, limit = "q11", config.Q11_RATE_LIMIT
    elif path == "/ping":  # Q12 - no client id? fall back to the caller's IP
        client = request.headers.get("X-Client-Id") or f"ip:{client_ip(request)}"
        prefix, limit = "q12", config.Q12_RATE_LIMIT
    else:
        return None
    wait = check_rate(prefix, client, limit)
    if wait:
        return JSONResponse(
            {"detail": "Too Many Requests"},
            status_code=429,
            headers={"Retry-After": str(wait)},
        )
    return None


def preflight(path: str, origin: str | None, request: Request) -> Response:
    if origin is None:  # not a CORS preflight, just a plain OPTIONS
        return Response(status_code=204, headers={"Allow": "GET, POST, OPTIONS"})
    if cors_origin(path, origin) is None:  # rejected: no ACAO header at all
        return Response(status_code=403, content="CORS origin not allowed")
    return Response(
        status_code=204,
        headers={
            "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
            "Access-Control-Allow-Headers": request.headers.get(
                "Access-Control-Request-Headers", "Content-Type"
            ),
            "Access-Control-Max-Age": "600",
        },
    )


# ----------------------------------------------------------------------
# 3. THE MIDDLEWARE  (Q3 headers, Q8 counter + logs, Q11/Q12 limits + CORS)
# ----------------------------------------------------------------------
@app.middleware("http")
async def stack(request: Request, call_next):
    started = time.perf_counter()
    http_requests_total.inc()

    request_id = (request.headers.get("X-Request-ID") or str(uuid.uuid4()))[:128]
    request.state.request_id = request_id

    path = request.url.path.rstrip("/") or "/"
    origin = request.headers.get("Origin")

    response: Response | None = None
    if request.method == "OPTIONS":
        response = preflight(path, origin, request)
    else:
        response = rate_limit_response(request, path)

    if response is None:
        try:
            response = await call_next(request)
        except Exception as exc:  # never leak a stack trace
            print(json.dumps({"level": "ERROR", "error": repr(exc)}), flush=True)
            response = JSONResponse({"detail": "Internal Server Error"}, status_code=500)

    allow = cors_origin(path, origin)
    if allow:
        response.headers["Access-Control-Allow-Origin"] = allow
    response.headers["Vary"] = "Origin"
    response.headers["Access-Control-Expose-Headers"] = (
        "X-Request-ID, X-Process-Time, Retry-After"
    )
    response.headers["X-Request-ID"] = request_id
    duration = time.perf_counter() - started
    response.headers["X-Process-Time"] = f"{duration:.6f}"

    entry = {
        "level": "ERROR" if response.status_code >= 500
        else "WARNING" if response.status_code >= 400
        else "INFO",
        "ts": datetime.now(timezone.utc).isoformat(),
        "request_id": request_id,
        "method": request.method,
        "path": request.url.path,
        "status": response.status_code,
        "duration_ms": round(duration * 1000, 3),
    }
    log_buffer.append(entry)
    print(json.dumps(entry), flush=True)  # structured JSON log line on stdout
    return response


# ----------------------------------------------------------------------
# Q3  GET /stats?values=1,2,3
# ----------------------------------------------------------------------
@app.get("/stats")
async def stats(values: str = Query(...)):
    try:
        nums = [int(x) for x in values.split(",") if x.strip() != ""]
    except ValueError:
        raise HTTPException(422, "values must be comma-separated integers")
    if not nums:
        raise HTTPException(422, "values must not be empty")
    return {
        "email": config.EMAIL,
        "count": len(nums),
        "sum": sum(nums),
        "min": min(nums),
        "max": max(nums),
        "mean": sum(nums) / len(nums),
    }


# ----------------------------------------------------------------------
# Q4  POST /verify  (RS256 JWT)
# ----------------------------------------------------------------------
@app.post("/verify")
async def verify(request: Request):
    invalid = JSONResponse({"valid": False}, status_code=401)
    try:
        body = await request.json()
        token = body.get("token")
        if not isinstance(token, str) or not token:
            return invalid
        claims = jwt.decode(
            token,
            config.PUBLIC_KEY_PEM,
            algorithms=["RS256"],  # never accept anything else
            issuer=config.ISSUER,
            audience=config.AUDIENCE,
            options={"require": ["exp", "iss", "aud"]},
        )
    except Exception:  # bad JSON, bad signature, expired, wrong iss/aud ...
        return invalid
    return {
        "valid": True,
        "email": claims.get("email"),
        "sub": claims.get("sub"),
        "aud": claims.get("aud"),
    }


# ----------------------------------------------------------------------
# Q5  GET /effective-config?set=key=value&set=...
# ----------------------------------------------------------------------
def norm_key(key: str) -> str:
    key = key.strip()
    if key.upper().startswith("APP_"):
        key = key[4:]
    key = key.lower()
    return "workers" if key == "num_workers" else key  # alias


def coerce(key: str, value):
    if key in ("port", "workers"):
        return int(str(value).strip())
    if key == "debug":
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in ("true", "1", "yes", "on")
    return str(value)


@app.get("/effective-config")
async def effective_config(request: Request):
    cfg: dict = {}
    layers = (  # low -> high precedence
        config.LAYER_DEFAULTS,
        config.LAYER_YAML,
        config.LAYER_DOTENV,
        config.LAYER_OS_ENV,
    )
    for layer in layers:
        for raw_key, value in layer.items():
            key = norm_key(raw_key)
            cfg[key] = coerce(key, value)

    for item in request.query_params.getlist("set"):  # highest precedence
        if "=" not in item:
            raise HTTPException(422, "set must look like key=value")
        raw_key, value = item.split("=", 1)
        key = norm_key(raw_key)
        try:
            cfg[key] = coerce(key, value)
        except ValueError:
            raise HTTPException(422, f"bad value for {key}")

    cfg["api_key"] = "****"  # secrets are never returned
    return cfg


# ----------------------------------------------------------------------
# Q6  Redis counters   +   Q8 /healthz (same endpoint serves both)
# ----------------------------------------------------------------------
@app.post("/hit/{key}")
async def hit(key: str):
    try:
        count = redis_client.incr(f"hits:{key}")  # atomic
    except redis.RedisError:
        raise HTTPException(503, "redis unavailable")
    return {"key": key, "count": int(count)}


@app.get("/count/{key}")
async def count(key: str):
    try:
        value = redis_client.get(f"hits:{key}")
    except redis.RedisError:
        raise HTTPException(503, "redis unavailable")
    return {"key": key, "count": int(value) if value else 0}


@app.get("/healthz")
async def healthz():
    uptime = time.monotonic() - START
    try:
        redis_client.ping()  # a real ping, not a static answer
    except redis.RedisError:
        return JSONResponse(
            {"status": "error", "redis": "down", "uptime_s": uptime}, status_code=503
        )
    return {"status": "ok", "redis": "up", "uptime_s": uptime}


# ----------------------------------------------------------------------
# Q7  POST /analytics
# ----------------------------------------------------------------------
@app.post("/analytics")
async def analytics(request: Request):
    sent = request.headers.get("X-API-Key", "")
    if not hmac.compare_digest(sent.encode(), config.Q7_API_KEY.encode()):
        return JSONResponse({"detail": "Unauthorized"}, status_code=401)
    try:
        events = (await request.json())["events"]
        if not isinstance(events, list):
            raise ValueError
    except Exception:
        return JSONResponse({"detail": "body must be {\"events\": [...]}"}, status_code=400)

    users = set()
    revenue = 0.0
    per_user: dict = defaultdict(float)
    for event in events:
        if not isinstance(event, dict):
            continue
        user = event.get("user")
        if user is not None:
            users.add(user)
        try:
            amount = float(event.get("amount", 0))
        except (TypeError, ValueError):
            continue
        if amount > 0:
            revenue += amount
            if user is not None:
                per_user[user] += amount
    return {
        "email": config.EMAIL,
        "total_events": len(events),
        "unique_users": len(users),
        "revenue": revenue,
        "top_user": max(per_user, key=per_user.get) if per_user else None,
    }


# ----------------------------------------------------------------------
# Q8  /work, /metrics, /logs/tail
# ----------------------------------------------------------------------
@app.get("/work")
async def work(n: int = Query(1, ge=0, le=1_000_000)):
    total = 0
    for i in range(min(n, 100_000)):  # "K units of work"
        total += i
    return {"email": config.EMAIL, "done": n}


@app.get("/metrics")
async def metrics():
    return Response(content=generate_latest(), headers={"Content-Type": CONTENT_TYPE_LATEST})


@app.get("/logs/tail")
async def logs_tail(limit: int = Query(10, ge=0, le=500)):
    return list(log_buffer)[-limit:] if limit else []


# ----------------------------------------------------------------------
# Q10  POST /extract   (rule-based extraction, no LLM needed on Render)
# ----------------------------------------------------------------------
class Invoice(BaseModel):
    vendor: str = ""
    amount: float = 0.0
    currency: str = ""
    date: str = ""


NUM = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?"
CODES = "USD|EUR|GBP"
SYMBOLS = {"$": "USD", "\u20ac": "EUR", "\u00a3": "GBP"}
SUFFIX = (
    r"(?:Industries|Ltd\.?|Limited|Inc\.?|LLC|GmbH|Corp\.?|Corporation|Co\.?|Group|"
    r"Holdings|Technologies|Systems|Solutions|Labs|Partners|Enterprises|Services|"
    r"Trading|Associates)"
)
WORD = r"[A-Z][\w&'\-]*"
COMPANY_RE = re.compile(
    rf"{WORD}(?:\s+{WORD})*?\s+{SUFFIX}(?:\s+{SUFFIX})*(?=[\s,.;:)]|$)"
)
LABEL_RE = re.compile(
    r"(?:vendor|supplier|payee|seller|invoice from|billed by|issued by|payable to|"
    r"pay to|paid to|from)\s*[:\-]?\s+(.+?)"
    r"(?=\s*(?:[\n,;|]|\.\s|\.$|\s(?:invoice|total|amount|due|date|dated|currency|"
    r"payment|please|for|is|by|on)\b|$))",
    re.IGNORECASE,
)
TOKEN_RE = re.compile(r"\b[A-Z][A-Za-z0-9]*-[A-Za-z0-9]{3,}\b")
STOP = {"invoice", "total", "amount", "due", "date", "payment", "please", "currency", "dear", "thank"}


def find_vendor(text: str) -> str:
    m = COMPANY_RE.search(text)
    if m:
        return m.group(0).strip()
    m = LABEL_RE.search(text)
    if m and m.group(1).strip():
        return m.group(1).strip().rstrip(".")
    m = TOKEN_RE.search(text)
    if m:
        vendor = m.group(0)
        for word in re.findall(r"\s+([A-Z][\w&'\-.]*)", text[m.end():])[:3]:
            if word.lower().strip(".") in STOP:
                break
            vendor += " " + word
        return vendor.strip()
    return ""


def find_amount_and_currency(text: str) -> tuple[float, str]:
    candidates = []  # (score, value, currency)
    patterns = [
        (rf"\b({CODES})\s*({NUM})", "code_first"),
        (rf"({NUM})\s*({CODES})\b", "code_last"),
        (rf"([$\u20ac\u00a3])\s*({NUM})", "symbol"),
    ]
    for pattern, kind in patterns:
        for m in re.finditer(pattern, text):
            if kind == "code_first":
                cur, num = m.group(1), m.group(2)
            elif kind == "code_last":
                num, cur = m.group(1), m.group(2)
            else:
                cur, num = SYMBOLS[m.group(1)], m.group(2)
            label = text[max(0, m.start() - 25): m.start()].lower()
            score = 0
            if re.search(r"(?<!sub)total|amount due|balance|due|payable|pay", label):
                score = 2
            if re.search(r"subtotal|tax|vat|fee|discount|shipping|deposit", label):
                score = -1
            candidates.append((score, float(num.replace(",", "")), cur))
    if candidates:
        best = max(candidates, key=lambda c: (c[0], c[1]))
        return best[1], best[2]

    currency = ""
    m = re.search(CODES, text)
    if m:
        currency = m.group(0)
    m = re.search(rf"(?:total|amount|due|balance)\D{{0,20}}({NUM})", text, re.IGNORECASE)
    amount = float(m.group(1).replace(",", "")) if m else 0.0
    return amount, currency


def find_date(text: str) -> str:
    best = None  # (score, position, date)
    for m in re.finditer(r"\b(\d{4}-\d{2}-\d{2})\b", text):
        label = text[max(0, m.start() - 22): m.start()].lower()  # words right before the date
        if re.search(r"issue|invoice date|dated|created", label):
            score = -1
        elif re.search(r"due|pay|deadline|settle", label):
            score = 2
        else:
            score = 0
        key = (score, m.start())
        if best is None or key > best[0]:
            best = (key, m.group(1))
    return best[1] if best else ""


def extract_invoice(text: str) -> Invoice:
    text = text[:20000]
    try:
        amount, currency = find_amount_and_currency(text)
        return Invoice(
            vendor=find_vendor(text),
            amount=amount,
            currency=currency.upper(),
            date=find_date(text),
        )
    except Exception:
        return Invoice()


@app.post("/extract", response_model=Invoice)
async def extract(request: Request):
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(422, 'Body must be JSON like {"text": "..."}')
    text = body.get("text") if isinstance(body, dict) else None
    if not isinstance(text, str) or not text.strip():
        raise HTTPException(422, "text must be a non-empty string")
    return extract_invoice(text)


# ----------------------------------------------------------------------
# Q11  Orders: idempotent POST + cursor pagination (+ rate limit in middleware)
# ----------------------------------------------------------------------
@app.post("/orders")
async def create_order(request: Request):
    key = request.headers.get("Idempotency-Key")
    if key and key in idempotency_store:
        return JSONResponse(
            idempotency_store[key], status_code=200, headers={"Idempotent-Replay": "true"}
        )
    try:
        body = await request.json()
    except Exception:
        body = None
    order = {
        "id": str(uuid.uuid4()),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "data": body if isinstance(body, (dict, list)) else None,
    }
    if key:
        idempotency_store[key] = order
        if len(idempotency_store) > 10000:
            idempotency_store.popitem(last=False)
    return JSONResponse(order, status_code=201)


def encode_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(f"offset:{offset}".encode()).decode()


def decode_cursor(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        label, value = base64.urlsafe_b64decode(cursor.encode()).decode().split(":")
        if label != "offset" or int(value) < 0:
            raise ValueError
        return int(value)
    except Exception:
        raise HTTPException(400, "invalid cursor")


@app.get("/orders")
async def list_orders(limit: int = Query(10, ge=1, le=1000), cursor: str | None = None):
    total = config.Q11_TOTAL_ORDERS
    start = decode_cursor(cursor)
    end = min(start + limit, total)
    items = [{"id": i, "name": f"Order {i}"} for i in range(start + 1, end + 1)]
    return {"items": items, "next_cursor": encode_cursor(end) if end < total else None}


# ----------------------------------------------------------------------
# Q12  GET /ping
# ----------------------------------------------------------------------
@app.get("/ping")
async def ping(request: Request):
    return {"email": config.EMAIL, "request_id": request.state.request_id}


@app.get("/")
async def root():
    return {"status": "ok", "message": "TDS GA2 service is running"}
