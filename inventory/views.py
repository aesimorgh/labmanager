from django.views.generic import ListView
from billing.models import MaterialItem  # چون مدل در اپ billing تعریف شده

class MaterialListView(ListView):
    model = MaterialItem
    template_name = "inventory/materials_list.html"
    context_object_name = "materials"

    def get_queryset(self):
        # فقط متریال‌های فعال
        return MaterialItem.objects.filter(is_active=True).order_by("name")

class ToolListView(ListView):
    model = MaterialItem
    template_name = "inventory/tools_list.html"
    context_object_name = "tools"

    def get_queryset(self):
        # فقط ابزارهای مصرفی فعال
        return MaterialItem.objects.filter(item_type='tool', is_active=True).order_by("name")

from billing.models import Equipment  # اگر بالای فایل نیست، اضافه کن

class EquipmentListView(ListView):
    model = Equipment
    template_name = "inventory/equipment_list.html"
    context_object_name = "equipment_list"

    def get_queryset(self):
        # فقط تجهیزات فعال (درصورت وجود فیلد is_active)
        qs = Equipment.objects.all().order_by("name")
        if hasattr(Equipment, "is_active"):
            qs = qs.filter(is_active=True)
        return qs

from django.db.models import Sum, F, Value, DecimalField, Case, When, ExpressionWrapper
from django.db.models.functions import Coalesce, Abs

class StockReportView(ListView):
    model = MaterialItem
    template_name = "inventory/stock_report.html"
    context_object_name = "items"

    def get_queryset(self):
        dec = DecimalField(max_digits=14, decimal_places=3)

        qs = (
            MaterialItem.objects.filter(is_active=True)
            .annotate(
                total_in=Coalesce(
                    Sum(
                        Case(
                            When(movements__movement_type__in=["purchase", "adjust_pos"], then=F("movements__qty")),
                            default=Value(0, output_field=dec),
                            output_field=dec,
                        )
                    ),
                    Value(0, output_field=dec),
                ),
                total_out=Coalesce(
                    Sum(
                        Case(
                            When(movements__movement_type__in=["issue", "adjust_neg"], then=Abs(F("movements__qty"))),
                            default=Value(0, output_field=dec),
                            output_field=dec,
                        )
                    ),
                    Value(0, output_field=dec),
                ),
            )
            .annotate(
                computed_stock_qty=ExpressionWrapper(F("total_in") - F("total_out"), output_field=dec)
            )
            .order_by("item_type", "name")
        )
        return qs


from billing.models import StockMovement  # اگر بالای فایل نیست، اضافه کن

class StockMovementListView(ListView):
    model = StockMovement
    template_name = "inventory/movements_list.html"
    context_object_name = "movements"

    def get_queryset(self):
        # فقط حرکت‌های ثبت‌شده، مرتب از جدید به قدیم
        return StockMovement.objects.select_related("item").order_by("-happened_at")


from billing.models import StockMovement
from django.db.models import Sum, F, Value, DecimalField, Case, When, Q
from django.db.models.functions import Coalesce, Abs

class MovementSummaryView(ListView):
    template_name = "inventory/movements_summary.html"
    context_object_name = "summary"

    def get_queryset(self):
        qs = (
            StockMovement.objects
            .values("item__name", "item__uom", "item__item_type")
            .annotate(
                total_in=Coalesce(
                    Sum("qty", filter=Q(qty__gt=0), output_field=DecimalField(max_digits=12, decimal_places=3)),
                    Value(0, output_field=DecimalField(max_digits=12, decimal_places=3))
                ),
                total_out_raw=Coalesce(
                    Sum("qty", filter=Q(qty__lt=0), output_field=DecimalField(max_digits=12, decimal_places=3)),
                    Value(0, output_field=DecimalField(max_digits=12, decimal_places=3))
                ),
            )
            .annotate(
                total_out=Abs(F("total_out_raw"), output_field=DecimalField(max_digits=12, decimal_places=3)),
                balance=F("total_in") + F("total_out_raw"),
            )
        )
        return qs