import os
import secrets
from pathlib import Path

import dj_database_url


BASE_DIR = Path(__file__).resolve().parents[2]
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY") or secrets.token_urlsafe(64)
DEBUG = os.getenv("DJANGO_DEBUG", "false").lower() == "true"
ALLOWED_HOSTS = [
    host.strip()
    for host in os.getenv("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
    if host.strip()
]

INSTALLED_APPS = [
    "rest_framework",
    "cloudorder.orders.apps.OrdersConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
]

ROOT_URLCONF = "cloudorder.config.urls"
WSGI_APPLICATION = "cloudorder.config.wsgi.application"
ASGI_APPLICATION = "cloudorder.config.asgi.application"

DATABASE_URL = os.getenv("DATABASE_URL")
if DATABASE_URL:
    DATABASES = {
        "default": dj_database_url.parse(
            DATABASE_URL, conn_max_age=60, conn_health_checks=True
        )
    }
else:
    database_options = {}
    if os.getenv("DATABASE_SSL_REQUIRE", "false").lower() == "true":
        database_options["sslmode"] = "require"
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.getenv("DB_NAME", "cloudorder"),
            "USER": os.getenv("DB_USER", ""),
            "PASSWORD": os.getenv("DB_PASSWORD", ""),
            "HOST": os.getenv("DB_HOST", "localhost"),
            "PORT": os.getenv("DB_PORT", "5432"),
            "CONN_MAX_AGE": 60,
            "CONN_HEALTH_CHECKS": True,
            "OPTIONS": database_options,
        }
    }

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
TIME_ZONE = "UTC"
LANGUAGE_CODE = "en-us"
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "cloudorder.orders.api.authentication.CognitoJWTAuthentication"
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "UNAUTHENTICATED_USER": None,
}

COGNITO_USER_POOL_ID = os.getenv("COGNITO_USER_POOL_ID", "")
COGNITO_APP_CLIENT_ID = os.getenv("COGNITO_APP_CLIENT_ID", "")
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
COGNITO_ISSUER = os.getenv(
    "COGNITO_ISSUER",
    f"https://cognito-idp.{AWS_REGION}.amazonaws.com/{os.getenv('COGNITO_USER_POOL_ID', '')}",
)
AWS_ENDPOINT_URL = os.getenv("AWS_ENDPOINT_URL") or None
AWS_S3_ENDPOINT_URL = os.getenv("AWS_S3_ENDPOINT_URL") or None
DYNAMODB_ORDER_PROCESSING_TABLE = os.getenv(
    "DYNAMODB_ORDER_PROCESSING_TABLE", "OrderProcessing"
)
INVOICE_BUCKET = os.getenv("INVOICE_BUCKET", "")
EVENT_BUS_NAME = os.getenv("EVENT_BUS_NAME", "default")
ORDER_QUEUE_URL = os.getenv("ORDER_QUEUE_URL", "")
ORDER_STATE_MACHINE_ARN = os.getenv("ORDER_STATE_MACHINE_ARN", "")
SNS_TOPIC_ARN = os.getenv("SNS_TOPIC_ARN", "")