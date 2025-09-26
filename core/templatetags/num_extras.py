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

# --- Jalali helpers (نمایش تاریخ‌ها به شمسی با اسلش و ارقام فارسی) ---
try:
    import jdatetime
except ImportError:
    jdatetime = None

from datetime import datetime, date as _date

def _to_jdatetime(val):
    """datetime/date میلادی → jdatetime (برای نمایش شمسی)."""
    if val is None or jdatetime is None:
        return None
    if isinstance(val, datetime):
        return jdatetime.datetime.fromgregorian(datetime=val)
    if isinstance(val, _date):
        return jdatetime.date.fromgregorian(date=val)
    return None

@register.filter(name="jalali_date")
def jalali_date(value):
    """YYYY/MM/DD با ارقام فارسی (در نبود jdatetime هم خروجی می‌دهد)"""
    jd = _to_jdatetime(value)
    if jd:
        s = jd.strftime("%Y/%m/%d")
        return s.translate(FA_DIGITS)
    # fallback: اگر مقدار رشته/تاریخ خام بود
    if value is None:
        return ""
    s = str(value).strip()
    # 1404-07-05 → 1404/07/05
    s = s.replace("-", "/")
    return s.translate(FA_DIGITS)


@register.filter(name="jalali_datetime")
def jalali_datetime(value):
    """YYYY/MM/DD HH:mm با ارقام فارسی (اگر time نداشت، فقط تاریخ)"""
    jd = _to_jdatetime(value)
    if not jd:
        return ""
    try:
        s = jd.strftime("%Y/%m/%d %H:%M")
    except Exception:
        s = jd.strftime("%Y/%m/%d")
    return s.translate(FA_DIGITS)

@register.filter(name="int_fa")
def int_fa(value):
    """عدد صحیح با جداکنندهٔ هزار فارسی و ارقام فارسی"""
    try:
        n = int(Decimal(_normalize_num(value)))
        # جداکنندهٔ هزار فارسی (٬) + ارقام فارسی
        return f"{n:,}".replace(",", "٬").translate(FA_DIGITS)
    except Exception:
        return digits_fa(value)




