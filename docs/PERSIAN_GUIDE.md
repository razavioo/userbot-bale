# راهنمای استفاده و اتصال به زبان فارسی (Persian Documentation)

> **توجه مهم درباره زبان مستندات:**  
> فایلهای اصلی پروژه، کدهای منبع، کامیتها و مستندات رسمی پروژه برای سازگاری بینالمللی کاملاً به زبان انگلیسی نگهداری میشوند. این راهنما به عنوان مرجع کمکی برای کاربران و توسعهدهندگان فارسیزبان تهیه شده است.

---

## ⚠️ سلب مسئولیت حقوقی و اخلاقی (Disclaimer)

> **لطفاً پیش از استفاده از این نرمافزار این بخش را با دقت مطالعه نمایید.**

۱. **صرفاً برای استفاده شخصی، پژوهشی و آموزشی:** پروژه `userbot-bale` یک ابزار آزمایشی و تحقیقاتی در حوزه سیستمهای توزیعشده، پروتکلهای ارتباطی و اتصال ایجنتهای هوش مصنوعی (MCP) است. این نرمافزار صرفاً برای مصارف شخصی توسعه داده شده است.  
۲. **عدم وابستگی رسمی:** این مخزن و توسعهدهندگان آن هیچگونه ارتباط، وابستگی، نمایندگی یا تأیید رسمی از جانب **پیامرسان بله** (`bale.ai`)، شرکتهای وابسته یا مالکین حقوقی آن ندارند.  
۳. **مسئولیت کامل کاربر نهایی:** هرگونه استفاده نادرست، اسپم، ارسال انبوه، استخراج غیرمجاز داده، ایجاد مزاحمت، دور زدن محدودیتها یا نقض قوانین جاری کشور و شرایط استفاده از خدمات پیامرسان بله (Terms of Service)، **تماماً و منحصراً بر عهده شخص کاربر استفادهکننده است**. توسعهدهندگان این پروژه هیچگونه مسئولیت حقوقی، کیفری یا مالی در قبال عواقب استفاده کاربران نخواهند داشت.  
۴. **عدم ارائه گارانتی (AS IS):** این نرمافزار به صورت «همانگونه که هست» ارائه شده و هیچگونه تضمین کارکرد دائمی یا پایداری در برابر تغییرات سرور بله ندارد.

---

## 🌟 قابلیتهای اصلی

- **یوزربات ماندگار (Durable Userbot):** دریافت و ارسال پیام با ثبت در دیتابیس لوکال SQLite، جلوگیری از پیامهای تکراری، و مدیریت سقف نرخ ارسال (حداکثر ۲۰ پیام در دقیقه به هر مخاطب).
- **سیستم دستورات و پترنها (`CommandDispatcher`):** امکان تعریف آسان دستورات ربات (مانند `help/` یا `ping/`) و الگوهای Regex برای پاسخدهی خودکار.
- **سرور هوش مصنوعی استاندارد (MCP - Model Context Protocol):** اتصال مستقیم به کلاینتهای هوش مصنوعی (مثل Claude Desktop یا Cursor) جهت مشاهده پیامها، چتها و ارسال پیام در چتهای تأییدشده.
- **لیست مجاز مخاطبان (Allowlist):** ربات به صورت پیشفرض به هیچ کاربری که در لیست مجاز تعریف نشده باشد پیام نمیدهد تا از ارسالهای ناخواسته جلوگیری شود.
- **دستورات کامل ترمینال (CLI):** انجام تمام عملیات بدون نیاز به کدنویسی از طریق ترمینال.

---

## 🚀 راهنمای نصب و راهاندازی سریع

### ۱. نصب پیشنیازها

پروژه به **پایتون ۳.۱۰ یا بالاتر** نیاز دارد:

```bash
# کلون کردن پروژه
git clone https://github.com/razavioo/userbot-bale.git
cd userbot-bale

# ساخت و فعالسازی محیط مجازی پایتون
python3 -m venv .venv
source .venv/bin/activate

# نصب پکیج و وابستگیها
pip install -e ".[bale,mcp,dev]"
```

---

### ۲. ورود به حساب کاربری (Authentication)

برای اتصال ربات به حساب بله خود میتوانید از یکی از دو روش زیر استفاده کنید:

#### روش اول: ورود از طریق شماره تلفن (توصیه شده)
```bash
userbot-bale auth bale-login --phone +98912xxxxxxx --save --no-print-jwt
```
کد پیامکشده را وارد میکنید و نشست شما در مخزن امن سیستم ذخیره میشود.

#### روش دوم: وارد کردن توکن JWT از قبل ذخیرهشده
```bash
userbot-bale auth login --jwt-file /path/to/token.jwt
```

برای بررسی وضعیت اتصال:
```bash
userbot-bale auth status
```

---

### ۳. تعریف مخاطبان مجاز (Allowlist)

جهت حفظ امنیت حساب، پیامرسانی خودکار فقط به مخاطبانی که به لیست مجاز اضافه شده باشند مجاز است. شناسه مخاطب باید شناسه عددی (Numeric User ID) در بله باشد:

```bash
# افزودن یک مخاطب به لیست مجاز
userbot-bale userbot allow-peer 123456789

# مشاهده لیست افراد مجاز
userbot-bale userbot peers

# حذف یک مخاطب از لیست مجاز
userbot-bale userbot disallow-peer 123456789
```

---

### ۴. دستورات کاربری در ترمینال (CLI)

```bash
# نمایش مشخصات حساب جاری
userbot-bale userbot whoami

# نمایش وضعیت یوزربات و افراد مجاز
userbot-bale userbot status

# ارسال پیام متنی به مخاطب مجاز
userbot-bale userbot send 123456789 "سلام، پیام از طریق یوزربات"

# مشاهده لیست گفتگوهای اخیر
userbot-bale userbot dialogs --limit 10

# مشاهده پیامهای اخیر یک چت
userbot-bale userbot messages 123456789 --limit 20

# جستجوی متن در تاریخچه پیامهای محلی
userbot-bale userbot search "سفارش" --peer-id 123456789

# علامتگذاری پیامها به عنوان خواندهشده تا یک زمان مشخص
userbot-bale userbot mark-read 123456789 1789370000000

# جستجوی مخاطبین در دفترچه تلفن و دایرکتوری بله
userbot-bale userbot search-contacts "پشتیبانی"

# تبدیل شماره تلفن به شناسه عددی کاربری
userbot-bale userbot resolve-phone "+98912xxxxxxx"
```

---

### ۵. اجرای سرویس یوزربات

برای اجرای یوزربات در پسزمینه و دریافت رویدادها:

```bash
# اجرای رسیور جهت دریافت و ذخیره لاگ پیامها
userbot-bale userbot run

# اجرا با فعال بودن پلاگین پاسخگوی خودکار (اکو فقط به افراد مجاز)
userbot-bale userbot run --echo
```

---

### ۶. اتصال به دستیارهای هوش مصنوعی (MCP)

اگر از ابزارهایی مثل **Claude Desktop** یا **Cursor** استفاده میکنید، کافیست در تنظیمات سرورهای MCP مسیر باینری پروژه را معرفی کنید:

```json
{
  "mcpServers": {
    "userbot-bale": {
      "command": "/path/to/userbot-bale/.venv/bin/userbot-bale",
      "args": ["mcp", "serve"]
    }
  }
}
```

یا اجرای دستی در ترمینال:
```bash
userbot-bale mcp serve
```

ابزارهای در دسترس مدل هوش مصنوعی:
- `account_status`: وضعیت لاگین و توکن و لیست مجاز
- `list_dialogs`: دریافت لیست چتها
- `list_messages`: خواندن پیامهای یک چت مجاز
- `search_messages`: جستجو در متن پیامها
- `send_text`: ارسال پاسخ به مخاطب مجاز
- `mark_read`: تیک خواندهشدن پیامها
- `search_contacts` و `resolve_phone`: جستجوی مخاطب و تبدیل شماره به آیدی

---

### ۷. توسعه و برنامهنویسی با پایتون

```python
import threading
from userbot_bale.userbot import (
    BaleUserClient,
    CommandDispatcher,
    UserbotRuntime,
    UserbotStore,
)

# ۱. راهاندازی کلاینت و لیست مجاز
store = UserbotStore()
store.allow_peer(123456789)

client = BaleUserClient(jwt="YOUR_JWT_TOKEN", store=store)

# ۲. ایجاد سیستم توزیع دستورات
dispatcher = CommandDispatcher(prefix="/")

@dispatcher.command("help")
def handle_help(event, bot, args):
    bot.send_text(event.peer_id, "دستورات فعال: /help و /ping")

@dispatcher.command("ping")
def handle_ping(event, bot, args):
    bot.send_text(event.peer_id, "pong!")

@dispatcher.regex(r"^ساعت$")
def handle_time(event, bot, match):
    bot.send_text(event.peer_id, "پاسخ خودکار ساعت")

# ۳. اجرای رانتایم
runtime = UserbotRuntime(client, plugins=[dispatcher])
runtime.start()

try:
    threading.Event().wait()
finally:
    runtime.stop()
```
