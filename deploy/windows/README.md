# اجرای Production روی Windows Server

این راهنما برای سروری است که Caddy روی پورت‌های `80/443` نشسته و Backend گرایه باید فقط روی `127.0.0.1:8000` گوش بدهد.

مسیر نمونه روی سرور شما:

```
C:\Users\Administrator\Desktop\PRASADbot\gerayeh_VQ
```

اسکریپت‌ها مسیر پروژه را از محل فایل خودشان محاسبه می‌کنند. لازم نیست این مسیر را hard-code کنید؛ فقط فرمان‌ها را از داخل ریشهٔ همین checkout اجرا کنید.

## Prerequisites

- Windows Server با PowerShell 5.1 (یا PowerShell 7)
- Python 3.11+ و محیط مجازی در `.venv\Scripts\python.exe`
- نصب برنامه با `deploy\install.ps1` و `deploy\initialize.ps1`
- فایل `.env` کامل (این راهنما `.env` را چاپ یا کپی نمی‌کند)
- Caddy در حال اجرا با `reverse_proxy 127.0.0.1:8000`
- PowerShell **Run as administrator** برای نصب Scheduled Task

در PowerShell، ابتدا به ریشه پروژه بروید:

```powershell
cd C:\Users\Administrator\Desktop\PRASADbot\gerayeh_VQ
```

## ۱) توقف uvicorn دستی قبلی

اگر Backend را قبلاً با این فرمان بالا آورده‌اید:

```text
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers
```

اول همان پروسس را متوقف کنید. هر `python.exe` نامرتبط را Kill نکنید.

```powershell
Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -match 'uvicorn' -and $_.CommandLine -match 'app\.main:app' } |
  Select-Object ProcessId, CommandLine |
  Format-List
```

اگر PID متعلق به همین پروژه بود:

```powershell
taskkill /PID <PID> /T /F
```

سپس تأیید کنید پورت `8000` خالی است، یا فقط پروسس خودتان روی آن است:

```powershell
netstat -ano | findstr :8000
```

## ۲) نصب Scheduled Task (شروع خودکار با ویندوز)

از PowerShell با دسترسی Administrator:

```powershell
cd C:\Users\Administrator\Desktop\PRASADbot\gerayeh_VQ
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\deploy\windows\install_backend_task.ps1
```

این کار:

- وظیفه `GarayeNewsletter` را می‌سازد یا اگر وجود داشته باشد **به‌روز** می‌کند (duplicate نمی‌سازد)
- با startup ویندوز، با حساب `SYSTEM` و بالاترین سطح اجرا می‌شود (بدون Login کاربر)
- `deploy\windows\backend_supervisor.ps1` را اجرا می‌کند
- Uvicorn را روی `127.0.0.1:8000` نگه می‌دارد
- هر ۳۰ ثانیه `GET http://127.0.0.1:8000/health` را با timeout ۱۰ ثانیه چک می‌کند
- اگر پروسس بمیرد، یا ۳ بار پشت‌سرهم health شکست بخورد، فقط process tree همین Backend را Restart می‌کند

معادل قدیمی: `.\deploy\register-task.ps1`

## ۳) فرمان‌های روزمره

همه از ریشه پروژه:

```powershell
cd C:\Users\Administrator\Desktop\PRASADbot\gerayeh_VQ
```

### Start

```powershell
.\deploy\windows\start_backend.ps1
```

اگر Scheduled Task ثبت شده باشد همان را Start می‌کند. در غیر این صورت ناظر را مخفی اجرا می‌کند.

### Stop

فقط Backend را می‌بندد. کرالر بله را نمی‌بندد و `python.exe` نامرتبط را Kill نمی‌کند.

```powershell
.\deploy\windows\stop_backend.ps1
```

### Restart

```powershell
.\deploy\windows\restart_backend.ps1
```

### Status

```powershell
.\deploy\windows\status_backend.ps1
```

خروجی سالم باید شامل این‌ها باشد:

- `Health : OK (HTTP 200, status=ok)`
- `Bind : 127.0.0.1:8000`
- `Port 8000 Pids` یک PID واقعی
- `Uvicorn PID` زنده و `ourUvicorn=True`
- `Supervisor PID` زنده
- `Scheduled Task : GarayeNewsletter state=Running` (اگر Task نصب شده)

### Health check

```powershell
.\deploy\healthcheck.ps1
```

یا:

```powershell
curl.exe http://127.0.0.1:8000/health
```

خروجی سالم:

```json
{"status":"ok","ok":true,"version":"12.3.1"}
```

`ok: true` فقط برای سازگاری با اسکریپت‌های قبلی است. خود endpoint سبک است و به دیتابیس یا AI دست نمی‌زند.

## ۴) مشاهده logها

| منبع | مسیر |
| --- | --- |
| ناظر / Restartها | `logs\backend-supervisor.log` |
| stdout یووی‌کورن | `logs\uvicorn.out.log` |
| stderr یووی‌کورن | `logs\uvicorn.err.log` |
| لاگ برنامه (rotating) | `data\logs\prasad.log` |

```powershell
Get-Content .\logs\backend-supervisor.log -Tail 80
Get-Content .\logs\uvicorn.err.log -Tail 80
Get-Content .\data\logs\prasad.log -Tail 80
```

خطوط مهم ناظر:

- `Supervisor starting`
- `Started uvicorn pid=...`
- `Health OK after startup`
- `uvicorn pid=... exited unexpectedly`
- `Consecutive health failures reached 3`
- `Terminating uvicorn tree`
- `Cooldown ... after supervised restart`

لاگ برنامه شامل زمان startup/shutdown و استثنای مدیریت‌نشده است. پسورد، توکن، cookie و هدر Authorization ذخیره نمی‌شوند.

## ۵) تست Port 8000

Uvicorn باید فقط روی loopback گوش بدهد:

```powershell
netstat -ano | findstr :8000
```

خروجی سالم نمونه:

```text
TCP    127.0.0.1:8000         0.0.0.0:0              LISTENING       12345
```

اگر دیدید `0.0.0.0:8000` یعنی هنوز با bind عمومی بالا است. Stop کنید و از ناظر جدید استفاده کنید.

از یک ماشین **دیگر** در شبکه، `http://<public-ip>:8000` نباید باز شود. سایت باید فقط از طریق Caddy روی `80/443` برسد.

## ۶) تست Caddy و دامنه

از خود سرور:

```powershell
curl.exe -I http://127.0.0.1:8000/health
curl.exe -I https://prasad.lmskalk.ir
```

خروجی سالم health:

- HTTP `200`
- بدنه `{"status":"ok",...}`

خروجی سالم دامنه (نمونه):

- `HTTP/1.1 200` یا `HTTP/2 200` یا `302/303` به صفحه ورود
- **نباید** `502 Bad Gateway` باشد

Caddyfile پروژه از قبل این را دارد و نباید Backend خودش HTTPS بدهد:

```text
reverse_proxy 127.0.0.1:8000
```

پس از کپی Caddyfile، Caddy را Reload کنید (فرمان دقیق به نصب Caddy شما بستگی دارد)، مثلاً:

```powershell
caddy reload --config C:\path\to\Caddyfile
```

## ۷) خطای `System.Int32[]` هنگام شروع ناظر

اگر ناظر بلافاصله با این پیام می‌میرد:

```text
Cannot convert the "System.Int32[]" value of type "System.Int32[]" to type "System.Int32".
Test-GarayeOurUvicorn : ... parameter 'ProcessId'
```

یعنی روی سرور هنوز اسکریپت قدیمی است (`return ,$ids.ToArray()`). uvicorn اصلاً start نمی‌شود و پورت ۸۰۰۰ LISTENING نمی‌شود.

فایل‌های `deploy\windows` را به‌روز کنید، **یا** همین هات‌فیکس را از ریشه پروژه اجرا کنید:

```powershell
cd C:\Users\Administrator\Desktop\PRASADbot\gerayeh_VQ
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\deploy\windows\apply_pid_hotfix.ps1
```

اگر `apply_pid_hotfix.ps1` روی دیسک نیست، سه فایل `_common.ps1`، `backend_supervisor.ps1` و `status_backend.ps1` را از شاخه `cursor/windows-production-supervisor-9246` کپی کنید.

بعد دوباره:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\deploy\windows\backend_supervisor.ps1 -HostAddress 127.0.0.1 -Port 8000
```

خروجی سالم باید شامل `Started uvicorn` باشد، نه `Supervisor error`. در پنجره دیگر:

```powershell
curl.exe http://127.0.0.1:8000/health
netstat -ano | findstr :8000
```

باید `127.0.0.1:8000` در حالت `LISTENING` باشد. اگر هنوز همان `Int32[]` را دیدید، فایل‌ها جایگزین نشده‌اند.

سپس Scheduled Task را (با PowerShell Administrator) دوباره نصب کنید تا SYSTEM همان اسکریپت ثابت را اجرا کند:

```powershell
.\deploy\windows\install_backend_task.ps1
```

اگر ناظر `Started uvicorn` می‌نویسد ولی بعد از ۴۵ ثانیه health fail می‌شود، uvicorn زنده است اما `GET /health` هنوز OK نشده. در پنجرهٔ دوم:

```powershell
curl.exe -sS -D - --noproxy "*" http://127.0.0.1:8000/health
netstat -ano | findstr :8000
Test-Path .\app\health.py
Get-Content .\logs\uvicorn.err.log -Tail 80
```

- `404` یعنی فایل `app\health.py` روی سرور نیست؛ از همین شاخه کپی کنید.
- اتصال برقرار نمی‌شود یعنی هنوز LISTENING نیست یا import/startup گیر کرده؛ `uvicorn.err.log` را بخوانید.
- `200` با `{"status":"ok"...}` یعنی اپ سالم است و باید مهلت startup را زیاد کنید: `-StartupGraceSeconds 180`.

## ۸) تشخیص مشکل 502


`502` از Caddy یعنی به `127.0.0.1:8000` وصل نشده است.

1. Caddy را چک کنید:

```powershell
Get-Process caddy -ErrorAction SilentlyContinue
```

2. ببینید آیا واقعاً LISTENING است:

```powershell
netstat -ano | findstr :8000
```

3. اگر PID در Task Manager هست ولی `LISTENING` نیست، همان خرابی قبلی است. ناظر باید پس از ۳ شکست health، process tree را ببندد و دوباره بالا بیاورد.

4. Health:

```powershell
curl.exe http://127.0.0.1:8000/health
.\deploy\windows\status_backend.ps1
```

5. لاگ ناظر را برای Restart بخوانید:

```powershell
Select-String -Path .\logs\backend-supervisor.log -Pattern 'restart|health failed|Terminating|exited unexpectedly'
```

6. اگر پورت را پروسس دیگری گرفته، ناظر آن را Kill نمی‌کند؛ PID و CommandLine را در لاگ می‌نویسد.

## ۹) تست Auto-Recovery ناظر

### الف) مرگ پروسس uvicorn

PID دقیق همین Backend را بگیرید (نه یک python دیگر):

```powershell
.\deploy\windows\status_backend.ps1
```

یا:

```powershell
netstat -ano | findstr :8000
Get-CimInstance Win32_Process -Filter "ProcessId=<PID>" | Select-Object ProcessId, ExecutablePath, CommandLine
```

فقط همان PID را ببندید:

```powershell
taskkill /PID <PID> /T /F
```

حدود ۲۰ تا ۶۰ ثانیه صبر کنید، بعد:

```powershell
.\deploy\windows\status_backend.ps1
curl.exe http://127.0.0.1:8000/health
curl.exe -I https://prasad.lmskalk.ir
Get-Content .\logs\backend-supervisor.log -Tail 40
```

موفق یعنی:

- PID جدید شده
- `/health` دوباره `200` و `status=ok`
- سایت دیگر `502` نمی‌دهد
- لاگ شامل `exited unexpectedly` و سپس `Started uvicorn pid=...`

### ب) پروسس زنده ولی پاسخ‌ندادن (شبیه‌سازی خرابی قبلی)

PID را پیدا کنید، سپس:

```powershell
Suspend-Process -Id <PID>
```

حداقل ۹۰ ثانیه صبر کنید (۳ health ناموفق × ۳۰ ثانیه + cooldown). بعد:

```powershell
.\deploy\windows\status_backend.ps1
Get-Content .\logs\backend-supervisor.log -Tail 50
```

باید `Consecutive health failures reached 3` و Restart ثبت شده باشد. اگر پروسس معلق هنوز مانده بود:

```powershell
Resume-Process -Id <PID>
```

سپس در صورت نیاز `.\deploy\windows\restart_backend.ps1`

## ۱۰) Firewall

با bind شدن به `127.0.0.1` دسترسی عمومی به پورت `8000` از بین می‌رود. هیچ Rule فایروال به‌صورت خودکار ساخته یا حذف نمی‌شود.

برای دیدن Ruleهای inbound روی TCP/8000:

```powershell
Get-NetFirewallRule -Direction Inbound -ErrorAction SilentlyContinue |
  Get-NetFirewallPortFilter |
  Where-Object { $_.LocalPort -eq 8000 -or $_.LocalPort -eq '8000' }
```

یا:

```powershell
netsh advfirewall firewall show rule name=all dir=in |
  Select-String -Pattern '8000' -Context 8,8
```

فقط اگر مطمئن هستید Rule قدیمی برای باز کردن `8000` به Internet است، آن را دستی حذف کنید. نام Rule را از خروجی بالا بردارید:

```powershell
Remove-NetFirewallRule -DisplayName 'EXACT RULE NAME HERE'
```

Rule ناشناخته را حذف نکنید.

## ۱۱) Rollback

برای برگشت به اجرای دستی قبلی (موقت):

```powershell
.\deploy\windows\stop_backend.ps1
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --proxy-headers
```

حتی در Rollback هم `--host 0.0.0.0` توصیه نمی‌شود.

برای برداشتن ناظر جدید ولی نگه داشتن کد:

```powershell
.\deploy\windows\uninstall_backend_task.ps1
```

برای برگرداندن فایل‌های برنامه به نسخه git قبلی، از همان checkout/backup خودتان استفاده کنید. `.env` را از روی نسخه دیگر کپی نکنید مگر خودتان بخواهید.

## ۱۲) Uninstall

```powershell
cd C:\Users\Administrator\Desktop\PRASADbot\gerayeh_VQ
.\deploy\windows\uninstall_backend_task.ps1
```

Scheduled Task حذف می‌شود و Backend متوقف می‌گردد. داده، `.env` و `.venv` پاک نمی‌شوند.

معادل قدیمی: `.\deploy\unregister-task.ps1`

## ۱۳) جلوگیری از چند نسخه همزمان

- Mutex سراسری روی همین مسیر پروژه
- فایل‌های `run\garaye.pid` و `run\garaye-supervisor.pid`
- Scheduled Task با `IgnoreNew`
- اگر پورت `8000` را پروسس خارجی گرفته باشد، ناظر آن را Kill نمی‌کند

دو checkout جداگانه mutex جدا دارند. دو ناظر روی **همین** پوشه هم‌زمان اجرا نمی‌شوند.

## ۱۴) به‌روزرسانی زنده

ناظر پرچم‌های `run\reload.request` و `run\pip.request` را مثل قبل رعایت می‌کند. جزئیات: `docs\LIVE_UPDATE.md`
