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

from inventory.views import MaterialListView  # بالای فایل اضافه کن (کنار importهای دیگر)
from inventory.views import MaterialListView, ToolListView
from inventory.views import MaterialListView, ToolListView, EquipmentListView
from inventory.views import MaterialListView, ToolListView, EquipmentListView, StockReportView
from inventory.views import (
    MaterialListView, ToolListView, EquipmentListView,
    StockReportView, StockMovementListView, MovementSummaryView
)

urlpatterns = [
    path('admin/', admin.site.urls),

    # روت اپ core (خانه، سفارش‌ها، داشبورد و ...)
    path('', include(('core.urls', 'core'), namespace='core')),

    # روت مالی
    path('billing/', include(('billing.urls', 'billing'), namespace='billing')),
    
    path('inventory/', TemplateView.as_view(template_name="inventory/home.html"), name='inventory_home'),
    path('inventory/items/', TemplateView.as_view(template_name="inventory/items_home.html"), name='inventory_items_home'),
    path('inventory/items/materials/', MaterialListView.as_view(), name='inventory_materials_list'),
    path('inventory/items/consumables/', ToolListView.as_view(), name='inventory_tools_list'),
    path('inventory/items/equipment/', EquipmentListView.as_view(), name='inventory_equipment_list'),
    path('inventory/reports/stock/', StockReportView.as_view(), name='inventory_stock_report'),
    path('inventory/movements/', StockMovementListView.as_view(), name='inventory_movements'),
    path('inventory/reports/movements-summary/', MovementSummaryView.as_view(), name='inventory_movements_summary'),

    # Placeholder تنظیمات (همان چیزی که قبل داشتی)
    path('settings/', include(('settings_app.urls', 'settings_app'), namespace='settings_app')),
]

# DEBUG: سروِ مدیا
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)


