"""
Django settings for labmanager_project project.
"""

import os
from pathlib import Path

# ----------------- مسیر پایه پروژه -----------------
BASE_DIR = Path(__file__).resolve().parent.parent

# ----------------- امنیت -----------------
SECRET_KEY = 'your-secret-key-here'
DEBUG = True
ALLOWED_HOSTS = []

# ----------------- اپ‌ها -----------------
INSTALLED_APPS = [
    # دقت: django_jalali باید قبل از اپ‌های خودمان قرار گیرد
    'jalali_date',

    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.humanize',
    'settings_app',

    # اپ‌های شما
    'core',
    'billing.apps.BillingConfig',
    'inventory',

    # پکیج‌های خارجی
    'crispy_forms',
    'crispy_bootstrap5',
]

# ----------------- Middleware -----------------
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

# ----------------- URL -----------------
ROOT_URLCONF = 'labmanager_project.urls'

# ----------------- Templates -----------------
TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        # قالب‌های سراسری پروژه
        'DIRS': [
    os.path.join(BASE_DIR, 'templates'),
    os.path.join(BASE_DIR, 'core', 'templates'),  # ← اضافه شد تا قالب‌های core قطعاً دیده شوند
],
        # قالب‌های داخل اپ‌ها (core/templates/core/ و ...)
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',  # مهم برای crispy
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                # --- افزوده‌شده‌ها برای نمایش لوگو و دسترسی به static/media در قالب ---
                'django.template.context_processors.static',
                'django.template.context_processors.media',
            ],
        },
    },
]

# ----------------- WSGI -----------------
WSGI_APPLICATION = 'labmanager_project.wsgi.application'

# ----------------- Database -----------------
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': os.path.join(BASE_DIR, 'db.sqlite3'),
    }
}

# ----------------- Password validation -----------------
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',},
]

# ----------------- Localization -----------------
LANGUAGE_CODE = 'fa-ir'
TIME_ZONE = 'Asia/Tehran'
USE_I18N = True
USE_TZ = True

# ----------------- Static & Media -----------------
STATIC_URL = '/static/'
STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles')
STATICFILES_DIRS = [os.path.join(BASE_DIR, 'static')]

MEDIA_URL = '/media/'
MEDIA_ROOT = os.path.join(BASE_DIR, 'media')

# ----------------- django-jalali -----------------
JALALI_SETTINGS = {
    "ADMIN_JS_STATIC_FILES": [
        "admin/jquery.ui.datepicker.jalali/scripts/jquery-1.10.2.min.js",
        "admin/jquery.ui.datepicker.jalali/scripts/jquery.ui.core.js",
        "admin/jquery.ui.datepicker.jalali/scripts/jquery.ui.datepicker-cc.js",
        "admin/jquery.ui.datepicker.jalali/scripts/calendar.js",
        "admin/jquery.ui.datepicker.jalali/scripts/jquery.ui.datepicker-cc-fa.js",
        "admin/main.js",
    ],
    "ADMIN_CSS_STATIC_FILES": {
        "all": [
            "admin/jquery.ui.datepicker.jalali/themes/base/jquery-ui.min.css",
            "admin/css/main.css",
        ]
    }
}

# ----------------- Crispy Forms -----------------
CRISPY_ALLOWED_TEMPLATE_PACKS = "bootstrap5"
CRISPY_TEMPLATE_PACK = "bootstrap5"

# ----------------- Default primary key -----------------
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# اجازهٔ نمایش صفحات داخل iframe وقتی از همان دامنه هستند
X_FRAME_OPTIONS = 'SAMEORIGIN'
LOGIN_URL = '/admin/login/'











