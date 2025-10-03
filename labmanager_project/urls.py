"""
URL configuration for labmanager_project project.
"""
from django.contrib import admin
from django.urls import include, path

# سرو فایل‌های مدیا در حالت توسعه
from django.conf import settings
from django.conf.urls.static import static

# فقط برای صفحهٔ Placeholder «تنظیمات»
from django.views.generic import TemplateView

urlpatterns = [
    path('admin/', admin.site.urls),

    # روت اپ core (خانه، سفارش‌ها، داشبورد و ...)
    path('', include(('core.urls', 'core'), namespace='core')),

    # روت مالی
    path('billing/', include(('billing.urls', 'billing'), namespace='billing')),

    # Placeholder تنظیمات (همان چیزی که قبل داشتی)
    path('settings/', include(('settings_app.urls', 'settings_app'), namespace='settings_app')),
]

# DEBUG: سروِ مدیا
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)


