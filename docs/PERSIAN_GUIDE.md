# راهنمای استفاده و اتصال به زبان فارسی (Persian Documentation)

> **توجه مهم درباره زبان مستندات:**  
> فایل‌های اصلی پروژه، کد‌های منبع، کامیت‌ها و مستندات رسمی پروژه برای سازگاری بین‌المللی کاملاً به زبان انگلیسی نگهداری می‌شوند. این راهنما به عنوان مرجع کمکی برای کاربران و توسعه‌دهندگان فارسی‌زبان تهیه شده است.

---

## ⚠️ سلب مسئولیت حقوقی و اخلاقی (Disclaimer)

> **لطفاً پیش از استفاده از این نرم‌افزار این بخش را با دقت مطالعه نمایید.**

۱. **صرفاً برای استفاده شخصی، پژوهشی و آموزشی:** پروژه `userbot-bale` یک ابزار آزمایشی و تحقیقاتی در حوزه سیستم‌های توزیع‌شده، پروتکل‌های ارتباطی و اتصال ایجنت‌های هوش مصنوعی (MCP) است. این نرم‌افزار صرفاً برای مصارف شخصی توسعه داده شده است.  
۲. **عدم وابستگی رسمی:** این مخزن و توسعه‌دهندگان آن هیچ‌گونه ارتباط، وابستگی، نمایندگی یا تأیید رسمی از جانب **پیام‌رسان بله** (`bale.ai`)، شرکت‌های وابسته یا مالکین حقوقی آن ندارند.  
۳. **مسئولیت کامل کاربر نهایی:** هرگونه استفاده نادرست، اسپم، ارسال انبوه، استخراج غیرمجاز داده، ایجاد مزاحمت، دور زدن محدودیت‌ها یا نقض قوانین جاری کشور و شرایط استفاده از خدمات پیام‌رسان بله (Terms of Service)، **تماماً و منحصراً بر عهده شخص کاربر استفاده‌کننده است**. توسعه‌دهندگان این پروژه هیچ‌گونه مسئولیت حقوقی، کیفری یا مالی در قبال عواقب استفاده کاربران نخواهند داشت.  
۴. **عدم ارائه گارانتی (AS IS):** این نرم‌افزار به صورت «همان‌گونه که هست» ارائه شده و هیچ‌گونه تضمین کارکرد دائمی یا پایداری در برابر تغییرات سرور بله ندارد.

---

## 🌟 قابلیت‌های اصلی

- **یوزربات ماندگار (Durable Userbot):** دریافت و ارسال پیام با ثبت در دیتابیس لوکال SQLite، جلوگیری از پیام‌های تکراری، و مدیریت سقف نرخ ارسال (حداکثر ۲۰ پیام در دقیقه به هر مخاطب).
- **سیستم دستورات و پترن‌ها (`CommandDispatcher`):** امکان تعریف آسان دستورات ربات (مانند `help/` یا `ping/`) و الگو‌های Regex برای پاسخ‌دهی خودکار.
- **سرور هوش مصنوعی استاندارد (MCP - Model Context Protocol):** اتصال مستقیم به کلاینت‌های هوش مصنوعی (مثل Claude Desktop یا Cursor) جهت مشاهده پیام‌ها، چتها و ارسال پیام در چت‌های تأیید‌شده.
- **لیست مجاز مخاطبان (Allowlist):** ربات به صورت پیش‌فرض به هیچ کاربری که در لیست مجاز تعریف نشده باشد پیام نمی‌دهد تا از ارسال‌های ناخواسته جلوگیری شود.
- **دستورات کامل ترمینال (CLI):** انجام تمام عملیات بدون نیاز به کدنویسی از طریق ترمینال.

---

## 🚀 راهنمای نصب و راه‌اندازی سریع

### ۱. نصب پیش‌نیازها

پروژه به **پایتون ۳.۱۰ یا بالاتر** نیاز دارد:

```bash
# کلون کردن پروژه
git clone https://github.com/razavioo/userbot-bale.git
cd userbot-bale

# ساخت و فعال‌سازی محیط مجازی پایتون
python3 -m venv .venv
source .venv/bin/activate

# نصب پکیج و وابستگی‌ها
pip install -e ".[bale,mcp,dev]"
```

---

### ۲. ورود به حساب کاربری (Authentication)

برای اتصال ربات به حساب بله خود می‌توانید از یکی از دو روش زیر استفاده کنید:

#### روش اول: ورود از طریق شماره تلفن (توصیه شده)
```bash
userbot-bale auth bale-login --phone +98912xxxxxxx --save --no-print-jwt
```
کد پیامک‌شده را وارد می‌کنید و نشست شما در مخزن امن سیستم ذخیره می‌شود.

#### روش دوم: وارد کردن توکن JWT از قبل ذخیره‌شده
```bash
userbot-bale auth login --jwt-file /path/to/token.jwt
```

برای بررسی وضعیت اتصال:
```bash
userbot-bale auth status
```

---

### ۳. تعریف مخاطبان مجاز (Allowlist)

جهت حفظ امنیت حساب، پیام‌رسانی خودکار فقط به مخاطبانی که به لیست مجاز اضافه شده باشند مجاز است. شناسه مخاطب باید شناسه عددی (Numeric User ID) در بله باشد:

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

# مشاهده لیست گفت‌و‌گوهای اخیر
userbot-bale userbot dialogs --limit 10

# مشاهده پیام‌های اخیر یک چت
userbot-bale userbot messages 123456789 --limit 20

# جستجوی متن در تاریخچه پیام‌های محلی
userbot-bale userbot search "سفارش" --peer-id 123456789

# جستجوی سمت‌سرور (bale.search.v1.Search)
userbot-bale userbot search-remote "فاکتور" --limit 10

# رسانه‌های اشتراکی یک چت مجاز (SharedMedia LoadMedia)
userbot-bale userbot shared-media 123456789 --limit 10

# فهرست مسیر‌های gRPC شناخته‌شده از inventory آفلاین APK
userbot-bale userbot rpc-paths --service messaging --query LoadHistory

# علامت‌گذاری پیام‌ها به عنوان خوانده‌شده تا یک زمان مشخص
userbot-bale userbot mark-read 123456789 1789370000000

# جستجوی مخاطبین در دفترچه تلفن و دایرکتوری بله
userbot-bale userbot search-contacts "پشتیبانی"

# تبدیل شماره تلفن به شناسه عددی کاربری
userbot-bale userbot resolve-phone "+98912xxxxxxx"
```

---

### ۵. اجرای سرویس یوزربات

برای اجرای یوزربات در پس‌زمینه و دریافت رویداد‌ها:

```bash
# اجرای رسیور جهت دریافت و ذخیره لاگ پیام‌ها
userbot-bale userbot run

# اجرا با فعال بودن پلاگین پاسخ‌گوی خودکار (اکو فقط به افراد مجاز)
userbot-bale userbot run --echo
```

---

### ۶. اتصال به دستیار‌های هوش مصنوعی (MCP)

پروژه `userbot-bale` یک سرور اختصاصی منطبق بر استاندارد **Model Context Protocol (MCP)** از طریق پروتکل `stdio` ارائه می‌دهد تا دستیار‌های هوش مصنوعی (مانند **Claude Desktop**، **opencode**، **Cursor** و **Windsurf**) بتوانند به شکل امن و استاندارد با پیام‌رسان بله تعامل داشته باشند.

#### پیش‌نیازها:
۱. احراز هویت اولیه: `userbot-bale auth bale-login --phone +98912xxxxxxx --save`
۲. تعریف لیست مجاز مخاطبان: `userbot-bale userbot allow-peer 123456789` (کلیه ابزار‌های ارسال یا خواندن خصوصی برای حفظ امنیت خارج از این لیست مسدود هستند).

#### نمونه‌های پیکربندی کلاینت‌های مختلف:

##### ۱. opencode (`~/.config/opencode/opencode.jsonc` یا `.opencode/opencode.json`):
```json
{
  "mcp": {
    "bale": {
      "type": "local",
      "command": ["userbot-bale", "mcp", "serve"],
      "enabled": true
    }
  }
}
```

##### ۲. Claude Desktop (`claude_desktop_config.json`):
```json
{
  "mcpServers": {
    "bale": {
      "command": "userbot-bale",
      "args": ["mcp", "serve"]
    }
  }
}
```

##### ۳. Cursor (`.cursor/mcp.json`):
```json
{
  "mcpServers": {
    "bale": {
      "command": "userbot-bale",
      "args": ["mcp", "serve"]
    }
  }
}
```

##### ۴. اجرای مستقیم با `uvx` (بدون نیاز به کلون دستی مخزن):
```json
{
  "mcpServers": {
    "bale": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/razavioo/userbot-bale.git", "userbot-bale", "mcp", "serve"]
    }
  }
}
```

یا تست مستقیم در ترمینال:
```bash
userbot-bale mcp serve
```

#### جدول ابزار‌های ارائه‌شده در MCP:

| نام ابزار | نوع دسترسی | توضیحات | پارامتر‌ها | خروجی ساختاریافته |
|:---|:---:|:---|:---|:---|
| `account_status` | 🔍 فقط‌خواندنی | وضعیت اتصال، شناسه عددی کاربر و مخاطبان مجاز | ندارد | `{state, user_id, expires_in, allowed_peers}` |
| `list_dialogs` | 🔍 فقط‌خواندنی | فهرست گفت‌و‌گوها و چت‌های اخیر | `limit` (اختیاری، پیش‌فرض: ۲۰) | `{dialogs, count}` |
| `list_messages` | 🔍 فقط‌خواندنی | دریافت تاریخچه پیام‌های یک مخاطب مجاز | `peer_id` (اجباری)، `limit` (اختیاری) | `{messages, count}` |
| `search_messages` | 🔍 فقط‌خواندنی | جست‌وجو در پیام‌های پایگاه‌داده محلی SQLite | `query` (اجباری)، `peer_id` (اختیاری)، `limit` | `{messages, count}` |
| `search_messages_remote` | 🔍 فقط‌خواندنی | جست‌وجوی مستقیم سمت سرور بله | `query` (اجباری)، `limit` (اختیاری) | `{messages, count, query}` |
| `list_shared_media` | 🔍 فقط‌خواندنی | دریافت رسانه‌ها و فایل‌های اشتراک‌گذاری‌شده چت | `peer_id` (اجباری)، `limit`، `content_type` | `{media, count, peer_id}` |
| `search_contacts` | 🔍 فقط‌خواندنی | جست‌وجوی مخاطبان در دفترچه تلفن | `query` (اجباری) | `{contacts, count}` |
| `resolve_phone` | 🔍 فقط‌خواندنی | تبدیل شماره تلفن به شناسه عددی بله | `phone` (اجباری) | `{phone, user_id, is_allowed}` |
| `list_rpc_paths` | 🔍 فقط‌خواندنی | مشاهده لیست اندپوینت‌های gRPC استخراج‌شده از APK | `service` (اختیاری)، `query` (اختیاری) | `{paths, count, total, service, query}` |
| `mark_read` | ✍️ اقدام | خوانده‌شدن پیام‌ها تا برچسب زمانی مشخص | `peer_id` (اجباری)، `date` (زمان میلی‌ثانیه) | `{ok, peer_id, date}` |
| `send_text` | 🛡️ دو‌مرحله‌ای | ارسال پیام متنی با تأیید امنیتی | `peer_id` (اجباری)، `text` (متن)، `confirm_token` | مرحله ۱: صدور توکن تأیید<br>مرحله ۲: ارسال قطعی پیام |

##### 🛡️ مکانیزم تأیید دو‌مرحله‌ای در `send_text`:
برای جلوگیری از ارسال تصادفی یا حلقه‌های تکراری هوش مصنوعی:
۱. **مرحلهٔ پیش‌نمایش و صدور توکن:** ایجنت ابتدا `send_text(peer_id=..., text=...)` را بدون توکن فراخوانی می‌کند. هیچ پیامی ارسال نمی‌شود و سرور توکن یک‌بار‌مصرف `confirm_token` با اعتبار ۵ دقیقه برمی‌گرداند.
۲. **مرحلهٔ ارسال نهایی:** مدل با دریافت تأیید یا اطمینان از صحت پیام، `send_text(peer_id=..., text=..., confirm_token=...)` را فراخوانی می‌کند و پیام به مخاطب تحویل داده می‌شود.

---

### ۷. توسعه و برنامه‌نویسی با پایتون (SDK)

#### شیوه مدرن و استاندارد Async (مشابه تلتون و پایروگرام)

این روش استانداردترین شیوه برای اتصال سایر پروژه‌ها و ساخت ربات‌های ناهم‌گام است:

```python
from userbot_bale import BaleClient, events, filters

# اتصال به بله (به صورت خودکار از سشن ذخیره‌شده استفاده می‌کند یا jwt را پاس دهید)
client = BaleClient(jwt="YOUR_JWT_TOKEN")

@client.on(filters.command("start"))
async def start_handler(event):
    await event.reply("سلام! ربات بله فعال است.")

@client.on(filters.command("ping"))
async def ping_handler(event):
    await event.reply("pong!")

@client.on(filters.regex(r"^سفارش #?(\d+)"))
async def order_handler(event):
    order_id = event.pattern_match.group(1)
    await event.reply(f"پیگیری سفارش: {order_id}")

@client.on(filters.text & filters.private)
async def private_msg_handler(event):
    await event.mark_read()

# اجرای ربات تا زمان متوقف‌شدن توسط کاربر
client.run()
```

همچنین می‌توانید در بدنه کد‌های ناهم‌گام (FastAPI / aiohttp / asyncio) استفاده کنید:

```python
async with client:
    await client.send_message(peer_id=123456789, text="اعلان از سرور")
```

#### شیوه کال‌بک‌های همگام (Synchronous)

```python
import threading
from userbot_bale import BaleUserClient, filters

client = BaleUserClient(jwt="YOUR_JWT_TOKEN", enforce_allowlist=False)

@client.on_message(filters.command("ping"))
def handle_ping(event):
    event.reply("pong!")

client.start()
try:
    threading.Event().wait()
finally:
    client.stop()
```

#### شیوه قدیمی و افزونه‌ها (`CommandDispatcher`)

```python
import threading
from userbot_bale.userbot import (
    BaleUserClient,
    CommandDispatcher,
    UserbotRuntime,
    UserbotStore,
)

# ۱. راه‌اندازی کلاینت و لیست مجاز
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
