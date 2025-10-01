"""
URL configuration for labmanager_project project.
"""
from django.contrib import admin
from django.urls import include, path

# برای سرو مدیا در حالت توسعه (لوگوی آپلودی پروفایل)
from django.conf import settings
from django.conf.urls.static import static

# صفحات مینیمال تب‌ها (همه با همان اسکلت base_user_panel.html)
from django.views.generic import TemplateView

urlpatterns = [
    path('admin/', admin.site.urls),

    # --- تب‌ها: صفحات مینیمال برای جلوگیری از 404 ---
    path('dashboard/', TemplateView.as_view(template_name='base_user_panel.html'), name='dashboard'),
    # ⛔️ حذف شد: path('orders/', TemplateView.as_view(...)) تا مسیر واقعی core اعمال شود
    # ریشهٔ مالی: صفحهٔ مینیمال (زیرمسیرها را billing.urls مدیریت می‌کند)
    path('billing/',   TemplateView.as_view(template_name='base_user_panel.html'), name='billing_root'),
    path('settings/',  TemplateView.as_view(template_name='base_user_panel.html'), name='settings'),

    # --- اپ‌های پروژه ---
    path('', include('core.urls')),
    path('billing/', include('billing.urls')),
]

# فقط در DEBUG: سرو فایل‌های مدیا مثل لوگو (MEDIA_URL -> MEDIA_ROOT)
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)


