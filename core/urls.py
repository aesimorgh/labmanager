# core/urls.py
from django.urls import path
from . import views, views_wages

app_name = 'core'

urlpatterns = [
    # ریشه / تب سفارش‌ها
    path('', views.home, name='home'),
    path('orders/', views.home, name='orders_home'),

    # درگاه انتقال (فقط از ویو؛ نسخه‌ی TemplateView حذف شد تا تداخل نداشته باشد)
    path('transfer/', views.transfer_gate, name='transfer_gate'),

    # گزارش مالی/حسابداری
    path('accounting/', views.accounting_report, name='accounting_report'),

    # جزئیات و اقدامات سفارش
    path('orders/<int:order_id>/', views.order_detail, name='order_detail'),
    path('orders/<int:order_id>/add-event/', views.add_order_event, name='add_order_event'),
    path('orders/<int:order_id>/deliver/', views.deliver_order, name='deliver_order'),
    path('orders/<int:order_id>/edit/', views.order_edit, name='order_edit'),

    # لاب دیجیتال
    path('digital-lab/new/', views.digital_lab_transfer_create, name='digital_lab_new_global'),
    path('orders/<int:order_id>/digital-lab/new/', views.digital_lab_transfer_create, name='digital_lab_new'),
    path('digital-lab/list/', views.digital_lab_transfer_list, name='digital_lab_list'),
    path('digital-lab/report/', views.digital_lab_report, name='digital_lab_report'),

    # داشبورد/ابزارها
    path('dashboard/', views.dashboard, name='dashboard'),
    path('workbench/', views.workbench, name='workbench'),
    path('station/', views.station_panel, name='station_panel'),

    # مراحل (اکشن‌های تکی و گروهی)
    path('stages/<int:stage_id>/start/',  views.stage_start_now,   name='stage_start_now'),
    path('stages/<int:stage_id>/done/',   views.stage_done_today,  name='stage_done_today'),
    path('stages/bulk/done/',             views.stage_bulk_done_today,  name='stage_bulk_done_today'),
    path('stages/bulk/start/',            views.stage_bulk_start_today, name='stage_bulk_start_today'),
    path('stages/bulk/plan/',             views.stage_bulk_plan_date,   name='stage_bulk_plan_date'),
    path('stages/bulk/claim/',            views.stage_bulk_claim,        name='stage_bulk_claim'),

    # APIs (بدون تکرار)
    path('api/doctors',            views.api_doctors,          name='api_doctors'),
    path('api/orders-by-doctor',   views.api_orders_by_doctor, name='api_orders_by_doctor'),
    path('api/order-stages',       views.api_order_stages,     name='api_order_stages'),
    path('api/products',           views.api_products,         name='api_products'),

    # رویداد گروهی (یک‌بار)
    path('orders/bulk-add-event/', views.add_order_event_bulk, name='add_order_event_bulk'),

    # Wages / Workbench
    path('wages/workbench/<int:order_id>/',  views_wages.workbench_order,  name='core_workbench_order'),
    path('wages/worklog/new/',               views_wages.worklog_create,   name='core_worklog_create'),
    path('wages/worklog/<int:pk>/delete/',   views_wages.worklog_delete,   name='core_worklog_delete'),
    
     # --- Wages / Payouts (تسویه دستمزد) ---
    path('wages/payout/new/',                views_wages.wages_payout_new,     name='wages_payout_new'),
    path('wages/payout/preview/',            views_wages.wages_payout_preview, name='wages_payout_preview'),
    path('wages/payout/<int:payout_id>/',    views_wages.wages_payout_detail,  name='wages_payout_detail'),
    
    # --- Wages / Reports ---
    path('wages/report/', views_wages.wages_report, name='wages_report'),
]
