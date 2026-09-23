import os

from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent


def _positive_int_env(
    name    : str,
    default : int,
) -> int:
    value = int(
        os.getenv(
            name, str(default)
        )
    )

    if value < 1:
        raise ValueError(f"{name} must be an integer.")

    return value


SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "development-only-change-me")
DEBUG      = os.getenv("DJANGO_DEBUG"     , "true"                      ).lower() in {"1", "true", "yes", "on"}

ALLOWED_HOSTS = [
    host.strip()
    for host in os.getenv(
        "DJANGO_ALLOWED_HOSTS",
        "localhost,127.0.0.1" ,
    ).split(",")
    if  host.strip()
]

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.sessions"    ,
    "django.contrib.staticfiles" ,
    "games.apps.GamesConfig"     ,
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware"         ,
    "django.contrib.sessions.middleware.SessionMiddleware"  ,
    "django.middleware.common.CommonMiddleware"             ,
    "django.middleware.csrf.CsrfViewMiddleware"             ,
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

if not DEBUG:
    MIDDLEWARE.insert(1, "whitenoise.middleware.WhiteNoiseMiddleware")

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND"  : "django.template.backends.django.DjangoTemplates",
        "APP_DIRS" : True                                             ,
        "DIRS"     : [
            BASE_DIR / "templates",
        ],
        "OPTIONS"  : {
            "context_processors" : [
                "django.template.context_processors.request",
            ],
        },
    }
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DATABASE_PATH = Path(
    os.getenv(
        "DATABASE_PATH", str(BASE_DIR / "data" / "db.sqlite3")
    )
)

DATABASES = {
    "default" : {
        "ENGINE"  : "django.db.backends.sqlite3",
        "NAME"    : DATABASE_PATH               ,
        "OPTIONS" : {
            "timeout"          : 20         ,
            "transaction_mode" : "IMMEDIATE",
            "init_command"     : (
                "PRAGMA journal_mode=WAL; "
                "PRAGMA synchronous=NORMAL;"
            ),
        },
    }
}

LANGUAGE_CODE = "pt-br"
TIME_ZONE     = "America/Fortaleza"
USE_I18N      = True
USE_TZ        = True

STATIC_URL  = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "staticfiles" : {
        "BACKEND" : (
            "django.contrib.staticfiles.storage.StaticFilesStorage"
            if   DEBUG
            else "whitenoise.storage.CompressedManifestStaticFilesStorage"
        )
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE   = os.getenv("DJANGO_SECURE_COOKIES", "false").lower() in {
    "1"   ,
    "true",
    "yes" ,
    "on"  ,
}

SESSION_COOKIE_AGE          = 60 * 60 * 24 * 30
CSRF_COOKIE_SECURE          = SESSION_COOKIE_SECURE
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS             = "DENY"

DATA_UPLOAD_MAX_MEMORY_SIZE = 256 * 1024

SOLVER_SERVICE_URL       =       os.getenv("SOLVER_SERVICE_URL"      , "http://127.0.0.1:8001")
SOLVER_CONNECT_TIMEOUT   = float(os.getenv("SOLVER_CONNECT_TIMEOUT"  , "1"  ))
SOLVER_READ_TIMEOUT      = float(os.getenv("SOLVER_READ_TIMEOUT"     , "10" ))
SOLVER_TIME_LIMIT        = float(os.getenv("SOLVER_TIME_LIMIT"       , "5"  ))
SOLVER_BUSY_RETRIES      = int  (os.getenv("SOLVER_BUSY_RETRIES"     , "6"  ))
SOLVER_CLAIM_TTL_SECONDS = float(os.getenv("SOLVER_CLAIM_TTL_SECONDS", "600"))

if SOLVER_TIME_LIMIT        <= 0:
    raise ValueError("SOLVER_TIME_LIMIT must be a positive number.")
if SOLVER_CLAIM_TTL_SECONDS <= SOLVER_TIME_LIMIT + 2:
    raise ValueError(
        "SOLVER_CLAIM_TTL_SECONDS must exceed SOLVER_TIME_LIMIT by more than 2 seconds."
    )
if SOLVER_BUSY_RETRIES      <  0:
    raise ValueError("SOLVER_BUSY_RETRIES must be a non-negative integer.")

INSTANCE_GENERATION_MAX_ATTEMPTS = _positive_int_env(
    "INSTANCE_GENERATION_MAX_ATTEMPTS", 20,
)

LOGGING = {
    "version"                  : 1    ,
    "disable_existing_loggers" : False,
    "handlers"                 : {
        "console" : {
            "class" : "logging.StreamHandler",
        }
    },
    "root"                     : {
        "handlers" : ["console"]                   ,
        "level"    : os.getenv("LOG_LEVEL", "INFO"),
    },
}
