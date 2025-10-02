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

    # روت اپ core (خانه، سفارش‌ها، داشبوردِ خودت و ... از همین‌جا می‌آید)
    path('', include(('core.urls', 'core'), namespace='core')),

    # ✅ خیلی مهم: روت مالی باید به billing.urls وصل باشد (نه TemplateView)
    path('billing/', include(('billing.urls', 'billing'), namespace='billing')),

    # Placeholder برای تنظیمات (لازم داری حفظش کن)
    path('settings/', TemplateView.as_view(template_name='base_user_panel.html'), name='settings'),
]

# DEBUG: سروِ مدیا
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)


