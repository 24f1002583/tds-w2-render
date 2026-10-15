# ==========================================================
# CONFIG - every value assigned to YOU on the GA2 page lives here.
# If the portal ever shows different values, change them HERE only.
# ==========================================================
import os

EMAIL = "24f1002583@ds.study.iitm.ac.in"

# Origin(s) of the exam page itself (needed when the browser runs the check).
# VERIFY: open the exam page, press F12 -> Console, type  location.origin
# and make sure the result is in this list. Add more with commas via the
# EXAM_ORIGINS environment variable on Render if needed.
EXAM_ORIGINS = [
    o.strip()
    for o in os.getenv("EXAM_ORIGINS", "https://exam.sanand.workers.dev").split(",")
    if o.strip()
]

# ---------------- Q3: CORS-aware metrics API ----------------
Q3_ALLOWED_ORIGIN = "https://dash-6w829y.example.com"

# ---------------- Q4: OAuth / OIDC token verification ----------------
ISSUER = "https://idp.exam.local"
AUDIENCE = "tds-gaaq8xyt.apps.exam.local"
PUBLIC_KEY_PEM = """-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA2okOHspNjgA+2rTLbeuY
cxiP/hG8C6Sb9iwg3yiLAA4HCnpITcbWCSelbvbYGuc3EbNy4xFyf5Cbj5DHJMID
EkryOgyd2giIIIBOUBj8S63uGcnRpOBh9NFatfNwheKuzsPuVNldu6A9cNteNpXc
WyJjG2axVfmq7i6SuKr1JoWYG7xTTAvKPujSl4OtsQfO3h5NepzdfXpr28oNnzfW
ed+zclR6BcmNNo/WVfJ4xyCLSf0BCOgdTgW6PdaChd1l9VDetJZVEgC5tkyvXsfI
SI6iyrYbKR0NEBSqq4XkadEjsCs4F1RncsS4LlgniT7GlkL9Mce3b0wGLs9/7ZIX
dQIDAQAB
-----END PUBLIC KEY-----"""

# ---------------- Q5: 12-factor config layers (low -> high precedence) ----
# Copied from the question. Real apps read these from files / the OS;
# here they are written out so the merge logic is easy to see and test.
LAYER_DEFAULTS = {
    "port": 8000,
    "workers": 1,
    "debug": False,
    "log_level": "info",
    "api_key": "default-secret-000",
}
LAYER_YAML = {  # config.development.yaml
    "debug": True,
    "log_level": "warning",
    "api_key": "key-d0g5d3k57s",
}
LAYER_DOTENV = {  # .env file
    "APP_PORT": "8594",
    "APP_LOG_LEVEL": "info",
}
LAYER_OS_ENV = {  # OS environment variables
    "APP_PORT": "8083",
    "APP_LOG_LEVEL": "error",
    "APP_API_KEY": "key-h9lfscr78o",
}

# ---------------- Q6: Redis ----------------
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# ---------------- Q7: POST analytics ----------------
Q7_API_KEY = "ak_jlbi6gvjh0xm259bt1x021nm"

# ---------------- Q11: idempotency + pagination + rate limit ----------
Q11_TOTAL_ORDERS = 54
Q11_RATE_LIMIT = 19  # requests per 10 seconds per X-Client-Id

# ---------------- Q12: middleware stack ----------------
Q12_ALLOWED_ORIGIN = "https://app-m4r5j4.example.com"
Q12_RATE_LIMIT = 8  # requests per 10 seconds per X-Client-Id

RATE_WINDOW_SECONDS = 10
