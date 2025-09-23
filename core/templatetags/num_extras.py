# core/templatetags/num_extras.py
from django import template
from decimal import Decimal, InvalidOperation

register = template.Library()

# نگاشت ارقام فارسی/عربی به انگلیسی + حذف جداکننده‌های قبلی
TRANS = str.maketrans({
    "۰":"0","۱":"1","۲":"2","۳":"3","۴":"4","۵":"5","۶":"6","۷":"7","۸":"8","۹":"9",
    "٠":"0","١":"1","٢":"2","٣":"3","٤":"4","٥":"5","٦":"6","٧":"7","٨":"8","٩":"9",
    ",":"", "٬":"", " " : ""
})

FA_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")

def _normalize_num(value) -> str:
    if value is None:
        return ""
    return str(value).strip().translate(TRANS)

@register.filter(name="commas")
def commas(value):
    s = _normalize_num(value)
    if s == "":
        return ""
    try:
        n_int = int(Decimal(s))
        return f"{n_int:,}"
    except (InvalidOperation, ValueError, TypeError):
        return value

@register.filter(name="commas_fa")
def commas_fa(value):
    res = commas(value)
    try:
        return str(res).replace(",", "٬")
    except Exception:
        return res

@register.filter(name="digits_fa")
def digits_fa(value):
    """فقط رقم‌های انگلیسی را به فارسی تبدیل می‌کند (بدون دست‌کاری جداکننده)."""
    if value is None:
        return ""
    return str(value).translate(FA_DIGITS)

@register.filter(name="money_fa")
def money_fa(value):
    """
    قالب نهاییِ نمایشی: جداکننده فارسی + رقم‌های فارسی.
    مثال: 1234567 -> ۱٬۲۳۴٬۵۶۷
    """
    return digits_fa(commas_fa(value))
@register.filter(name="dash_to_slash")
def dash_to_slash(value):
    """همه '-' ها را به '/' تبدیل می‌کند (برای تاریخ‌هایی مثل 1404-07-04)."""
    if value is None:
        return ""
    return str(value).replace("-", "/")




