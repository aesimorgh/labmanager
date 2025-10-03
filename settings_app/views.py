from django.shortcuts import render, redirect

def home(request):
    # فعلاً یک صفحهٔ ساده؛ بعداً تب‌ها و فرم‌ها را اضافه می‌کنیم
    return render(request, 'settings_app/home.html', {})

# Create your views here.
