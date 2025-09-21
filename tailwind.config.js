/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    './core/templates/**/*.html',   // همه قالب‌های Django
    './templates/**/*.html',        // اگر قالب‌های عمومی دارید
    './core/static/js/**/*.js',     // کلاس‌های داخل JS
  ],
  theme: {
    extend: {
      colors: {
        primary: '#3b82f6',    // آبی
        secondary: '#10b981',  // سبز
        danger: '#ef4444',     // قرمز
        warning: '#facc15',    // زرد
        info: '#0ea5e9',       // آبی روشن
        grayLight: '#f3f4f6',  // خاکستری روشن برای کارت و جدول
      },
      fontFamily: {
        sans: ['Vazirmatn', 'sans-serif'], // فونت فارسی
      },
    },
  },
  plugins: [
    require('@tailwindcss/forms'),       // استایل فرم‌ها
    require('@tailwindcss/typography'),  // استایل متون
    require('@tailwindcss/aspect-ratio'),// نسبت‌ها
  ],
}




