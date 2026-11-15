from .settings import *


DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}
COGNITO_ISSUER = "https://cognito-idp.us-east-1.amazonaws.com/test-pool"
COGNITO_APP_CLIENT_ID = "test-client"
INVOICE_BUCKET = ""