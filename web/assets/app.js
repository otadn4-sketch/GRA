const $ = (id) => document.getElementById(id);
const state = {
  page: "overview", messages: [], selectedMessages: new Set(), sources: [],
  people: [], personCategories: [], topics: [], drafts: [], currentDraft: null, draftFilter: "", draftOriginFilter: "", automation: null,
  bulletinDrafts: [], bulletinFinalizationDay: "", bulletinHighAttentionItems: [],
  highAttentionDay: "", highAttentionDrafts: [], highAttentionRuns: [], currentHighAttention: null,
  analysisFilters: {speakers: [], general_topics: [], specific_topics: [], events: []},
  analyzedMessages: [], selectedAnalyzedMessages: new Set(),
  messageSearchTexts: new Map(),
  finalizationWindowConfirmed: false,
  finalizationView: "workbench", finalizationStage: "range",
  me: null, adminUsers: [], roles: [], monitoring: null, monitoringFocusDay: null,
  senderCandidates: [], senderProfiles: new Map(), garayeInsights: null, wordCloudResizeObserver: null,
  wordCloudResizeTimer: null, bulletinRuns: [], bulletinOutputPollTimer: null,
  botQueueRecoveryPollTimer: null,
  garayeWordTrendWord: "", personProfileId: null, calendar: {year: null, month: null, selectedDay: null, eventsByDay: new Map(), apiError: ""},
  streamOffset: 0, streamTotal: 0,
  jalaliPicker: {input: null, year: null, month: null},
  deskSubjectConfirmed: false,
  quickStart: {step: 1, dateFrom: "", dateTo: "", timeFrom: "00:00", timeTo: "23:59", analyzing: false, analysisDone: false, highAttentionDay: "", outputDay: ""},
  garayeSpeakerFocus: "",
  eitanAxes: [], eitanAxisId: "", eitanMessages: [], eitanOffset: 0, eitanTotal: 0, eitanCreating: false,
};
const pageMeta = {
  overview: ["تقویم و مناسبت‌ها", "تقویم رسمی هجری شمسی و مناسبت‌های روز"],
  stream: ["جریان اخبار", "جست‌وجو، بررسی و انتخاب پیام‌های دریافت‌شده"],
  automation: ["خودکارسازی", "کنترل مستقل تحلیل هوشمند، کنترل انسانی و کرولر Selenium"],
  finalization: ["نهایی‌سازی خبر", "تدوین متن پایه، خلاصه‌ها و مدیریت نسخه‌ها"],
  monitoring: ["نظارت", "آمار ارسال‌کنندگان، روند روزانه و میانگین امتیاز خودکار پیام‌ها"],
  garaye: ["گرایه", "نبض محتوای تحریریه: موضوع‌ها، واژه‌ها، گویندگان و ایتان گرا"],
  "eitan-gara": ["ایتان گرا", "جست‌وجوی پیام‌ها بر اساس محورهای کلیدواژه و افراد شاخص"],
  "high-attention": ["پربازتاب", "انتخاب خبرهای نهایی‌شده و تدوین محورهای پربازتاب"],
  bulletins: ["خبرنامه‌ها", "چینش خبرهای نهایی و تولید خروجی‌های انتشار"],
  people: ["شناسنامه اشخاص", "مدیریت نام، سمت، دسته و کانال اشخاص"],
  "person-profile": ["پروفایل شخص", "مشخصات، کانال‌ها و خبرهای منتسب به یک شخص"],
  sources: ["منابع پایش", "کانال‌های اشخاص و گروه‌های پشتیبانی"],
  users: ["کاربران و دسترسی", "تعریف حساب، نقش و مجوزهای هر کاربر"],
  "api-keys": ["کلیدهای API", "ساخت و ابطال کلید برای فراخوانی برنامه‌ای سامانه"],
  system: ["وضعیت سامانه", "سلامت اجزا، رویدادها، به‌روزرسانی زنده و پشتیبان‌گیری"],
  portal: ["پرتال کاربری", "نمایه، نقش، آمار ارسال و تنظیمات شخصی حساب شما"],
  "quick-start": ["شروع سریع", "انجام مرحله‌ای تحلیل، نهایی‌سازی، پربازتاب و خروجی خبرنامه"],
};
const fa = new Intl.NumberFormat("fa-IR");
const dateFormat = new Intl.DateTimeFormat("fa-IR-u-ca-persian", {
  timeZone: "Asia/Tehran", year: "numeric", month: "2-digit", day: "2-digit",
  weekday: "long", hour: "2-digit", minute: "2-digit", hour12: false, hourCycle: "h23",
});
const dateOnlyFormat = new Intl.DateTimeFormat("fa-IR-u-ca-persian", {
  timeZone: "Asia/Tehran", year: "numeric", month: "2-digit", day: "2-digit", weekday: "long",
});
const timeOnlyFormat = new Intl.DateTimeFormat("fa-IR", {
  timeZone: "Asia/Tehran", hour: "2-digit", minute: "2-digit", hour12: false, hourCycle: "h23",
});
const jalaliInputDateFormat = new Intl.DateTimeFormat("fa-IR-u-ca-persian", {
  timeZone: "Asia/Tehran", year: "numeric", month: "2-digit", day: "2-digit",
});
const timeInputFormat = new Intl.DateTimeFormat("en-GB", {
  timeZone: "Asia/Tehran", hour: "2-digit", minute: "2-digit", hour12: false, hourCycle: "h23",
});
const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
}[char]));
const n = (value) => fa.format(Number(value || 0));
const fdate = (value) => {
  if (!value) return "—";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? String(value) : dateFormat.format(parsed);
};
const fdateParts = (value) => {
  if (!value) return {date: "-", time: "-", raw: ""};
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return {date: String(value), time: "-", raw: String(value)};
  return {
    date: dateOnlyFormat.format(parsed),
    time: timeOnlyFormat.format(parsed),
    raw: parsed.toISOString(),
  };
};
const jalaliInputDate = (value) => {
  const parsed = value ? new Date(value) : new Date();
  return Number.isNaN(parsed.getTime()) ? "" : jalaliInputDateFormat.format(parsed);
};
const tehranTimeInput = (value) => {
  const parsed = value ? new Date(value) : new Date();
  return Number.isNaN(parsed.getTime()) ? "" : timeInputFormat.format(parsed);
};
const statusLabel = (value) => ({
  // Old approve/reject states are kept only for historical database rows;
  // the editorial flow exposes neither as a user-facing workflow.
  pending: "دریافت‌شده", approved: "دریافت‌شده", rejected: "دریافت‌شده",
  draft: "پیش‌نویس", finalized: "نهایی", completed: "آماده",
  running: "در حال اجرا", queued: "در صف", failed: "ناموفق",
}[value] || value || "نامشخص");
const senderLabel = (item) => {
  const profileName = String(
    item.sender_display_name || item.sender_profile_full_name || senderProfileFor(item)?.profile_full_name || ""
  ).trim();
  if (profileName) return profileName;
  if (item.sender_kind === "chat" || item.sender_chat_title || item.sender_chat_username) {
    const chat = item.sender_chat_title || (item.sender_chat_username ? `@${item.sender_chat_username}` : "گفتگوی ناشناس");
    return `${chat} (ارسال از طرف گفتگو؛ هویت انسانی پنهان)`;
  }
  return item.sender_name || (item.sender_username ? `@${item.sender_username}` : "نامشخص");
};
const senderProfileKey = (item = {}) => {
  const isChat = item.sender_kind === "chat" || item.sender_chat_id || item.sender_chat_username || item.sender_chat_title;
  if (isChat) return `chat:${item.sender_chat_id || item.source_chat_id || item.sender_chat_username || item.source_chat_username || "unknown"}`;
  return `person:${item.sender_id || item.sender_username || item.sender_name || item.source_chat_id || item.id || "unknown"}`;
};
const senderProfileFor = (item = {}) => state.senderProfiles.get(senderProfileKey(item)) || null;
const isExactBalePostUrl = (value) => {
  try {
    const parsed = new URL(String(value || ""));
    const parts = parsed.pathname.split("/").filter(Boolean);
    return (
      parsed.protocol === "https:"
      && parsed.hostname.toLowerCase() === "ble.ir"
      && parts.length === 3
      && /^[A-Za-z0-9_]{3,}$/.test(parts[0])
      && /^\d+$/.test(parts[1])
      && /^\d{13}$/.test(parts[2])
    );
  } catch (_) {
    return false;
  }
};

const jalaliMonths = ["فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور", "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند"];
const jalaliBreaks = [-61, 9, 38, 199, 426, 686, 756, 818, 1111, 1181, 1210, 1635, 2060, 2097, 2192, 2262, 2324, 2394, 2456, 3178];
const faDigits = (value) => String(value ?? "").replace(/\d/g, (digit) => "۰۱۲۳۴۵۶۷۸۹"[Number(digit)]);
const jalaliInputValue = (parts) => `${faDigits(parts.year)}/${faDigits(String(parts.month).padStart(2, "0"))}/${faDigits(String(parts.day).padStart(2, "0"))}`;
const jalaliToday = () => {
  const parts = new Intl.DateTimeFormat("en-US-u-ca-persian", {timeZone: "Asia/Tehran", year: "numeric", month: "numeric", day: "numeric"}).formatToParts(new Date());
  const map = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return {year: Number(map.year), month: Number(map.month), day: Number(map.day)};
};
// The Jalali conversion algorithm uses integer truncation (not floor) for
// negative intermediates such as the March-month offset.
const div = (a, b) => Math.trunc(a / b);
const mod = (a, b) => a - div(a, b) * b;
function jalCal(jy, withoutLeap = false) {
  const bl = jalaliBreaks.length;
  const gy = jy + 621;
  let leapJ = -14;
  let jp = jalaliBreaks[0];
  let jump = 0;
  if (jy < jp || jy >= jalaliBreaks[bl - 1]) throw new Error("سال جلالی خارج از محدودهٔ تقویم است.");
  let jm;
  for (let index = 1; index < bl; index += 1) {
    jm = jalaliBreaks[index];
    jump = jm - jp;
    if (jy < jm) break;
    leapJ += div(jump, 33) * 8 + div(mod(jump, 33), 4);
    jp = jm;
  }
  let nYear = jy - jp;
  leapJ += div(nYear, 33) * 8 + div(mod(nYear, 33) + 3, 4);
  if (mod(jump, 33) === 4 && jump - nYear === 4) leapJ += 1;
  const leapG = div(gy, 4) - div((div(gy, 100) + 1) * 3, 4) - 150;
  const march = 20 + leapJ - leapG;
  if (withoutLeap) return {gy, march};
  if (jump - nYear < 6) nYear = nYear - jump + div(jump + 4, 33) * 33;
  let leap = mod(mod(nYear + 1, 33) - 1, 4);
  if (leap === -1) leap = 4;
  return {leap, gy, march};
}
function g2d(gy, gm, gd) {
  let d = div((gy + div(gm - 8, 6) + 100100) * 1461, 4) + div(153 * mod(gm + 9, 12) + 2, 5) + gd - 34840408;
  d = d - div(div(gy + 100100 + div(gm - 8, 6), 100) * 3, 4) + 752;
  return d;
}
function d2g(jdn) {
  let j = 4 * jdn + 139361631;
  j = j + div(div(4 * jdn + 183187720, 146097) * 3, 4) * 4 - 3908;
  const i = div(mod(j, 1461), 4) * 5 + 308;
  const gd = div(mod(i, 153), 5) + 1;
  const gm = mod(div(i, 153), 12) + 1;
  const gy = div(j, 1461) - 100100 + div(8 - gm, 6);
  return {gy, gm, gd};
}
function j2d(jy, jm, jd) {
  const r = jalCal(jy, true);
  return g2d(r.gy, 3, r.march) + (jm - 1) * 31 - div(jm, 7) * (jm - 7) + jd - 1;
}
const jalaliToDate = (year, month, day) => {
  const gregorian = d2g(j2d(year, month, day));
  return new Date(Date.UTC(gregorian.gy, gregorian.gm - 1, gregorian.gd));
};
const jalaliMonthLength = (year, month) => month <= 6 ? 31 : (month <= 11 ? 30 : (jalCal(year).leap === 0 ? 30 : 29));
const weekdayFromJalali = (year, month, day) => (jalaliToDate(year, month, day).getUTCDay() + 1) % 7;
const jalaliDateFromGregorian = (date) => {
  const parts = new Intl.DateTimeFormat("en-US-u-ca-persian", {timeZone: "Asia/Tehran", year: "numeric", month: "numeric", day: "numeric"}).formatToParts(date);
  const map = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return {year: Number(map.year), month: Number(map.month), day: Number(map.day)};
};
const parseJalaliInput = (value) => {
  const digits = String(value || "").replace(/[۰-۹٠-٩]/g, (digit) => "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩".indexOf(digit) % 10);
  const match = digits.trim().match(/^(\d{4})[/-](\d{1,2})[/-](\d{1,2})$/);
  if (!match) return null;
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  if (month < 1 || month > 12 || day < 1 || day > jalaliMonthLength(year, month)) return null;
  return {year, month, day};
};
const jalaliValueToFlowDate = (value) => {
  const parts = parseJalaliInput(value);
  if (!parts) return "";
  const gregorian = d2g(j2d(parts.year, parts.month, parts.day));
  return `${gregorian.gy}-${String(gregorian.gm).padStart(2, "0")}-${String(gregorian.gd).padStart(2, "0")}`;
};
const latinDigits = (value) => String(value ?? "").replace(/[۰-۹٠-٩]/g, (digit) => "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩".indexOf(digit) % 10);

function closeJalaliPicker() {
  const picker = $("jalaliPicker");
  if (picker) picker.hidden = true;
  state.jalaliPicker.input = null;
}

function renderJalaliPickerDays() {
  const picker = $("jalaliPicker");
  if (!picker || picker.hidden) return;
  const year = state.jalaliPicker.year;
  const month = state.jalaliPicker.month;
  const selected = parseJalaliInput(state.jalaliPicker.input?.value || "");
  const today = jalaliToday();
  $("jalaliPickerMonth").textContent = `${jalaliMonths[month - 1]} ${faDigits(year)}`;
  const blanks = weekdayFromJalali(year, month, 1);
  const days = jalaliMonthLength(year, month);
  const cells = [];
  for (let index = 0; index < blanks; index += 1) cells.push(`<button type="button" class="blank" tabindex="-1"></button>`);
  for (let day = 1; day <= days; day += 1) {
    const isToday = today.year === year && today.month === month && today.day === day;
    const isSelected = selected && selected.year === year && selected.month === month && selected.day === day;
    cells.push(`<button type="button" class="${isToday ? "today" : ""} ${isSelected ? "selected" : ""}" data-picker-day="${day}">${faDigits(day)}</button>`);
  }
  $("jalaliPickerDays").innerHTML = cells.join("");
}

function openJalaliPicker(input) {
  const picker = $("jalaliPicker");
  if (!picker || !input) return;
  const parsed = parseJalaliInput(input.value) || jalaliToday();
  state.jalaliPicker = {input, year: parsed.year, month: parsed.month};
  picker.hidden = false;
  picker.style.position = "fixed";
  renderJalaliPickerDays();
  const rect = input.getBoundingClientRect();
  const top = Math.min(window.innerHeight - picker.offsetHeight - 12, rect.bottom + 8);
  const left = Math.max(12, Math.min(window.innerWidth - picker.offsetWidth - 12, rect.left));
  picker.style.top = `${Math.max(12, top)}px`;
  picker.style.left = `${left}px`;
}

function bindJalaliDatePickers() {
  document.querySelectorAll("[data-jalali-date]").forEach((input) => {
    if (input.dataset.pickerBound) return;
    input.dataset.pickerBound = "1";
    if (!input.parentElement?.classList.contains("jalali-date-wrap")) {
      const wrap = document.createElement("span");
      wrap.className = "jalali-date-wrap";
      input.parentNode.insertBefore(wrap, input);
      wrap.appendChild(input);
      const button = document.createElement("button");
      button.type = "button";
      button.className = "jalali-date-open";
      button.setAttribute("aria-label", "انتخاب از تقویم");
      button.title = "انتخاب از تقویم";
      button.textContent = "📅";
      wrap.appendChild(button);
      button.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        openJalaliPicker(input);
      });
    }
  });
}

function clock24(value) {
  let text = latinDigits(String(value || "")).trim().toLowerCase().replace(/[٫]/g, ":");
  if (!text) return "";
  const isPm = /p\.?\s*m\.?|ب\.?\s*ظ|بعدازظهر|بعد از ظهر/.test(text);
  const isAm = /a\.?\s*m\.?|ق\.?\s*ظ|قبل‌?ازظهر|قبل از ظهر/.test(text);
  text = text.replace(/a\.?\s*m\.?|p\.?\s*m\.?|ق\.?\s*ظ\.?|ب\.?\s*ظ\.?|قبل‌?ازظهر|بعدازظهر|قبل از ظهر|بعد از ظهر/g, "").trim();
  text = text.replace(/[.\-]/g, ":").replace(/\s+/g, "");
  const match = text.match(/^(\d{1,2}):(\d{1,2})(?::\d{1,2})?$/);
  if (!match) return "";
  let hour = Number(match[1]);
  const minute = Number(match[2]);
  if (isPm && hour < 12) hour += 12;
  if (isAm && hour === 12) hour = 0;
  if (hour > 23 || minute > 59) return "";
  return `${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}`;
}

function jalaliWindowParams(dateFromId, timeFromId, dateToId, timeToId) {
  const params = new URLSearchParams();
  const dateFrom = $(dateFromId)?.value.trim() || "";
  const dateTo = $(dateToId)?.value.trim() || dateFrom;
  const timeFrom = clock24($(timeFromId)?.value || "");
  const timeTo = clock24($(timeToId)?.value || "");
  if (dateFrom) params.set("date_from_jalali", dateFrom);
  if (dateTo) params.set("date_to_jalali", dateTo);
  if (timeFrom) params.set("time_from", timeFrom);
  if (timeTo) params.set("time_to", timeTo);
  params.set("timezone", "Asia/Tehran");
  return params;
}

function bindTime24Pickers() {
  const proto = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value");
  document.querySelectorAll('input[type="time"]').forEach((input) => {
    if (input.dataset.time24Bound === "1") return;
    input.dataset.time24Bound = "1";
    const wrap = document.createElement("div");
    wrap.className = "time24";
    wrap.setAttribute("dir", "ltr");
    const hour = document.createElement("select");
    hour.className = "time24-hour";
    hour.setAttribute("aria-label", "ساعت");
    const minute = document.createElement("select");
    minute.className = "time24-minute";
    minute.setAttribute("aria-label", "دقیقه");
    const sep = document.createElement("span");
    sep.className = "time24-sep";
    sep.textContent = ":";
    const optional = !input.required && !input.value;
    if (optional) {
      hour.appendChild(new Option("ساعت", ""));
      minute.appendChild(new Option("دقیقه", ""));
    }
    for (let h = 0; h < 24; h += 1) {
      const value = String(h).padStart(2, "0");
      hour.appendChild(new Option(value, value));
    }
    for (let m = 0; m < 60; m += 1) {
      const value = String(m).padStart(2, "0");
      minute.appendChild(new Option(value, value));
    }
    input.classList.add("time24-native");
    input.setAttribute("step", "60");
    input.parentNode.insertBefore(wrap, input);
    wrap.appendChild(hour);
    wrap.appendChild(sep);
    wrap.appendChild(minute);
    wrap.appendChild(input);
    const syncFromInput = () => {
      const parsed = clock24(input.value);
      if (!parsed) {
        hour.value = optional ? "" : "00";
        minute.value = optional ? "" : "00";
        return;
      }
      const [hh, mm] = parsed.split(":");
      hour.value = hh;
      minute.value = mm;
    };
    const syncToInput = () => {
      if (!hour.value && !minute.value) {
        proto.set.call(input, "");
      } else {
        proto.set.call(input, `${hour.value || "00"}:${minute.value || "00"}`);
      }
      input.dispatchEvent(new Event("change", {bubbles: true}));
      input.dispatchEvent(new Event("input", {bubbles: true}));
    };
    hour.addEventListener("change", syncToInput);
    minute.addEventListener("change", syncToInput);
    input.addEventListener("change", syncFromInput);
    input.addEventListener("input", syncFromInput);
    if (proto) {
      Object.defineProperty(input, "value", {
        configurable: true,
        get() { return proto.get.call(this); },
        set(next) {
          proto.set.call(this, next);
          syncFromInput();
        },
      });
    }
    syncFromInput();
  });
}

function calendarEventsFrom(value) {
  const source = Array.isArray(value) ? value : (value == null || value === "" ? [] : [value]);
  return source.map((entry) => {
    if (typeof entry === "string" || typeof entry === "number") return String(entry).trim();
    if (entry && typeof entry === "object") return String(entry.title || entry.name || entry.event || entry.description || entry.text || "").trim();
    return "";
  }).filter(Boolean);
}
function normalizeCalendarPayload(payload, year, month) {
  const root = payload?.data ?? payload?.days ?? payload?.result ?? payload;
  const nested = root?.days ?? root?.data ?? root;
  const rows = Array.isArray(nested)
    ? nested.map((row) => ["", row])
    : Object.entries(nested && typeof nested === "object" ? nested : {});
  const eventsByDay = new Map();
  for (const [fallbackDay, row] of rows) {
    if (!row || typeof row !== "object") continue;
    const dateToken = String(row.date || row.jalali_date || row.day || fallbackDay || "").replace(/[۰-۹٠-٩]/g, (digit) => "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩".indexOf(digit) % 10);
    const parts = dateToken.match(/(\d{1,2})(?!.*\d)/);
    const day = Number(parts?.[1] || row.day || 0);
    if (day < 1 || day > jalaliMonthLength(year, month)) continue;
    const rawHoliday = row.holiday ?? row.is_holiday ?? row.isHoliday;
    const holiday = rawHoliday === true || rawHoliday === 1 || ["1", "true", "yes", "بله"].includes(String(rawHoliday || "").toLowerCase());
    const events = calendarEventsFrom(row.event ?? row.events ?? row.occasion ?? row.occasions);
    eventsByDay.set(day, {holiday, events});
  }
  return eventsByDay;
}
function renderCalendarEvents() {
  const {year, month, selectedDay, eventsByDay, apiError} = state.calendar;
  const record = eventsByDay.get(selectedDay) || {holiday: false, events: []};
  const dateLabel = `${faDigits(selectedDay || "—")} ${jalaliMonths[(month || 1) - 1] || ""} ${faDigits(year || "")}`.trim();
  $("calendarEventsTitle").textContent = `مناسبت‌های ${dateLabel}`;
  if (apiError) {
    $("calendarEventsSubtitle").textContent = "فهرست مناسبت‌ها از سرویس تقویم دریافت نشد.";
    $("calendarEvents").innerHTML = `<div class="calendar-api-error"><b>اتصال به API تقویم برقرار نشد.</b><span>${esc(apiError)}</span><button type="button" class="outline-button" onclick="loadCalendar(true)">تلاش دوباره</button></div>`;
    return;
  }
  $("calendarEventsSubtitle").textContent = record.holiday ? "این روز تعطیل رسمی است." : "تعطیل رسمی نیست.";
  const events = [...record.events];
  if (record.holiday && !events.some((event) => /تعطیل/.test(event))) events.unshift("تعطیل رسمی");
  $("calendarEvents").innerHTML = events.length
    ? `<ul>${events.map((event) => `<li>${esc(event)}</li>`).join("")}</ul>`
    : `<div class="empty-mini">مناسبتی برای این روز در API ثبت نشده است.</div>`;
}
function renderCalendar() {
  const {year, month, selectedDay, eventsByDay} = state.calendar;
  if (!year || !month) return;
  const today = jalaliToday();
  const daysInMonth = jalaliMonthLength(year, month);
  const leading = weekdayFromJalali(year, month, 1);
  $("calendarMonthLabel").textContent = `${jalaliMonths[month - 1]} ${faDigits(year)}`;
  $("calendarTodayLabel").textContent = `امروز: ${jalaliInputValue(today)}`;
  const blanks = Array.from({length: leading}, () => '<span class="calendar-day blank" aria-hidden="true"></span>');
  const days = Array.from({length: daysInMonth}, (_, index) => {
    const day = index + 1;
    const record = eventsByDay.get(day) || {holiday: false, events: []};
    const friday = weekdayFromJalali(year, month, day) === 6;
    const isToday = year === today.year && month === today.month && day === today.day;
    const active = day === selectedDay;
    return `<button type="button" class="calendar-day${friday || record.holiday ? " holiday" : ""}${isToday ? " today" : ""}${active ? " selected" : ""}" data-calendar-day="${day}" aria-label="${faDigits(day)} ${jalaliMonths[month - 1]}${record.holiday ? "، تعطیل رسمی" : ""}"><b>${faDigits(day)}</b>${record.events.length ? '<i aria-hidden="true"></i>' : ""}</button>`;
  });
  $("calendarDays").innerHTML = [...blanks, ...days].join("");
  $("calendarDays").querySelectorAll("[data-calendar-day]").forEach((button) => button.addEventListener("click", () => {
    state.calendar.selectedDay = Number(button.dataset.calendarDay);
    renderCalendar();
    renderCalendarEvents();
  }));
  renderCalendarEvents();
}
async function loadCalendar(force = false) {
  const calendar = state.calendar;
  const today = jalaliToday();
  if (!calendar.year || !calendar.month) {
    calendar.year = today.year;
    calendar.month = today.month;
    calendar.selectedDay = today.day;
  }
  const cacheKey = `garaye:jalali-calendar:${calendar.year}:${calendar.month}`;
  calendar.apiError = "";
  try {
    const cached = !force ? localStorage.getItem(cacheKey) : null;
    let payload;
    if (cached) {
      payload = JSON.parse(cached);
    } else {
      const response = await fetch(`https://pnldev.com/api/calender?year=${encodeURIComponent(calendar.year)}&month=${encodeURIComponent(calendar.month)}`);
      if (!response.ok) throw new Error(`پاسخ ناموفق سرویس تقویم (${response.status})`);
      payload = await response.json();
      localStorage.setItem(cacheKey, JSON.stringify(payload));
    }
    calendar.eventsByDay = normalizeCalendarPayload(payload, calendar.year, calendar.month);
  } catch (error) {
    calendar.eventsByDay = new Map();
    calendar.apiError = error instanceof Error ? error.message : "خطای نامشخص در دریافت مناسبت‌ها";
  }
  renderCalendar();
}
function moveCalendarMonth(delta) {
  let {year, month} = state.calendar;
  month += delta;
  if (month < 1) { year -= 1; month = 12; }
  if (month > 12) { year += 1; month = 1; }
  state.calendar.year = year;
  state.calendar.month = month;
  state.calendar.selectedDay = 1;
  state.calendar.eventsByDay = new Map();
  loadCalendar().catch((error) => toast(error.message, true));
}

function renderTopicTrend(trends = {}) {
  const days = Array.isArray(trends.days) ? trends.days : [];
  const series = Array.isArray(trends.series) ? trends.series : [];
  // Topic series deliberately use a data-visualization palette, independent
  // from the product's teal identity colors.
  const palette = ["#7C3AED", "#DB2777", "#EA580C", "#65A30D", "#0284C7", "#CA8A04", "#4F46E5", "#0F766E"];
  $("trendWindow").textContent = `${n(trends.window_days || days.length || 14)} روز اخیر · ${n(trends.total_finalized)} خبر نهایی`;
  if (!days.length || !series.length) {
    $("topicTrendChart").innerHTML = `
      <div class="trend-empty">
        <b>هنوز روندی برای نمایش شکل نگرفته است.</b>
        <span>در بخش نهایی‌سازی، موضوع خبر را انتخاب و خبر را نهایی کنید؛ نمودار به‌صورت خودکار به‌روز می‌شود.</span>
      </div>`;
    $("topicTrendLegend").innerHTML = "";
    return;
  }

  const width = 1000;
  const height = 320;
  const pad = {top: 22, right: 28, bottom: 54, left: 54};
  const plotWidth = width - pad.left - pad.right;
  const plotHeight = height - pad.top - pad.bottom;
  const maxValue = Math.max(1, ...series.flatMap((item) => (item.values || []).map(Number)));
  const yMax = maxValue <= 4 ? Math.max(2, maxValue) : Math.ceil(maxValue / 4) * 4;
  const gridSteps = Math.min(4, yMax);
  const xAt = (index) => pad.left + (days.length === 1 ? plotWidth / 2 : index * plotWidth / (days.length - 1));
  const yAt = (value) => pad.top + plotHeight - Math.min(yMax, Number(value || 0)) * plotHeight / yMax;
  const shortDate = (value) => {
    const date = new Date(`${value}T12:00:00+03:30`);
    return Number.isNaN(date.getTime())
      ? value
      : new Intl.DateTimeFormat("fa-IR-u-ca-persian", {
        timeZone: "Asia/Tehran", month: "2-digit", day: "2-digit",
      }).format(date);
  };
  const grid = Array.from({length: gridSteps + 1}, (_, index) => {
    const value = yMax * index / gridSteps;
    const y = yAt(value);
    return `<g><line x1="${pad.left}" y1="${y}" x2="${width - pad.right}" y2="${y}" class="trend-grid-line"/><text x="${pad.left - 12}" y="${y + 5}" class="trend-axis-label">${n(value)}</text></g>`;
  }).join("");
  const labelEvery = days.length > 10 ? 2 : 1;
  const xLabels = days.map((day, index) => (
    index % labelEvery === 0 || index === days.length - 1
      ? `<text x="${xAt(index)}" y="${height - 18}" class="trend-axis-label trend-date-label">${esc(shortDate(day))}</text>`
      : ""
  )).join("");
  const lines = series.map((item, seriesIndex) => {
    const color = palette[seriesIndex % palette.length];
    const values = days.map((_, index) => Number((item.values || [])[index] || 0));
    const points = values.map((value, index) => `${xAt(index)},${yAt(value)}`).join(" ");
    const dots = values.map((value, index) => `
      <circle cx="${xAt(index)}" cy="${yAt(value)}" r="${value ? 4.5 : 2.5}" fill="${color}">
        <title>${esc(item.topic)} · ${esc(days[index])}: ${n(value)}</title>
      </circle>`).join("");
    return `<g><polyline points="${points}" fill="none" stroke="${color}" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>${dots}</g>`;
  }).join("");
  $("topicTrendChart").innerHTML = `
    <svg class="trend-svg" viewBox="0 0 ${width} ${height}" role="img" aria-label="روند موضوعات خبرهای نهایی‌شده در چهارده روز اخیر">
      ${grid}${xLabels}${lines}
    </svg>`;
  $("topicTrendLegend").innerHTML = series.map((item, index) => `
    <span class="trend-key">
      <i class="trend-swatch" style="background:${palette[index % palette.length]}"></i>
      <b>${esc(item.topic)}</b>
      <small>${n(item.total)} خبر</small>
    </span>`).join("");
}

async function api(url, options = {}) {
  const config = {...options, headers: {...(options.headers || {})}};
  if (config.body && !(config.body instanceof FormData)) config.headers["Content-Type"] = "application/json";
  const response = await fetch(url, config);
  if (!response.ok) {
    if (response.status === 401 && location.pathname !== "/login") {
      location.replace("/login");
      throw new Error("نشست ورود پایان یافته است.");
    }
    let message = `${response.status} ${response.statusText}`;
    try {
      const data = await response.json();
      message = typeof data.detail === "string"
        ? data.detail
        : data.detail?.message || data.message || message;
    } catch (_) {
      const text = await response.text();
      if (text) message = text.slice(0, 500);
    }
    throw new Error(message);
  }
  if (response.status === 204) return null;
  return response.json();
}

let toastTimer;
function toast(message, isError = false) {
  const box = $("toast");
  box.textContent = message;
  box.className = `toast show${isError ? " error" : ""}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => box.className = "toast", 3800);
}

function showPage(page, section = "") {
  const leavingQuickStart = state.page === "quick-start" && page !== "quick-start";
  if (page === "finalization") {
    state.finalizationView = section === "drafts" ? "drafts" : "workbench";
    section = state.finalizationView;
  }
  if (page === "person-profile") {
    const personId = Number(section);
    if (!Number.isInteger(personId) || personId < 1) {
      return showPage("people");
    }
    state.personProfileId = personId;
    section = String(personId);
  }
  state.page = page;
  state.pageSection = section;
  if (leavingQuickStart) unmountQuickStartDesk();
  if (page === "finalization") renderFinalizationView();
  document.querySelectorAll(".page").forEach((element) => element.classList.toggle("active", element.id === `page-${page}`));
  document.querySelectorAll(".nav-item, .quick-start-nav").forEach((element) => element.classList.toggle(
    "active", (element.dataset.page === page || (page === "person-profile" && element.dataset.page === "people") || (page === "portal" && element.dataset.page === "portal"))
      && (!element.dataset.section || element.dataset.section === section)
  ));
  document.querySelectorAll(".nav-menu-group").forEach((group) => {
    const hasActiveItem = Boolean(group.querySelector(".nav-item.active"));
    group.classList.toggle("has-active", hasActiveItem);
    if (hasActiveItem) group.open = true;
  });
  const [title, subtitle] = pageMeta[page] || pageMeta.overview;
  $("pageTitle").textContent = title;
  $("pageSubtitle").textContent = subtitle;
  closeMobileNav();
  history.replaceState(null, "", `#${page}${section ? `:${section}` : ""}`);
  loadPage(page).catch((error) => toast(error.message, true));
}

function closeMobileNav() {
  $("sidebar")?.classList.remove("open");
  const backdrop = $("navBackdrop");
  if (backdrop) backdrop.hidden = true;
  document.body.classList.remove("nav-open");
}

function openMobileNav() {
  $("sidebar")?.classList.add("open");
  const backdrop = $("navBackdrop");
  if (backdrop) backdrop.hidden = false;
  document.body.classList.add("nav-open");
}

function toggleMobileNav() {
  if ($("sidebar")?.classList.contains("open")) closeMobileNav();
  else openMobileNav();
}

function closeNavigationMenus() {
  document.querySelectorAll(".nav-menu-group").forEach((group) => {
    group.open = false;
    group.querySelectorAll("details").forEach((submenu) => { submenu.open = false; });
  });
}

async function loadPage(page) {
  if (page === "overview") return loadCalendar();
  if (page === "stream") return loadStream();
  if (page === "automation") return loadAutomationWorkspace();
  if (page === "finalization") return loadFinalizationWorkspace();
  if (page === "monitoring") return loadMonitoring();
  if (page === "garaye") return loadGarayeInsights();
  if (page === "eitan-gara") return loadEitanGaraPage();
  if (page === "high-attention") return loadHighAttentionWorkspace();
  if (page === "bulletins") return loadBulletinWorkspace();
  if (page === "people") return loadPeople();
  if (page === "person-profile") return loadPersonProfile();
  if (page === "sources") return loadSources();
  if (page === "users") return loadAdminUsers();
  if (page === "api-keys") return loadApiKeys();
  if (page === "system") return loadSystem();
  if (page === "portal") return loadUserPortal();
  if (page === "quick-start") return loadQuickStart();
}

async function loadCurrentUser() {
  state.me = await api("/admin/api/me");
  $("currentUser").textContent = `${state.me.full_name} · ${state.me.role_title}`;
  if ($("sidebarVersion") && state.me.version) $("sidebarVersion").textContent = faDigits(state.me.version);
  document.querySelectorAll("[data-permission]").forEach((element) => {
    const permissions = state.me.permissions || [];
    element.classList.toggle(
      "hidden",
      !permissions.includes("*") && !permissions.includes(element.dataset.permission)
    );
  });
}

async function loadSenderProfiles() {
  const candidates = await api("/admin/api/users/senders");
  state.senderCandidates = Array.isArray(candidates) ? candidates : [];
  state.senderProfiles = new Map(
    state.senderCandidates.filter((item) => item.sender_key).map((item) => [String(item.sender_key), item])
  );
}

async function loadGarayeOverviewStats() {
  const stats = await api("/admin/api/stats");
  const counts = stats.counts || {};
  $("metrics").innerHTML = [
    ["کل پیام‌ها", counts.total, "◫", ""],
    ["پیام‌های دریافت‌شده", counts.pending, "◷", "amber"],
    ["تحلیل‌شده", counts.analyzed, "✦", "green"],
    ["منابع فعال", counts.active_sources, "⌁", "red"],
  ].map(([label, value, icon, tone]) => `<article class="metric-card"><span class="metric-icon ${tone}">${icon}</span><div><b>${n(value)}</b><small>${label}</small></div></article>`).join("");
  return stats;
}

function monitoringDayLabel(value) {
  const parsed = new Date(`${value}T12:00:00+03:30`);
  return Number.isNaN(parsed.getTime()) ? value : new Intl.DateTimeFormat("fa-IR-u-ca-persian", {
    timeZone: "Asia/Tehran", month: "numeric", day: "numeric",
  }).format(parsed);
}

async function loadMonitoring() {
  const days = Number($("monitoringDays").value || 30);
  const data = await api(`/admin/api/monitoring?days=${days}`);
  state.monitoring = data;
  $("monitoringMetrics").innerHTML = [
    ["کل پیام‌های ثبت‌شده", data.total_messages, "◫", ""],
    [`پیام‌های ${n(data.window_days)} روز اخیر`, data.window_messages, "▥", "green"],
    ["میانگین امتیاز خودکار", data.average_rating == null ? "—" : n(data.average_rating), "★", "amber"],
    ["ارسال‌کنندگان", (data.people || []).length, "♙", "red"],
  ].map(([label, value, icon, tone]) => `<article class="metric-card"><span class="metric-icon ${tone}">${icon}</span><div><b>${esc(value)}</b><small>${label}</small></div></article>`).join("");
  const availableDays = new Set((data.daily_totals || []).filter((item) => Number(item.count || 0) > 0).map((item) => item.date));
  if (state.monitoringFocusDay && !availableDays.has(state.monitoringFocusDay)) state.monitoringFocusDay = null;
  renderMonitoringChart(data, state.monitoringFocusDay);
  $("monitoringRows").innerHTML = (data.people || []).length ? data.people.map((item) => {
    const activeDays = Object.entries(item.daily || {}).filter(([, count]) => Number(count) > 0);
    const daily = activeDays.map(([date, count]) => `<span>${esc(monitoringDayLabel(date))}: ${n(count)}</span>`).join(" · ") || "—";
    const username = item.sender_username ? `<small>@${esc(item.sender_username)}</small>` : "";
    const label = item.profile_full_name || item.sender_name;
    const profile = item.profile_user_id
      ? `<button class="text-button sender-profile-link" type="button" onclick="viewAdminUserProfile(${Number(item.profile_user_id)})">نمایش پروفایل</button>`
      : `<span class="sender-unlinked">بدون پروفایل</span>`;
    return `<tr><td><b>${esc(label)}</b>${username}<div>${profile}</div></td><td>${esc(item.sender_type)}</td><td>${n(item.total_messages)}</td><td>${n(item.rated_messages)}</td><td>${item.average_rating == null ? "—" : `<span class="monitoring-score">★ ${n(item.average_rating)}</span>`}</td><td><b>${n(item.window_messages)}</b><div class="monitoring-days">${daily}</div></td></tr>`;
  }).join("") : `<tr><td colspan="6">هنوز هیچ ارسال‌کننده‌ای از داده‌های پیام‌ها قابل بازیابی نیست.</td></tr>`;
}

function renderMonitoringChart(data, focusDay = null) {
  const chart = $("monitoringDailyChart");
  const reset = $("monitoringResetChart");
  const activeDays = (data.daily_totals || []).filter((item) => Number(item.count || 0) > 0);
  if (!focusDay) {
    $("monitoringChartTitle").textContent = "روند پیام‌های دریافتی";
    $("monitoringChartSubtitle").textContent = "روزهای دارای پیام، از راست به چپ نمایش داده می‌شوند؛ برای دیدن سهم ارسال‌کنندگان همان روز، روی ستون آن کلیک کنید.";
    reset.classList.add("hidden");
    const max = Math.max(1, ...activeDays.map((item) => Number(item.count || 0)));
    chart.className = "monitoring-daily-chart";
    chart.innerHTML = activeDays.length ? activeDays.map((item) => {
      const height = Math.max(8, Math.round(Number(item.count || 0) / max * 100));
      return `<button type="button" class="monitoring-bar monitoring-bar-button" title="نمایش سهم ارسال‌کنندگان در ${esc(monitoringDayLabel(item.date))}" onclick="showMonitoringDayShare('${esc(item.date)}')"><b>${n(item.count)}</b><span class="monitoring-bar-track"><i style="height:${height}%"></i></span><small>${esc(monitoringDayLabel(item.date))}</small></button>`;
    }).join("") : `<div class="empty-mini">در این بازه روز دارای پیامی ثبت نشده است.</div>`;
    return;
  }
  const total = Number((data.daily_totals || []).find((item) => item.date === focusDay)?.count || 0);
  const shares = (data.people || []).map((item) => ({
    ...item, count: Number((item.daily || {})[focusDay] || 0),
  })).filter((item) => item.count > 0).sort((a, b) => b.count - a.count);
  $("monitoringChartTitle").textContent = `سهم ارسال‌کنندگان در ${monitoringDayLabel(focusDay)}`;
  $("monitoringChartSubtitle").textContent = "سهم هر ارسال‌کننده از کل پیام‌های همان روز؛ برای بازگشت، دکمهٔ کنار عنوان را بزنید.";
  reset.classList.remove("hidden");
  chart.className = "monitoring-daily-chart monitoring-share-chart";
  const palette = ["#008080", "#4f46e5", "#c27500", "#8a4a2c", "#0f766e", "#7c3f8d", "#475569"];
  chart.innerHTML = shares.length ? shares.map((item, index) => {
    const share = total ? Math.round(item.count / total * 100) : 0;
    const label = item.profile_full_name || item.sender_name;
    return `<article class="monitoring-share-row"><span class="monitoring-share-dot" style="background:${palette[index % palette.length]}"></span><b>${esc(label)}</b><span class="monitoring-share-track"><i style="width:${share}%;background:${palette[index % palette.length]}"></i></span><small>${n(item.count)} پیام · ${n(share)}٪</small></article>`;
  }).join("") : `<div class="empty-mini">برای این روز ارسال‌کننده‌ای ثبت نشده است.</div>`;
}

function showMonitoringDayShare(day) {
  if (!state.monitoring) return;
  state.monitoringFocusDay = day;
  renderMonitoringChart(state.monitoring, day);
}

function garayeDateLabel(value) {
  return monitoringDayLabel(value);
}

function renderGarayeBars(targetId, rows, emptyText) {
  const target = $(targetId);
  if (!target) return;
  const values = rows || [];
  const max = Math.max(1, ...values.map((item) => Number(item.count || 0)));
  target.innerHTML = values.length ? values.map((item) => {
    const width = Math.max(6, Math.round(Number(item.count || 0) / max * 100));
    return `<article class="garaye-rank-row"><b title="${esc(item.name || item.date || "")}">${esc(item.name || garayeDateLabel(item.date))}</b><span class="garaye-rank-track"><i style="width:${width}%"></i></span><small>${n(item.count)}</small></article>`;
  }).join("") : `<div class="empty-mini">${esc(emptyText)}</div>`;
}

const GARAYE_LINE_PALETTE = ["#007c7c", "#4f46e5", "#c27500", "#0f766e", "#db2777", "#0284c7", "#65a30d", "#7c3aed", "#8a4a2c", "#475569"];

function garayeShortDate(value) {
  const date = new Date(`${value}T12:00:00+03:30`);
  return Number.isNaN(date.getTime())
    ? value
    : new Intl.DateTimeFormat("fa-IR-u-ca-persian", {
      timeZone: "Asia/Tehran", month: "2-digit", day: "2-digit",
    }).format(date);
}

function catmullRomPath(points) {
  if (!points.length) return "";
  if (points.length === 1) return `M ${points[0][0]} ${points[0][1]}`;
  let path = `M ${points[0][0]} ${points[0][1]}`;
  for (let index = 0; index < points.length - 1; index += 1) {
    const p0 = points[Math.max(0, index - 1)];
    const p1 = points[index];
    const p2 = points[index + 1];
    const p3 = points[Math.min(points.length - 1, index + 2)];
    const c1x = p1[0] + (p2[0] - p0[0]) / 6;
    const c1y = p1[1] + (p2[1] - p0[1]) / 6;
    const c2x = p2[0] - (p3[0] - p1[0]) / 6;
    const c2y = p2[1] - (p3[1] - p1[1]) / 6;
    path += ` C ${c1x.toFixed(2)} ${c1y.toFixed(2)}, ${c2x.toFixed(2)} ${c2y.toFixed(2)}, ${p2[0]} ${p2[1]}`;
  }
  return path;
}

function alignSeriesToDays(rows, days) {
  return (rows || []).map((item) => {
    const byDate = new Map((item.series || []).map((row) => [row.date, Number(row.count || 0)]));
    return {
      name: item.name,
      total: Number(item.count || 0),
      values: days.map((day) => byDate.get(day) || 0),
      person_id: item.person_id || null,
    };
  });
}

function renderCurvedTrendChart(targetId, legendId, {days, series, emptyText, ariaLabel, focusName = "", onSelect = null}) {
  const target = $(targetId);
  const legend = $(legendId);
  if (!target) return;
  if (!days.length || !series.length) {
    target.innerHTML = `<div class="trend-empty"><b>${esc(emptyText)}</b></div>`;
    if (legend) legend.innerHTML = "";
    return;
  }
  const width = 1000;
  const height = 320;
  const pad = {top: 22, right: 28, bottom: 54, left: 54};
  const plotWidth = width - pad.left - pad.right;
  const plotHeight = height - pad.top - pad.bottom;
  const maxValue = Math.max(1, ...series.flatMap((item) => (item.values || []).map(Number)));
  const yMax = maxValue <= 4 ? Math.max(2, maxValue) : Math.ceil(maxValue / 4) * 4;
  const gridSteps = Math.min(4, yMax);
  const xAt = (index) => pad.left + (days.length === 1 ? plotWidth / 2 : index * plotWidth / (days.length - 1));
  const yAt = (value) => pad.top + plotHeight - Math.min(yMax, Number(value || 0)) * plotHeight / yMax;
  const focused = Boolean(focusName);
  const grid = Array.from({length: gridSteps + 1}, (_, index) => {
    const value = yMax * index / gridSteps;
    const y = yAt(value);
    return `<g><line x1="${pad.left}" y1="${y}" x2="${width - pad.right}" y2="${y}" class="trend-grid-line"/><text x="${pad.left - 12}" y="${y + 5}" class="trend-axis-label">${n(value)}</text></g>`;
  }).join("");
  const labelEvery = days.length > 10 ? 2 : 1;
  const xLabels = days.map((day, index) => (
    index % labelEvery === 0 || index === days.length - 1
      ? `<text x="${xAt(index)}" y="${height - 18}" class="trend-axis-label trend-date-label">${esc(garayeShortDate(day))}</text>`
      : ""
  )).join("");
  const lines = series.map((item, seriesIndex) => {
    const color = GARAYE_LINE_PALETTE[seriesIndex % GARAYE_LINE_PALETTE.length];
    const values = days.map((_, index) => Number((item.values || [])[index] || 0));
    const points = values.map((value, index) => [xAt(index), yAt(value)]);
    const dim = focused && item.name !== focusName;
    const strong = focused && item.name === focusName;
    const strokeWidth = strong ? 4.2 : (dim ? 2.2 : 2.8);
    const opacity = dim ? "0.16" : "1";
    const dots = values.map((value, index) => `
      <circle cx="${xAt(index)}" cy="${yAt(value)}" r="${value ? (strong ? 5 : 3.8) : 2.2}" fill="${color}">
        <title>${esc(item.name)} · ${esc(garayeShortDate(days[index]))}: ${n(value)}</title>
      </circle>`).join("");
    return `<g class="trend-series" data-series-name="${esc(item.name)}" opacity="${opacity}">
      <path d="${catmullRomPath(points)}" fill="none" stroke="${color}" stroke-width="${strokeWidth}" stroke-linecap="round" stroke-linejoin="round"/>
      <path d="${catmullRomPath(points)}" fill="none" stroke="transparent" stroke-width="14"/>
      ${dots}
    </g>`;
  }).join("");
  target.innerHTML = `
    <svg class="trend-svg" viewBox="0 0 ${width} ${height}" role="img" aria-label="${esc(ariaLabel || "نمودار منحنی")}">
      ${grid}${xLabels}${lines}
    </svg>`;
  if (onSelect) {
    target.querySelector("svg")?.addEventListener("click", () => onSelect(""));
    target.querySelectorAll("[data-series-name]").forEach((group) => {
      group.addEventListener("click", (event) => {
        event.stopPropagation();
        onSelect(group.getAttribute("data-series-name") || "");
      });
    });
  }
  if (legend) {
    legend.innerHTML = series.map((item, index) => {
      const dim = focused && item.name !== focusName;
      const strong = focused && item.name === focusName;
      return `
      <span class="trend-key${strong ? " is-focused" : ""}${dim ? " is-dimmed" : ""}" data-series-name="${esc(item.name)}">
        <i class="trend-swatch" style="background:${GARAYE_LINE_PALETTE[index % GARAYE_LINE_PALETTE.length]}"></i>
        <b>${esc(item.name)}</b>
        <small>${n(item.total)}</small>
      </span>`;
    }).join("");
    if (onSelect) {
      legend.querySelectorAll("[data-series-name]").forEach((key) => {
        key.addEventListener("click", (event) => {
          event.stopPropagation();
          onSelect(key.getAttribute("data-series-name") || "");
        });
      });
    }
  }
}

function findPersonBySpeakerName(name) {
  const key = String(name || "").trim();
  if (!key) return null;
  return (state.people || []).find((item) => item.full_name === key)
    || (state.people || []).find((item) => String(item.aliases || "").split("|").map((part) => part.trim()).includes(key))
    || null;
}

function renderGarayeWordCloud(words = state.garayeInsights?.word_cloud || []) {
  const target = $("garayeWordCloud");
  const values = Array.isArray(words) ? words : [];
  if (!values.length) {
    target.innerHTML = `<div class="garaye-word-cloud-empty"><b>هنوز دادهٔ محتوایی کافی نداریم.</b><span>با دریافت پیام و ثبت برچسب‌های تحلیل‌شده، ابر واژه در اینجا شکل می‌گیرد.</span></div>`;
    return;
  }
  const layoutEngine = window.GarayeWordCloudLayout;
  if (!layoutEngine?.layout) {
    target.innerHTML = `<div class="empty-mini">مؤلفهٔ ابر واژه بارگذاری نشد؛ صفحه را یک‌بار تازه‌سازی کنید.</div>`;
    return;
  }
  const width = Math.max(260, target.clientWidth || 0);
  const height = Math.max(240, target.clientHeight || 0);
  const layout = layoutEngine.layout(values, width, height);
  if (!layout.length) {
    target.innerHTML = `<div class="garaye-word-cloud-empty"><b>واژهٔ محتوایی قابل‌نمایشی پیدا نشد.</b><span>فیلتر بازهٔ زمانی را تغییر دهید یا پیام‌های بیشتری تحلیل کنید.</span></div>`;
    return;
  }
  target.innerHTML = layout.map((item, index) => {
    const tone = index % 6;
    const strength = item.central ? 760 : Math.max(500, Math.round(470 + (item.fontSize / 44) * 210));
    return `<button class="garaye-word word-tone-${tone}${item.central ? " central" : ""}${item.word === state.garayeWordTrendWord ? " is-selected" : ""}" type="button" data-word="${esc(item.word)}" data-word-index="${index}" style="left:${item.x}px;top:${item.y}px;--word-size:${item.fontSize}px;--word-weight:${strength}" title="${n(item.count)} بار تکرار" aria-label="${esc(item.word)}؛ ${n(item.count)} بار تکرار. برای نمایش روند روزانه کلیک کنید.">${esc(item.word)}</button>`;
  }).join("");
  target.querySelectorAll(".garaye-word").forEach((button) => {
    button.addEventListener("click", () => {
      const item = layout[Number(button.dataset.wordIndex)];
      if (!item?.word) return;
      state.garayeWordTrendWord = item.word;
      $("garayeWordTrendSelect").value = item.word;
      renderGarayeWordTrend();
      $("garayeWordTreemap")?.scrollIntoView({behavior: "smooth", block: "center"});
    });
  });
}

function observeGarayeWordCloud() {
  const target = $("garayeWordCloud");
  if (state.wordCloudResizeObserver || !window.ResizeObserver) return;
  state.wordCloudResizeObserver = new ResizeObserver(() => {
    clearTimeout(state.wordCloudResizeTimer);
    state.wordCloudResizeTimer = setTimeout(() => {
      if (state.page === "garaye" && state.garayeInsights) renderGarayeWordCloud();
    }, 120);
  });
  state.wordCloudResizeObserver.observe(target);
}

async function loadGarayeInsights() {
  const params = new URLSearchParams({days: "30"});
  if ($("garayeFrom").value.trim()) params.set("date_from_jalali", $("garayeFrom").value.trim());
  if ($("garayeTo").value.trim()) params.set("date_to_jalali", $("garayeTo").value.trim());
  if ($("garayeFromTime").value) params.set("time_from", $("garayeFromTime").value);
  if ($("garayeToTime").value) params.set("time_to", $("garayeToTime").value);
  if (!state.people.length) {
    try { state.people = await api("/admin/api/people"); } catch (_) {}
  }
  const [data] = await Promise.all([
    api(`/admin/api/garaye-insights?${params}`),
    loadGarayeOverviewStats(),
  ]);
  state.garayeInsights = data;
  state.garayeSpeakerFocus = "";
  renderTopicTrend(data.topic_chart || {});
  renderGarayeWordCloud(data.word_cloud || []);
  observeGarayeWordCloud();
  renderGarayeSpeakerTrends();
  const trendWords = data.word_trends || [];
  const selected = trendWords.some((item) => item.name === state.garayeWordTrendWord)
    ? state.garayeWordTrendWord
    : (trendWords[0]?.name || "");
  state.garayeWordTrendWord = selected;
  $("garayeWordTrendSelect").innerHTML = trendWords.length
    ? trendWords.map((item) => `<option value="${esc(item.name)}" ${item.name === selected ? "selected" : ""}>${esc(item.name)} · ${n(item.count)}</option>`).join("")
    : '<option value="">واژه‌ای موجود نیست</option>';
  renderGarayeWordTrend();
}

function layoutTreemap(items, width, height) {
  const nodes = (items || [])
    .map((item) => ({name: item.name, value: Math.max(Number(item.value || 0), 0)}))
    .filter((item) => item.value > 0 && item.name)
    .sort((a, b) => b.value - a.value);
  const out = [];
  const split = (list, x, y, w, h) => {
    if (!list.length || w < 0.5 || h < 0.5) return;
    if (list.length === 1) {
      out.push({...list[0], x, y, w, h});
      return;
    }
    const sum = list.reduce((total, item) => total + item.value, 0) || 1;
    const half = sum / 2;
    let acc = 0;
    let index = 0;
    for (; index < list.length; index += 1) {
      acc += list[index].value;
      if (acc >= half) {
        index += 1;
        break;
      }
    }
    if (index <= 0) index = 1;
    if (index >= list.length) index = list.length - 1;
    const first = list.slice(0, index);
    const rest = list.slice(index);
    const firstSum = first.reduce((total, item) => total + item.value, 0);
    const ratio = Math.min(0.86, Math.max(0.14, firstSum / sum));
    if (w >= h) {
      split(first, x, y, w * ratio, h);
      split(rest, x + w * ratio, y, w * (1 - ratio), h);
    } else {
      split(first, x, y, w, h * ratio);
      split(rest, x, y + h * ratio, w, h * (1 - ratio));
    }
  };
  split(nodes, 0, 0, width, height);
  return out;
}

function garayeTreemapItems() {
  const cloud = state.garayeInsights?.word_cloud || [];
  if (cloud.length) {
    return cloud.slice(0, 48).map((item) => ({name: item.word || item.name, value: Number(item.count || 0)}));
  }
  return (state.garayeInsights?.word_trends || []).slice(0, 48).map((item) => ({
    name: item.name,
    value: Number(item.count || 0),
  }));
}

function renderGarayeWordTreemap() {
  const host = $("garayeWordTreemap");
  if (!host) return;
  const items = garayeTreemapItems();
  if (!items.length) {
    host.innerHTML = `<div class="garaye-treemap-empty">واژه‌ای برای نقشهٔ مستطیلی در این بازه نیست.</div>`;
    return;
  }
  const width = 1000;
  const height = 620;
  const maxValue = Math.max(1, ...items.map((item) => Number(item.value || 0)));
  const selected = state.garayeWordTrendWord;
  const cells = layoutTreemap(items, width, height);
  host.innerHTML = cells.map((cell) => {
    const share = Number(cell.value || 0) / maxValue;
    const fill = `hsl(168 ${42 + share * 28}% ${86 - share * 48}%)`;
    const ink = share > 0.45 ? "#f7fffe" : "#073c3d";
    const focused = selected && cell.name === selected;
    const dimmed = selected && cell.name !== selected;
    const showLabel = cell.w > 78 && cell.h > 42;
    const showCount = cell.w > 96 && cell.h > 62;
    return `<button type="button" class="garaye-treemap-cell${focused ? " is-focused" : ""}${dimmed ? " is-dimmed" : ""}" data-word="${esc(cell.name)}" title="${esc(cell.name)} · ${n(cell.value)}" style="left:${(cell.x / width) * 100}%;top:${(cell.y / height) * 100}%;width:${(cell.w / width) * 100}%;height:${(cell.h / height) * 100}%;background:${fill};color:${ink}">${showLabel ? `<b>${esc(cell.name)}</b>${showCount ? `<small>${n(cell.value)}</small>` : ""}` : ""}</button>`;
  }).join("");
  host.querySelectorAll("[data-word]").forEach((cell) => {
    cell.addEventListener("click", () => {
      const word = cell.getAttribute("data-word") || "";
      state.garayeWordTrendWord = word;
      if ($("garayeWordTrendSelect")) $("garayeWordTrendSelect").value = word;
      renderGarayeWordTrend();
    });
  });
}

function renderGarayeWordTrend() {
  const data = state.garayeInsights || {};
  const totals = data.daily_word_totals || [];
  const chartDays = totals.length
    ? totals.map((item) => item.date)
    : [...new Set((data.word_trends || []).flatMap((item) => (item.series || []).map((row) => row.date)))].sort();
  const series = alignSeriesToDays(data.word_trends || [], chartDays);
  renderCurvedTrendChart("garayeWordTrendChart", "garayeWordTrendLegend", {
    days: chartDays,
    series,
    emptyText: "برای این بازه روند ابرواژگان ثبت نشده است.",
    ariaLabel: "نمودار منحنی روند روزانه ابرواژگان",
  });
  renderGarayeWordTreemap();
  $("garayeWordCloud")?.querySelectorAll(".garaye-word").forEach((button) => {
    button.classList.toggle("is-selected", button.getAttribute("data-word") === state.garayeWordTrendWord);
  });
}

function setGarayeSpeakerFocus(name) {
  const next = String(name || "").trim();
  state.garayeSpeakerFocus = state.garayeSpeakerFocus === next ? "" : next;
  renderGarayeSpeakerTrends();
}

function renderGarayeSpeakerTrends() {
  const data = state.garayeInsights || {};
  const days = (data.daily_finalized || []).map((item) => item.date);
  const series = alignSeriesToDays(data.speaker_trends || [], days);
  renderCurvedTrendChart("garayeSpeakerChart", "garayeSpeakerLegend", {
    days,
    series,
    emptyText: "هنوز گویندهٔ مشخصی در این بازه نیست.",
    ariaLabel: "نمودار منحنی ترند گویندگان",
    focusName: state.garayeSpeakerFocus,
    onSelect: setGarayeSpeakerFocus,
  });
  const box = $("garayeSpeakerPeople");
  if (!box) return;
  box.innerHTML = series.length ? series.map((item) => {
    const person = item.person_id
      ? (state.people || []).find((row) => Number(row.person_id) === Number(item.person_id))
      : findPersonBySpeakerName(item.name);
    const personId = person?.person_id || item.person_id;
    const initials = String(item.name || "؟").trim().split(/\s+/).slice(0, 2).map((part) => part[0]).join("") || "؟";
    const focused = state.garayeSpeakerFocus === item.name;
    const dimmed = state.garayeSpeakerFocus && !focused;
    const avatar = `<span class="person-avatar">${personId ? `<img src="/admin/portraits/${personId}" alt="${esc(item.name)}" onerror="this.remove()">` : ""}<i>${esc(initials)}</i></span>`;
    return `<article class="garaye-speaker-row${focused ? " is-focused" : ""}${dimmed ? " is-dimmed" : ""}" data-speaker-name="${esc(item.name)}">${avatar}<div><b>${esc(item.name)}</b><p>${esc(person?.position || person?.category || "گویندهٔ خبرهای نهایی")}</p></div><small>${n(item.total)} خبر</small></article>`;
  }).join("") : `<div class="empty-mini">هنوز گویندهٔ مشخصی در این بازه نیست.</div>`;
  box.querySelectorAll("[data-speaker-name]").forEach((row) => {
    row.addEventListener("click", () => setGarayeSpeakerFocus(row.getAttribute("data-speaker-name") || ""));
  });
}

function setGarayeRangePreset(preset, {load = false} = {}) {
  const today = new Date();
  const toParts = (value) => jalaliDateFromGregorian(value);
  const dateBefore = (count) => new Date(today.getTime() - count * 24 * 60 * 60 * 1000);
  const current = jalaliToday();
  let from = null;
  let to = toParts(today);
  if (preset === "today") from = to;
  if (preset === "week") {
    const daysSinceSaturday = (today.getDay() + 1) % 7;
    from = toParts(dateBefore(daysSinceSaturday));
  }
  if (preset === "last7") from = toParts(dateBefore(6));
  if (preset === "last30") from = toParts(dateBefore(29));
  if (preset === "last90") from = toParts(dateBefore(89));
  if (preset === "year") { from = {year: current.year, month: 1, day: 1}; to = current; }
  if (preset !== "custom") {
    $("garayeFrom").value = jalaliInputValue(from || current);
    $("garayeTo").value = jalaliInputValue(to || current);
    $("garayeFromTime").value = "00:00";
    $("garayeToTime").value = "23:59";
  }
  if (load && state.page === "garaye") loadGarayeInsights().catch((error) => toast(error.message, true));
}

function toggleEitanCreate(show) {
  state.eitanCreating = Boolean(show);
  const panel = $("eitanCreatePanel");
  if (panel) panel.hidden = !state.eitanCreating;
  if (state.eitanCreating) $("eitanAxisTitle")?.focus();
}

function renderEitanAxes() {
  const host = $("eitanAxisButtons");
  if (!host) return;
  host.innerHTML = "";
  (state.eitanAxes || []).forEach((axis) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `eitan-axis-btn${state.eitanAxisId === axis.axis_id ? " is-active" : ""}`;
    button.textContent = axis.title;
    button.addEventListener("click", () => selectEitanAxis(axis.axis_id).catch((error) => toast(error.message, true)));
    host.appendChild(button);
  });
  const add = document.createElement("button");
  add.type = "button";
  add.className = "eitan-axis-btn eitan-axis-add";
  add.textContent = "اضافه کردن محور جدید";
  add.addEventListener("click", () => toggleEitanCreate(true));
  host.appendChild(add);
}

function renderEitanMessages() {
  const grid = $("eitanMessageGrid");
  if (!grid) return;
  const items = state.eitanMessages || [];
  grid.innerHTML = items.length ? items.map((item) => {
    const text = item.text || item.caption || "[پیام رسانه‌ای]";
    const sender = senderLabel(item);
    const analysisComplete = item.ai_enrichment_status === "validated" || Boolean((item.speaker_tags || []).length);
    const timestamp = fdateParts(item.published_at || item.received_at || item.created_at);
    const origin = item.forwarded_origin_title || (item.forwarded_origin_username ? `@${item.forwarded_origin_username}` : "پیام مستقیم");
    const analysisTags = (item.speaker_tags || []).map((tag) => `
      <span class="status-pill approved" title="${esc(tag.specific_topic || "")}">
        ${esc(tag.speaker_name || "نامشخص")} · ${esc(tag.general_topic || "نامشخص")}
      </span>`).join("");
    return `<article class="message-card ${analysisComplete ? "analysis-complete" : ""}"${analysisComplete ? ' data-analysis-state="complete"' : ""}>
      <div class="message-head"><div class="message-source"><span class="source-avatar">${esc((item.source_chat_title || "خ").slice(0, 1))}</span><b>${esc(item.source_chat_title || item.source_chat_username || "منبع")}</b></div></div>
      <div><span class="status-pill ${esc(item.status)}">${statusLabel(item.status)}</span>${analysisComplete ? `<span class="analysis-status-tag">تحلیل‌شده</span>` : ""} ${analysisTags || (item.detected_person_name ? `<span class="status-pill">${esc(item.detected_person_name)}</span>` : "")}</div>
      <div class="message-text">${esc(text)}</div>
      <div class="trace-meta"><span>👤 ارسال‌کننده/کارشناس: <b>${esc(sender)}</b></span><span>📍 مبدأ فوروارد: <b>${esc(origin)}</b></span></div>
      <div class="message-meta"><span class="message-timestamp"><span>${esc(timestamp.date)}</span><time datetime="${esc(timestamp.raw)}">${esc(timestamp.time)}</time></span><span>${n(item.media_count)} رسانه · ${n(item.link_count)} لینک</span></div>
      <div class="message-actions"><button class="detail-btn" onclick="openMessage(${item.id})">جزئیات</button></div>
    </article>`;
  }).join("") : `<div class="panel empty-mini">${state.eitanAxisId ? "پیامی مطابق این محور پیدا نشد." : "محور را انتخاب کنید تا پیام‌ها نمایش داده شوند."}</div>`;
}

async function loadEitanMessages({append = false} = {}) {
  if (!state.eitanAxisId) {
    state.eitanMessages = [];
    state.eitanTotal = 0;
    state.eitanOffset = 0;
    if ($("eitanCount")) $("eitanCount").textContent = "محور را انتخاب کنید";
    $("eitanMoreBar")?.classList.add("hidden");
    renderEitanMessages();
    return;
  }
  const pageSize = 40;
  if (!append) {
    state.eitanOffset = 0;
    state.eitanMessages = [];
  }
  const data = await api(`/admin/api/eitan-axes/${encodeURIComponent(state.eitanAxisId)}/messages?limit=${pageSize}&offset=${state.eitanOffset}`);
  const incoming = data.items || [];
  state.eitanTotal = Number(data.total || 0);
  state.eitanMessages = append ? state.eitanMessages.concat(incoming) : incoming;
  state.eitanOffset = state.eitanMessages.length;
  const axisTitle = data.axis?.title || "محور";
  if ($("eitanCount")) $("eitanCount").textContent = `${n(state.eitanTotal)} پیام برای «${axisTitle}»`;
  if ($("eitanSelectionHint")) {
    $("eitanSelectionHint").textContent = `${n(data.terms_count || 0)} عبارت از فایل کلیدواژه‌ها و افراد شاخص در همهٔ پیام‌های سامانه جست‌وجو شد.`;
  }
  const moreBar = $("eitanMoreBar");
  if (moreBar) {
    const remaining = Math.max(0, state.eitanTotal - state.eitanMessages.length);
    moreBar.classList.toggle("hidden", remaining <= 0);
    if ($("eitanMoreHint")) {
      $("eitanMoreHint").textContent = remaining
        ? `${n(state.eitanMessages.length)} از ${n(state.eitanTotal)} پیام نمایش داده شده است.`
        : "";
    }
  }
  renderEitanMessages();
}

async function selectEitanAxis(axisId) {
  state.eitanAxisId = axisId;
  toggleEitanCreate(false);
  renderEitanAxes();
  await loadEitanMessages();
}

async function loadEitanGaraPage() {
  const data = await api("/admin/api/eitan-axes");
  state.eitanAxes = data.items || [];
  if (state.eitanAxisId && !state.eitanAxes.some((axis) => axis.axis_id === state.eitanAxisId)) {
    state.eitanAxisId = "";
    state.eitanMessages = [];
  }
  renderEitanAxes();
  if (state.eitanAxisId) await loadEitanMessages();
  else renderEitanMessages();
}

async function submitEitanCreate(event) {
  event.preventDefault();
  const title = $("eitanAxisTitle")?.value.trim() || "";
  const keywords = $("eitanKeywordsFile")?.files?.[0];
  const people = $("eitanPeopleFile")?.files?.[0];
  if (!title) {
    toast("عنوان محور را وارد کنید.", true);
    return;
  }
  if (!keywords || !people) {
    toast("هر دو فایل کلیدواژه‌ها و افراد شاخص را انتخاب کنید.", true);
    return;
  }
  const body = new FormData();
  body.append("title", title);
  body.append("keywords_file", keywords);
  body.append("people_file", people);
  const created = await api("/admin/api/eitan-axes", {method: "POST", body});
  toast(`محور «${created.title}» ثبت شد.`);
  $("eitanCreatePanel")?.reset();
  toggleEitanCreate(false);
  await loadEitanGaraPage();
  if (created.axis_id) await selectEitanAxis(created.axis_id);
}

async function loadSources(silent = false) {
  const [sources, crawler] = await Promise.all([
    api("/admin/api/sources"),
    api("/admin/api/crawler"),
  ]);
  state.sources = sources;
  $("streamSource").innerHTML = `<option value="">همه منابع</option>` + state.sources.map((item) => `<option value="${item.chat_id ?? ""}">${esc(item.title || item.username || item.chat_id)}</option>`).join("");
  const sourceTypeLabels = {channel: "کانال پایش", group: "گروه", supergroup: "سوپرگروه"};
  $("sourcesGrid").innerHTML = state.sources.length ? state.sources.map((item) => {
    const lastSuccess = item.last_success_at || item.last_received_at;
    const error = String(item.last_error || "").trim();
    const health = error
      ? `<br><span class="source-receipt-error" title="${esc(error)}">آخرین خطا: ${esc(error.length > 160 ? `${error.slice(0, 160)}…` : error)}</span>`
      : "";
    const canEditSources = (state.me?.permissions || []).some((permission) => permission === "*" || permission === "sources.manage");
    const titleEditor = canEditSources
      ? `<div class="source-title-edit"><input data-source-title="${item.id}" value="${esc(item.title || item.username || "")}" placeholder="عنوان یا نام منبع"><button type="button" class="outline-button" onclick="saveSourceTitle(${item.id})">ثبت عنوان</button></div>`
      : "";
    return `
    <article class="source-card">
      <div class="source-card-top"><div><h4>${esc(item.title || item.username || "منبع")}</h4><span class="status-pill ${Number(item.enabled) ? "approved" : "rejected"}">${Number(item.enabled) ? "فعال" : "غیرفعال"}</span></div>
      <button class="switch ${Number(item.enabled) ? "on" : "off"}" onclick="toggleSource(${item.id},${!Number(item.enabled)})">${Number(item.enabled) ? "روشن" : "خاموش"}</button></div>
      <p>${item.username ? `@${esc(item.username)}` : `شناسه: ${esc(item.chat_id)}`}<br>نوع: ${sourceTypeLabels[item.chat_type] || "منبع پایش"}<br>آخرین دریافت موفق: ${fdate(lastSuccess)}${health}</p>
      ${titleEditor}
    </article>`;
  }).join("") : `<div class="empty-mini">هنوز منبعی ثبت نشده است.</div>`;
  $("crawlerChannels").value = (crawler.channels || []).join("\n");
  $("crawlerBadge").className = `status-pill ${crawler.process_running ? "approved" : "pending"}`;
  $("crawlerBadge").textContent = crawler.process_running
    ? (crawler.enabled ? "در حال کرول" : "در حال توقف امن")
    : (crawler.enabled ? "آمادهٔ اجرا" : "متوقف");
  $("crawlerHelp").textContent =
    `${n((crawler.channels || []).length)} کانال · هر ${n(Math.round((crawler.repeat_seconds || 1800) / 60))} دقیقه · ` +
    `پروفایل Firefox: ${crawler.firefox_profile_configured ? "تنظیم‌شده" : "تشخیص خودکار"} · ` +
    `مقصد: ${crawler.destination_configured ? "تنظیم‌شده" : "تنظیم‌نشده"}`;
  const crawlerToggle = $("crawlerEnabledToggle");
  crawlerToggle.className = `switch ${crawler.enabled ? "on" : "off"}`;
  crawlerToggle.textContent = crawler.enabled ? "روشن" : "خاموش";
  crawlerToggle.setAttribute("aria-pressed", String(Boolean(crawler.enabled)));
  crawlerToggle.dataset.enabled = String(Boolean(crawler.enabled));
  crawlerToggle.title = crawler.process_running
    ? "تغییر وضعیت در دور جاری نیز بررسی می‌شود."
    : "برای اجرای Selenium از دکمهٔ «اجرای کرولر» استفاده کنید.";
  $("startCrawler").disabled = Boolean(crawler.process_running && crawler.enabled);
  $("stopCrawler").disabled = !crawler.process_running && !crawler.enabled;
  if (!silent) $("connectionLabel").textContent = `${n(state.sources.filter((item) => Number(item.enabled)).length)} منبع فعال`;
}

async function toggleSource(id, enabled) {
  try {
    await api(`/admin/api/sources/${id}`, {method: "PATCH", body: JSON.stringify({enabled})});
    toast("وضعیت منبع تغییر کرد.");
    await loadSources();
  } catch (error) { toast(error.message, true); }
}

async function saveSourceTitle(id) {
  const input = document.querySelector(`[data-source-title="${id}"]`);
  const title = input?.value.trim() || "";
  if (!title) return toast("عنوان یا نام منبع را وارد کنید.", true);
  try {
    await api(`/admin/api/sources/${id}`, {method: "PATCH", body: JSON.stringify({title})});
    toast("عنوان منبع به‌روزرسانی شد.");
    await loadSources(true);
  } catch (error) { toast(error.message, true); }
}

async function toggleCrawlerRuntime() {
  const button = $("crawlerEnabledToggle");
  const enabled = button.dataset.enabled !== "true";
  button.disabled = true;
  try {
    await api("/admin/api/crawler/enabled", {
      method: "PATCH", body: JSON.stringify({enabled}),
    });
    toast(enabled ? "کرول خودکار فعال شد." : "کرول خودکار متوقف شد.");
    await loadSources(true);
  } catch (error) { toast(error.message, true); }
  finally { button.disabled = false; }
}

async function startCrawlerFromDashboard() {
  const button = $("startCrawler");
  const oldText = button.textContent;
  button.disabled = true;
  button.textContent = "در حال شروع…";
  try {
    const result = await api("/admin/api/crawler/start", {method: "POST"});
    toast(result.message || (result.started ? "کرولر Selenium اجرا شد." : "کرولر از قبل فعال بود."));
    await loadSources(true);
  } catch (error) { toast(error.message, true); }
  finally { button.textContent = oldText; }
}

async function stopCrawlerFromDashboard() {
  const button = $("stopCrawler");
  const oldText = button.textContent;
  button.disabled = true;
  try {
    const result = await api("/admin/api/crawler/stop", {method: "POST"});
    toast(result.message || "توقف امن کرولر ثبت شد.");
    await loadSources(true);
  } catch (error) { toast(error.message, true); }
  finally { button.textContent = oldText; }
}

async function loadStream({append = false} = {}) {
  if (!state.sources.length) await loadSources(true);
  const pageSize = 200;
  if (!append) {
    state.streamOffset = 0;
    state.messages = [];
  }
  const params = streamQueryParams({limit: String(pageSize), offset: String(state.streamOffset)});
  const data = await api(`/admin/api/messages?${params}`);
  const incoming = data.items || [];
  state.streamTotal = Number(data.total || 0);
  state.messages = append ? state.messages.concat(incoming) : incoming;
  state.streamOffset = state.messages.length;
  $("streamCount").textContent = `${n(state.streamTotal)} پیام`;
  const moreBar = $("streamMoreBar");
  if (moreBar) {
    const remaining = Math.max(0, state.streamTotal - state.messages.length);
    moreBar.classList.toggle("hidden", remaining <= 0);
    $("streamMoreHint").textContent = remaining
      ? `${n(state.messages.length)} از ${n(state.streamTotal)} خبر همین فیلتر نمایش داده شده است.`
      : "";
  }
  renderMessages();
  updateStreamSelectionHint();
}

function streamQueryParams(extra = {}) {
  const params = new URLSearchParams(extra);
  if ($("streamQuery")?.value.trim()) params.set("q", $("streamQuery").value.trim());
  if ($("streamStatus")?.value) params.set("status", $("streamStatus").value);
  if ($("streamSource")?.value) params.set("source_chat_id", $("streamSource").value);
  if ($("streamFrom")?.value.trim()) params.set("date_from_jalali", $("streamFrom").value.trim());
  if ($("streamTo")?.value.trim()) params.set("date_to_jalali", $("streamTo").value.trim());
  const timeFrom = clock24($("streamTimeFrom")?.value || "");
  const timeTo = clock24($("streamTimeTo")?.value || "");
  if (timeFrom) params.set("time_from", timeFrom);
  if (timeTo) params.set("time_to", timeTo);
  return params;
}

function removeLegacyModerationControls(root = document) {
  // The current renderer never creates these controls. Removing them here is
  // a defensive guard against an old cached fragment exposing the retired
  // approval/rejection flow.
  root.querySelectorAll(
    ".approve-btn,.reject-btn,[data-message-action='approve'],[data-message-action='reject']"
  ).forEach((button) => button.remove());
}

function renderMessages() {
  $("messageGrid").innerHTML = state.messages.length ? state.messages.map((item) => {
    const text = item.text || item.caption || "[پیام رسانه‌ای]";
    const selected = state.selectedMessages.has(item.id);
    const sender = senderLabel(item);
    const analysisComplete = item.ai_enrichment_status === "validated" || Boolean((item.speaker_tags || []).length);
    const timestamp = fdateParts(item.published_at || item.received_at || item.created_at);
    const origin = item.forwarded_origin_title || (item.forwarded_origin_username ? `@${item.forwarded_origin_username}` : "پیام مستقیم");
    const analysisTags = (item.speaker_tags || []).map((tag) => `
      <span class="status-pill approved" title="${esc(tag.specific_topic || "")}">
        ${esc(tag.speaker_name || "نامشخص")} · ${esc(tag.general_topic || "نامشخص")}
      </span>`).join("");
    return `<article class="message-card ${selected ? "selected" : ""} ${analysisComplete ? "analysis-complete" : ""}"${analysisComplete ? ' data-analysis-state="complete"' : ""}>
      <div class="message-head"><div class="message-source"><span class="source-avatar">${esc((item.source_chat_title || "خ").slice(0, 1))}</span><b>${esc(item.source_chat_title || item.source_chat_username || "منبع")}</b></div><input class="select-check" type="checkbox" ${selected ? "checked" : ""} onchange="selectMessage(${item.id},this.checked)" aria-label="انتخاب پیام"></div>
      <div><span class="status-pill ${esc(item.status)}">${statusLabel(item.status)}</span>${analysisComplete ? `<span class="analysis-status-tag">تحلیل‌شده</span>` : ""} ${analysisTags || (item.detected_person_name ? `<span class="status-pill">${esc(item.detected_person_name)}</span>` : "")}</div>
      <div class="message-text">${esc(text)}</div>
      <div class="trace-meta"><span>👤 ارسال‌کننده/کارشناس: <b>${esc(sender)}</b></span><span>📍 مبدأ فوروارد: <b>${esc(origin)}</b></span></div>
      <div class="message-meta"><span class="message-timestamp"><span>${esc(timestamp.date)}</span><time datetime="${esc(timestamp.raw)}">${esc(timestamp.time)}</time></span><span>${n(item.media_count)} رسانه · ${n(item.link_count)} لینک</span></div>
      <div class="message-actions"><button class="detail-btn" onclick="openMessage(${item.id})">جزئیات</button></div>
    </article>`;
  }).join("") : `<div class="panel empty-mini">پیامی مطابق فیلترها پیدا نشد.</div>`;
  removeLegacyModerationControls($("messageGrid"));
}

function selectMessage(id, selected) {
  selected ? state.selectedMessages.add(id) : state.selectedMessages.delete(id);
  renderMessages();
  updateStreamSelectionHint();
}

function updateStreamSelectionHint() {
  const count = state.selectedMessages.size;
  const visibleIds = state.messages.map((item) => Number(item.id)).filter(Number.isFinite);
  $("streamSelectionHint").textContent = count
    ? `${n(count)} پیام برای تحلیل با مدل اول انتخاب شده است.`
    : "پیام‌های موردنظر را برای تحلیل با مدل اول انتخاب کنید.";
  const selectAll = $("selectVisibleMessages");
  const clear = $("clearMessageSelection");
  const allMatchingSelected = count > 0 && count >= Number(state.streamTotal || 0) && Number(state.streamTotal || 0) > 0;
  selectAll.disabled = !Number(state.streamTotal || visibleIds.length) || allMatchingSelected;
  selectAll.textContent = "انتخاب همه";
  clear.disabled = count === 0;
}

async function selectVisibleMessages() {
  const button = $("selectVisibleMessages");
  const oldText = button.textContent;
  button.disabled = true;
  button.textContent = "در حال انتخاب…";
  try {
    const result = await api(`/admin/api/messages/ids?${streamQueryParams()}`);
    const ids = (result.ids || []).map((id) => Number(id)).filter(Number.isFinite);
    state.selectedMessages = new Set(ids);
    renderMessages();
    updateStreamSelectionHint();
    const total = Number(result.total || ids.length);
    if (total > ids.length) {
      toast(`${n(ids.length)} پیام از ${n(total)} پیام مطابق فیلتر انتخاب شد.`);
    }
  } catch (error) {
    toast(error.message, true);
  } finally {
    button.disabled = false;
    button.textContent = oldText;
  }
}

function clearMessageSelection() {
  state.selectedMessages.clear();
  renderMessages();
  updateStreamSelectionHint();
}

async function analyzeSelectedMessages() {
  const ids = [...state.selectedMessages];
  if (!ids.length) return toast("حداقل یک پیام را برای تحلیل انتخاب کنید.", true);
  const button = $("analyzeSelected");
  const oldText = button.textContent;
  button.disabled = true;
  button.textContent = "در حال تحلیل…";
  try {
    const chunkSize = 200;
    let succeeded = 0;
    let failed = 0;
    let parallelism = 0;
    for (let index = 0; index < ids.length; index += chunkSize) {
      const batch = ids.slice(index, index + chunkSize);
      button.textContent = `در حال تحلیل ${n(index + 1)} تا ${n(Math.min(index + batch.length, ids.length))} از ${n(ids.length)}…`;
      const result = await api("/admin/api/messages/analyze", {
        method: "POST",
        body: JSON.stringify({message_ids: batch}),
      });
      succeeded += Number(result.succeeded || 0);
      failed += Number(result.failed || 0);
      parallelism = Math.max(parallelism, Number(result.parallelism || 0));
    }
    state.selectedMessages.clear();
    await loadStream();
    const parallelNote = parallelism > 1
      ? ` با ${n(parallelism)} درخواست هم‌زمان`
      : "";
    const message = failed
      ? `${n(succeeded)} پیام${parallelNote} تحلیل شد و ${n(failed)} پیام خطا داشت.`
      : `تحلیل ${n(succeeded)} پیام${parallelNote} ذخیره شد و در نهایی‌سازی در دسترس است.`;
    toast(message, Boolean(failed));
  } catch (error) {
    toast(error.message, true);
  } finally {
    button.disabled = false;
    button.textContent = oldText;
  }
}

async function openMessage(id, allowSpeakerCorrection = false) {
  try {
    const item = await api(`/admin/api/messages/${id}`);
    state.messageSearchTexts.set(Number(id), String(item.text || item.caption || "").trim());
    const publicPostUrl = [item.forwarded_origin_url, item.message_url].find(isExactBalePostUrl);
    const links = [
      ["پیام ثبت‌شده در منبع", isExactBalePostUrl(item.message_url) ? item.message_url : null],
      ["منبع تکمیلی", item.primary_source_url],
    ].filter((entry, index, all) => entry[1] && all.findIndex((candidate) => candidate[1] === entry[1]) === index);
    const publicPostLink = publicPostUrl
      ? `<a class="primary-message-link" href="${esc(publicPostUrl)}" target="_blank" rel="noopener">پیام در کانال اصلی ↗</a>`
      : `<span class="link-unavailable">پیوند عمومیِ دقیق این پیام در دسترس نیست.</span>`;
    const sender = senderLabel(item);
    const senderUsername = item.sender_kind !== "chat" && item.sender_username ? `@${String(item.sender_username).replace(/^@/, "")}` : "";
    const origin = item.forwarded_origin_title || (item.forwarded_origin_username ? `@${item.forwarded_origin_username}` : "پیام مستقیم");
    const speakerTags = (item.speaker_tags || []).map((tag) => `
      <div class="detail-field">
        <small>گوینده و تگ تحلیل‌شده</small>
        <b>${esc(tag.speaker_name || "نامشخص")}</b>
        ${tag.position ? ` · ${esc(tag.position)}` : ""}
        <br>${esc(tag.general_topic || "نامشخص")} ← ${esc(tag.specific_topic || "نامشخص")}
        ${tag.evidence ? `<br><small>شاهد: ${esc(tag.evidence)}</small>` : ""}
        ${tag.expression_method_type && tag.expression_method_type !== "نامشخص" ? `<br><small>محل بیان: ${esc([tag.expression_method_type, tag.expression_method_context].filter((value) => value && value !== "نامشخص").join(" · "))}</small>` : ""}
      </div>`).join("");
    const isEvent = String(item.analysis_content_type || "") === "event";
    const analysisKind = isEvent ? "رویداد مهم ایران و جهان" : "اظهارنظر شخص‌محور";
    const eventInfo = isEvent ? `
      <div class="detail-field"><small>عنوان رویداد</small>${esc(item.analysis_event_title || item.analysis_main_subject || "—")}</div>
      <div class="detail-field"><small>مکان و زمان رویداد</small>${esc([item.analysis_event_location, item.analysis_event_time].filter((value) => value && value !== "نامشخص").join(" · ") || "—")}</div>` : "";
    const correction = allowSpeakerCorrection && !isEvent ? `
      <form class="speaker-correction-form" onsubmit="correctAnalyzedSpeaker(event,${Number(id)})">
        <div><span class="eyebrow">اصلاح گوینده</span><h4>این پیام را زیر گویندهٔ درست قرار دهید</h4><p>پس از تأیید، پیام از فهرست گویندهٔ فعلی حذف و زیر نام جدید نمایش داده می‌شود.</p></div>
        <div class="form-grid two">
          <label>نام گوینده<input id="correctSpeakerName" required value="${esc(item.detected_person_name || item.speaker_tags?.[0]?.speaker_name || "")}"></label>
          <label>سمت<input id="correctSpeakerPosition" value="${esc(item.speaker_tags?.[0]?.position || "")}" placeholder="در صورت نیاز اصلاح کنید"></label>
        </div>
        <button type="submit" class="primary-button">تأیید اصلاح گوینده</button>
      </form>` : "";
    // Text first, then editorial details. Raw metadata/analysis JSON stays hidden from operators.
    $("messageDetail").innerHTML = `
      <div class="detail-text">${esc(item.text || item.caption || "[پیام بدون متن]")}</div>
      <div class="detail-links">${publicPostLink}${links.map(([label, link]) => `<a href="${esc(link)}" target="_blank" rel="noopener">${esc(label)} ↗</a>`).join("")}<button type="button" class="outline-button" onclick="searchMessageInGoogle(${Number(id)})">جست‌وجوی کامل متن در گوگل</button></div>
      <div class="detail-grid">
        <div class="detail-field"><small>منبع</small>${esc(item.source_chat_title || item.source_chat_username || item.source_chat_id)}</div>
        <div class="detail-field"><small>زمان انتشار</small>${fdate(item.published_at || item.received_at)}</div>
        <div class="detail-field"><small>ارسال‌کننده/کارشناس</small>${esc(sender)}${senderUsername ? ` · ${esc(senderUsername)}` : ""}</div>
        <div class="detail-field"><small>کانال مبدأ فوروارد</small>${esc(origin)}</div>
        <div class="detail-field"><small>امتیاز خودکار</small>${item.editorial_rating ? `${n(item.editorial_rating)} از ۵` : "—"}</div>
        <div class="detail-field"><small>شخص تشخیص‌داده‌شده</small>${esc(item.detected_person_name || "—")}</div>
        <div class="detail-field"><small>موضوع</small>${esc(item.detected_topic_name || "—")}</div>
        <div class="detail-field"><small>نوع محتوا</small>${analysisKind}</div>
        ${!isEvent && item.statement_location_label ? `<div class="detail-field"><small>محل بیان</small>${esc(item.statement_location_label)}</div>` : ""}
        ${eventInfo}
        ${speakerTags}
      </div>
      ${correction}`;
    $("messageDialog").showModal();
  } catch (error) { toast(error.message, true); }
}

function searchMessageInGoogle(messageId) {
  const row = state.analyzedMessages.find((item) => Number(item.id) === Number(messageId));
  const text = String(
    state.messageSearchTexts.get(Number(messageId))
    || row?.text
    || row?.caption
    || "",
  ).trim();
  if (!text) return toast("متن قابل جست‌وجویی برای این پیام وجود ندارد.", true);
  const url = `https://www.google.com/search?q=${encodeURIComponent(text)}`;
  const popup = window.open(url, "garaye-google-search", "popup=yes,width=1120,height=780,resizable=yes,scrollbars=yes");
  if (!popup) window.open(url, "_blank", "noopener");
}

async function correctAnalyzedSpeaker(event, messageId) {
  event.preventDefault();
  const speakerName = $("correctSpeakerName").value.trim();
  if (!speakerName) return toast("نام گوینده را وارد کنید.", true);
  const submit = event.currentTarget.querySelector("button[type='submit']");
  const oldText = submit.textContent;
  submit.disabled = true;
  submit.textContent = "در حال ثبت…";
  try {
    await api(`/admin/api/analysis/messages/${messageId}/speaker`, {
      method: "PUT",
      body: JSON.stringify({
        speaker_name: speakerName,
        position: $("correctSpeakerPosition").value.trim() || null,
      }),
    });
    $("messageDialog").close();
    await loadAnalysisFilters();
    toast("گوینده اصلاح شد؛ پیام اکنون زیر گویندهٔ جدید در دسترس است.");
  } catch (error) {
    toast(error.message, true);
  } finally {
    submit.disabled = false;
    submit.textContent = oldText;
  }
}

async function loadReferenceData() {
  const [people, topics, categories] = await Promise.all([
    api("/admin/api/people"),
    api("/admin/api/topics"),
    api("/admin/api/people/categories").catch(() => ({ options: [] })),
  ]);
  state.people = people;
  state.personCategories = categories.options || [];
  fillPersonCategorySelects();
  state.topics = topics;
  $("draftPerson").innerHTML = `<option value="">انتخاب شخص</option>` + people.map((item) => `<option value="${item.person_id}">${esc(item.full_name)}${item.position ? ` — ${esc(item.position)}` : ""}</option>`).join("");
  $("draftTopic").innerHTML = `<option value="">انتخاب موضوع</option>` + topics.map((item) => `<option value="${item.topic_id}">${esc(item.name)}</option>`).join("");
}

async function loadFinalizationWorkspace() {
  if (!state.people.length || !state.topics.length) await loadReferenceData();
  renderFinalizationView();
  if (state.finalizationView === "drafts") await loadDrafts();
}

function renderFinalizationView() {
  if (isQuickStartWorkspace()) {
    $("finalizationRangeView")?.classList.add("hidden");
    $("finalizationDeskView")?.classList.remove("hidden");
    $("finalizationDrafts")?.classList.remove("hidden");
    return;
  }
  const wanted = state.finalizationView === "drafts"
    ? "finalizationDrafts"
    : (state.finalizationStage === "desk" && state.finalizationWindowConfirmed
      ? "finalizationDeskView"
      : "finalizationRangeView");
  document.querySelectorAll(".finalization-view").forEach((element) => {
    element.classList.toggle("hidden", element.id !== wanted);
  });
  if (wanted === "finalizationDeskView") {
    const dateFrom = $("finalizationDateFrom")?.value.trim() || "—";
    const timeFrom = faDigits(clock24($("finalizationTimeFrom")?.value || "") || "00:00");
    const dateTo = $("finalizationDateTo")?.value.trim() || dateFrom;
    const timeTo = faDigits(clock24($("finalizationTimeTo")?.value || "") || "23:59");
    $("finalizationDeskHint").textContent = `بازهٔ فعال: ${dateFrom} ${timeFrom} تا ${dateTo} ${timeTo}. فهرست زیر فقط از همین بازه ساخته شده است.`;
  }
}

async function loadAutomationWorkspace() {
  await Promise.all([loadEditorialAutomation(), loadHumanControl(), loadSources(true)]);
}

function renderFinalizationProgress(progress) {
  const panel = $("finalizationProgress");
  if (!panel) return;
  if (!progress || !state.finalizationWindowConfirmed) {
    panel.classList.add("hidden");
    panel.innerHTML = "";
    return;
  }
  const totalPeople = Number(progress.total_people || 0);
  const remainingPeople = Number(progress.remaining_people || 0);
  const totalEvents = Number(progress.total_events || 0);
  const remainingEvents = Number(progress.remaining_events || 0);
  const totalUnits = totalPeople + totalEvents;
  const remainingUnits = remainingPeople + remainingEvents;
  const completedUnits = Math.max(0, totalUnits - remainingUnits);
  const percent = totalUnits ? Math.round(completedUnits / totalUnits * 100) : 100;
  panel.classList.remove("hidden");
  panel.innerHTML = `
    <div class="finalization-progress-copy">
      <span class="eyebrow">پیشرفت میز تدوین</span>
      <b>${n(percent)}٪ تکمیل شده</b>
      <small>${n(remainingPeople)} از ${n(totalPeople)} گوینده و ${n(remainingEvents)} از ${n(totalEvents)} رویداد برای نهایی‌سازی باقی مانده است.</small>
    </div>
    <div class="finalization-progress-track" role="progressbar" aria-label="پیشرفت میز تدوین" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${percent}"><i style="width:${percent}%"></i></div>`;
}

async function loadAnalysisFilters() {
  const previousSpeaker = $("finalizationSpeaker").value;
  const previousEvent = $("finalizationEvent").value;
  const previousGeneral = $("finalizationGeneralTopic").value;
  const previousSpecific = $("finalizationSpecificTopic").value;
  const dateFrom = $("finalizationDateFrom").value.trim();
  const dateTo = $("finalizationDateTo").value.trim() || dateFrom;
  if (!dateFrom || !dateTo) throw new Error("روز و ساعت شروع و پایان بازه را وارد کنید.");
  const params = jalaliWindowParams("finalizationDateFrom", "finalizationTimeFrom", "finalizationDateTo", "finalizationTimeTo");
  state.analysisFilters = await api(`/admin/api/analysis/filters?${params}`);
  $("finalizationSpeaker").innerHTML =
    `<option value="">ابتدا یک گوینده انتخاب کنید</option>`
    + (state.analysisFilters.speakers || []).map((item) => `
      <option value="${esc(item.speaker_name)}">
        ${esc(item.speaker_name)} · ${n(item.message_count)} خبر
      </option>`).join("");
  $("finalizationEvent").innerHTML =
    `<option value="">یک رویداد مستقل را انتخاب کنید</option>`
    + (state.analysisFilters.events || []).map((item) => `
      <option value="${Number(item.message_id)}">
        ${esc(item.event_title)} · ${esc(item.general_topic || "نامشخص")}
      </option>`).join("");
  $("finalizationGeneralTopic").innerHTML =
    `<option value="">همه موضوعات کلی</option>`
    + (state.analysisFilters.general_topics || []).map((item) => `
      <option value="${esc(item.general_topic)}">
        ${esc(item.general_topic)} · ${n(item.message_count)}
      </option>`).join("");
  $("finalizationSpecificTopic").innerHTML =
    `<option value="">همه موضوعات مشخص</option>`
    + (state.analysisFilters.specific_topics || []).map((item) => `
      <option value="${esc(item.specific_topic)}">
        ${esc(item.specific_topic)} · ${n(item.message_count)}
      </option>`).join("");
  if ([...$("finalizationSpeaker").options].some((option) => option.value === previousSpeaker)) {
    $("finalizationSpeaker").value = previousSpeaker;
  }
  if ([...$("finalizationEvent").options].some((option) => option.value === previousEvent)) {
    $("finalizationEvent").value = previousEvent;
  }
  if ([...$("finalizationGeneralTopic").options].some((option) => option.value === previousGeneral)) {
    $("finalizationGeneralTopic").value = previousGeneral;
  }
  if ([...$("finalizationSpecificTopic").options].some((option) => option.value === previousSpecific)) {
    $("finalizationSpecificTopic").value = previousSpecific;
  }
  ["finalizationSpeaker", "finalizationEvent", "finalizationGeneralTopic", "finalizationSpecificTopic"].forEach((id) => { $(id).disabled = false; });
  state.finalizationWindowConfirmed = true;
  state.finalizationStage = "desk";
  $("finalizationWindowHint").textContent = "بازه تأیید شد؛ گویندگان، تگ‌ها و رویدادهای مستقل فقط از همین بازه نمایش داده می‌شوند.";
  renderFinalizationProgress(state.analysisFilters.progress);
  await loadAnalyzedMessages();
  renderFinalizationView();
}

function invalidateFinalizationWindow() {
  state.finalizationWindowConfirmed = false;
  state.finalizationStage = "range";
  state.analyzedMessages = [];
  state.selectedAnalyzedMessages.clear();
  ["finalizationSpeaker", "finalizationEvent", "finalizationGeneralTopic", "finalizationSpecificTopic"].forEach((id) => {
    $(id).disabled = true;
  });
  $("finalizationSpeaker").innerHTML = `<option value="">ابتدا بازه را تأیید کنید</option>`;
  $("finalizationEvent").innerHTML = `<option value="">ابتدا بازه را تأیید کنید</option>`;
  $("finalizationGeneralTopic").innerHTML = `<option value="">همه موضوعات کلی</option>`;
  $("finalizationSpecificTopic").innerHTML = `<option value="">همه موضوعات مشخص</option>`;
  $("finalizationWindowHint").textContent = "ابتدا روز و ساعت بازه را تعیین و تأیید کنید؛ سپس فهرست اشخاص همان بازه نمایش داده می‌شود.";
  hideQuickRegistryPrompt();
  renderFinalizationProgress(null);
  resetDeskSubjectEditorTouch();
  hideDeskSubjectEditor();
  renderAnalyzedMessages();
  if (state.page === "finalization" && state.finalizationView === "workbench") renderFinalizationView();
}

function groupAnalyzedMessages(rows) {
  const grouped = new Map();
  for (const row of rows || []) {
    if (!grouped.has(row.id)) grouped.set(row.id, {...row, tags: []});
    if (row.tag_id != null) {
      grouped.get(row.id).tags.push({
        tag_id: row.tag_id,
        speaker_name: row.speaker_name,
        speaker_position: row.speaker_position,
        specific_topic: row.specific_topic,
        general_topic: row.general_topic,
        speaker_evidence: row.speaker_evidence,
        analyzed_person_id: row.analyzed_person_id,
      });
    }
  }
  return [...grouped.values()];
}

function combineJalaliDateTime(dateId, timeId) {
  const date = $(dateId).value.trim();
  const time = $(timeId).value.trim();
  return date ? `${date}${time ? ` ${time}` : ""}` : "";
}

function hideQuickRegistryPrompt() {
  $("detectedPersonRegistryPrompt").classList.add("hidden");
  $("quickRegistryPersonForm").dataset.tagId = "";
}

function hideDeskSubjectEditor() {
  $("deskSubjectEditor")?.classList.add("hidden");
  $("deskPersonFields")?.classList.add("hidden");
  $("deskEventFields")?.classList.add("hidden");
  $("deskNewPersonBox")?.classList.add("hidden");
  $("deskPersonMetaFields")?.classList.add("hidden");
  closeDeskPersonOptions();
}

function registryPeople() {
  return (state.people || []).filter((item) => Number(item.active) !== 0);
}

function closeDeskPersonOptions() {
  $("deskPersonOptions")?.classList.add("hidden");
}

function renderDeskPersonOptions(query = "") {
  const box = $("deskPersonOptions");
  if (!box) return;
  const needle = String(query || "").trim().toLowerCase();
  const people = registryPeople().filter((item) => {
    if (!needle) return true;
    const hay = `${item.full_name} ${item.position || ""} ${item.category || ""} ${item.aliases || ""}`.toLowerCase();
    return hay.includes(needle);
  });
  const selectedId = $("deskPersonId")?.value || "";
  const selectedNew = $("deskPersonMode")?.value === "new";
  box.innerHTML = [
    `<button type="button" class="desk-combobox-option desk-combobox-new ${selectedNew ? "active" : ""}" data-desk-person="new">شخص جدید</button>`,
    ...people.slice(0, 50).map((item) => {
      const initials = String(item.full_name || "؟").trim().split(/\s+/).slice(0, 2).map((part) => part[0]).join("") || "؟";
      return `<button type="button" class="desk-combobox-option ${String(item.person_id) === selectedId ? "active" : ""}" data-desk-person="${item.person_id}">
        <span class="person-avatar"><img src="/admin/portraits/${item.person_id}" alt="" onerror="this.remove()"><i>${esc(initials)}</i></span>
        <span><b>${esc(item.full_name)}</b><small>${esc([item.position, item.category].filter(Boolean).join(" · ") || "بدون سمت")}</small></span>
      </button>`;
    }),
  ].join("");
  box.classList.remove("hidden");
  box.querySelectorAll("[data-desk-person]").forEach((button) => {
    button.addEventListener("mousedown", (event) => {
      event.preventDefault();
      applyDeskPersonChoice(button.dataset.deskPerson, {fromUser: true});
    });
  });
}

function markDeskSubjectUnconfirmed() {
  state.deskSubjectConfirmed = false;
  const hint = $("deskSubjectConfirmHint");
  if (hint) hint.textContent = "برای ذخیره اصلاحات، ثبت مشخصات را بزنید.";
}

function markDeskSubjectConfirmed(message = "مشخصات ثبت شد.") {
  state.deskSubjectConfirmed = true;
  const hint = $("deskSubjectConfirmHint");
  if (hint) hint.textContent = message;
}

function applyDeskPersonChoice(value, {fromUser = true} = {}) {
  const search = $("deskPersonSearch");
  if (fromUser && search) search.dataset.touched = "1";
  if (value === "new") {
    $("deskPersonMode").value = "new";
    $("deskPersonId").value = "";
    if (search) search.value = "شخص جدید";
    $("deskNewPersonBox")?.classList.remove("hidden");
    $("deskPersonMetaFields")?.classList.remove("hidden");
    if ($("deskNewPersonName") && !$("deskNewPersonName").dataset.touched) {
      $("deskNewPersonName").value = $("finalizationSpeaker")?.value || "";
    }
    $("deskPersonName").value = $("deskNewPersonName")?.value.trim() || "";
  } else {
    const person = registryPeople().find((item) => String(item.person_id) === String(value));
    if (!person) return;
    $("deskPersonMode").value = "existing";
    $("deskPersonId").value = String(person.person_id);
    $("deskPersonName").value = person.full_name;
    if (search) search.value = person.full_name;
    $("deskNewPersonBox")?.classList.add("hidden");
    $("deskPersonMetaFields")?.classList.remove("hidden");
    if ($("deskPersonPosition") && !$("deskPersonPosition").dataset.touched) {
      $("deskPersonPosition").value = person.position || "";
    }
    if ($("deskPersonCategory") && !$("deskPersonCategory").dataset.touched) {
      fillPersonCategorySelects(person.category || "");
      $("deskPersonCategory").value = person.category || "";
    }
  }
  closeDeskPersonOptions();
  if (fromUser) markDeskSubjectUnconfirmed();
}

function renderDeskSubjectEditor() {
  const speaker = $("finalizationSpeaker").value;
  const eventMessageId = Number($("finalizationEvent").value) || null;
  if (!state.finalizationWindowConfirmed || (!speaker && !eventMessageId)) {
    hideDeskSubjectEditor();
    return;
  }
  $("deskSubjectEditor").classList.remove("hidden");
  if (eventMessageId) {
    const row = state.analyzedMessages.find((item) => Number(item.id) === eventMessageId) || state.analyzedMessages[0];
    const event = row?.event || {};
    $("deskSubjectHeading").textContent = "اصلاح مشخصات رویداد";
    $("deskSubjectHint").textContent = "عنوان، محل و زمان رویداد را بازبینی کنید و سپس ثبت مشخصات را بزنید.";
    $("deskPersonFields").classList.add("hidden");
    $("deskEventFields").classList.remove("hidden");
    $("deskNewPersonBox")?.classList.add("hidden");
    $("deskPersonMetaFields")?.classList.add("hidden");
    if (!$("deskEventTitle").dataset.touched) {
      $("deskEventTitle").value = event.title || row?.analysis_event_title || row?.analysis_main_subject || "";
    }
    if (!$("deskEventLocation").dataset.touched) {
      $("deskEventLocation").value = event.location || row?.analysis_event_location || "";
    }
    if (!$("deskEventTime").dataset.touched) {
      $("deskEventTime").value = event.time || row?.analysis_event_time || "";
    }
    return;
  }
  const speakerRecord = (state.analysisFilters.speakers || []).find((item) => item.speaker_name === speaker);
  const person = findPersonBySpeakerName(speaker);
  const tags = state.analyzedMessages.flatMap((item) => item.tags || []);
  const positionCounts = new Map();
  for (const tag of tags) {
    const position = String(tag.speaker_position || "").trim();
    if (position && position !== "نامشخص") positionCounts.set(position, (positionCounts.get(position) || 0) + 1);
  }
  const analyzedPosition = [...positionCounts.entries()].sort((left, right) => right[1] - left[1])[0]?.[0] || "";
  $("deskSubjectHeading").textContent = "اصلاح مشخصات گوینده";
  $("deskSubjectHint").textContent = "گوینده را از شناسنامه جست‌وجو کنید یا «شخص جدید» را بسازید؛ سپس ثبت مشخصات را بزنید.";
  $("deskEventFields").classList.add("hidden");
  $("deskPersonFields").classList.remove("hidden");
  if (!$("deskPersonMode")?.value) {
    if (person) applyDeskPersonChoice(String(person.person_id), {fromUser: false});
    else applyDeskPersonChoice("new", {fromUser: false});
  } else if ($("deskPersonMode").value === "new") {
    $("deskNewPersonBox")?.classList.remove("hidden");
    $("deskPersonMetaFields")?.classList.remove("hidden");
  } else {
    $("deskNewPersonBox")?.classList.add("hidden");
    $("deskPersonMetaFields")?.classList.remove("hidden");
  }
  if (!$("deskPersonPosition").dataset.touched) {
    $("deskPersonPosition").value = analyzedPosition || person?.position || speakerRecord?.position || "";
  }
  if (!$("deskPersonCategory").dataset.touched) {
    $("deskPersonCategory").value = person?.category || "";
  }
}

function resetDeskSubjectEditorTouch() {
  ["deskPersonName", "deskPersonPosition", "deskPersonCategory", "deskEventTitle", "deskEventLocation", "deskEventTime", "deskNewPersonName", "deskPersonSearch", "deskPersonId", "deskPersonMode"].forEach((id) => {
    const field = $(id);
    if (!field) return;
    delete field.dataset.touched;
    if (field.type === "hidden" || field.tagName === "INPUT" || field.tagName === "SELECT") field.value = "";
  });
  state.deskSubjectConfirmed = false;
  closeDeskPersonOptions();
  $("deskNewPersonBox")?.classList.add("hidden");
  $("deskPersonMetaFields")?.classList.add("hidden");
  const hint = $("deskSubjectConfirmHint");
  if (hint) hint.textContent = "";
}

function canManagePeople() {
  return (state.me?.permissions || []).some((permission) => permission === "*" || permission === "people.manage");
}

async function confirmDeskSubject() {
  const eventMessageId = Number($("finalizationEvent").value) || null;
  if (eventMessageId) {
    ["deskEventTitle", "deskEventLocation", "deskEventTime"].forEach((id) => {
      if ($(id)) $(id).dataset.touched = "1";
    });
    markDeskSubjectConfirmed("مشخصات رویداد ثبت شد.");
    toast("مشخصات رویداد ثبت شد.");
    return;
  }
  const mode = $("deskPersonMode")?.value;
  try {
    if (mode === "new") {
      const name = $("deskNewPersonName")?.value.trim() || "";
      if (!name) return toast("نام شخص جدید را وارد کنید.", true);
      $("deskPersonName").value = name;
      if (canManagePeople()) {
        const saved = await api("/admin/api/people", {
          method: "POST",
          body: JSON.stringify({
            full_name: name,
            position: $("deskPersonPosition")?.value.trim() || null,
            category: $("deskPersonCategory")?.value.trim() || null,
            registry_status: "inside",
            active: true,
            aliases: [],
            replace_aliases: true,
          }),
        });
        await loadReferenceData();
        applyDeskPersonChoice(String(saved.person_id), {fromUser: false});
        markDeskSubjectConfirmed("شخص جدید در شناسنامه ثبت شد.");
        toast("شخص جدید در شناسنامه ثبت شد.");
        return;
      }
      markDeskSubjectConfirmed("مشخصات برای تدوین ثبت شد.");
      toast("مشخصات برای تدوین ثبت شد.");
      return;
    }
    const personId = Number($("deskPersonId")?.value);
    const person = state.people.find((item) => Number(item.person_id) === personId);
    if (!person) return toast("یک گوینده از فهرست شناسنامه انتخاب کنید یا شخص جدید بسازید.", true);
    $("deskPersonName").value = person.full_name;
    if (canManagePeople()) {
      await api("/admin/api/people", {
        method: "POST",
        body: JSON.stringify({
          person_id: personId,
          full_name: person.full_name,
          position: $("deskPersonPosition")?.value.trim() || null,
          category: $("deskPersonCategory")?.value.trim() || null,
          registry_status: person.registry_status || "inside",
          active: Number(person.active) !== 0,
          replace_aliases: false,
          priority: Number(person.priority || 100),
        }),
      });
      await loadReferenceData();
      markDeskSubjectConfirmed("اصلاحات شناسنامه ثبت شد.");
      toast("اصلاحات شناسنامه ثبت شد.");
      return;
    }
    markDeskSubjectConfirmed("مشخصات برای تدوین ثبت شد.");
    toast("مشخصات برای تدوین ثبت شد.");
  } catch (error) {
    toast(error.message, true);
  }
}

function renderQuickRegistryPrompt() {
  const speaker = $("finalizationSpeaker").value;
  const canManagePeople = (state.me?.permissions || []).some((permission) => permission === "*" || permission === "people.manage");
  const speakerRecord = (state.analysisFilters.speakers || []).find((item) => item.speaker_name === speaker);
  const tags = state.analyzedMessages.flatMap((item) => (item.tags || []).map((tag) => ({...tag, message_id: item.id})));
  const candidate = tags.find((tag) => tag.speaker_name === speaker);
  const knownInside = state.people.some((person) => person.full_name === speaker && person.registry_status === "inside" && Number(person.active) !== 0);
  if (!speaker || speaker === "نامشخص" || !candidate || knownInside || !canManagePeople) {
    hideQuickRegistryPrompt();
    return;
  }
  $("quickRegistryDetectedName").textContent = speaker;
  $("quickRegistryPersonName").value = speaker;
  $("quickRegistryPersonPosition").value = candidate.speaker_position || speakerRecord?.position || "";
  $("quickRegistryPersonCategory").value = "";
  $("quickRegistryPersonAliases").value = "";
  $("quickRegistrySourceMessageId").value = String(candidate.message_id || "");
  $("quickRegistryPersonForm").dataset.tagId = String(candidate.tag_id || "");
  $("detectedPersonRegistryPrompt").classList.remove("hidden");
}

function uniqueTagValues(tags, field) {
  return [...new Set((tags || []).map((tag) => String(tag[field] || "").trim()).filter(Boolean))];
}

function renderBulletinCandidate(draft = state.currentDraft) {
  const fields = draft?.bulletin_fields || {};
  const person = fields.person || {};
  const topic = fields.topic || {};
  const isEvent = fields.content_type === "event" || draft?.content_type === "event";
  const event = fields.event || draft?.event || {};
  const tags = fields.analysis_topics || [];
  const generalTopics = uniqueTagValues(tags, "general_topic");
  const specificTopics = uniqueTagValues(tags, "specific_topic");
  const put = (id, value) => { $(id).textContent = value || "—"; };
  put("candidatePersonName", isEvent ? (event.title || draft?.event_title || draft?.title) : (person.name || draft?.person_name));
  put("candidatePersonPosition", isEvent ? "رویداد مستقل" : (person.position || draft?.position));
  put("candidateCategory", isEvent ? "وقایع و رویدادهای مهم ایران و جهان" : (fields.category_name || draft?.category_name || person.category));
  put("candidateGeneralTopic", generalTopics.join("، ") || topic.name || draft?.topic_name);
  put("candidateMainSubject", fields.main_subject || draft?.main_subject);
  put("candidateSpecificTopic", specificTopics.join("، "));
  put("candidateSummaryParagraph", fields.summary_paragraph || draft?.summary_paragraph);
  put("candidateSummarySentence", fields.summary_sentence || draft?.summary_sentence);
  put("candidateSummaryTitle", fields.detail || draft?.detail || fields.summary_title || draft?.summary_title);
  put("candidateFootnote", fields.footnote || draft?.footnote);
  $("bulletinCandidateTitle").textContent = draft?.title || "خروجی آماده برای نهایی‌سازی";
  const status = draft?.status || "draft";
  $("bulletinCandidateStatus").textContent = statusLabel(status);
  $("bulletinCandidateStatus").className = `status-pill ${status}`;
}

function refreshCurrentCandidatePreview() {
  if (!state.currentDraft) return;
  const preview = {
    ...state.currentDraft,
    person_name: $("draftPerson").selectedOptions[0]?.textContent?.split(" — ")[0]?.trim() || state.currentDraft.person_name,
    topic_name: $("draftTopic").selectedOptions[0]?.textContent?.trim() || state.currentDraft.topic_name,
    main_subject: $("mainSubject").value.trim(),
    category_name: $("draftCategory").value.trim(),
    summary_paragraph: $("summaryParagraph").value.trim(),
    summary_sentence: $("summarySentence").value.trim(),
    summary_title: $("summaryTitle").value.trim(), detail: $("summaryTitle").value.trim(),
    footnote: $("deskFootnote")?.value.trim() || "",
    bulletin_fields: {...(state.currentDraft.bulletin_fields || {}), main_subject: $("mainSubject").value.trim(), detail: $("summaryTitle").value.trim(), category_name: $("draftCategory").value.trim(), footnote: $("deskFootnote")?.value.trim() || ""},
  };
  renderBulletinCandidate(preview);
}

async function loadAnalyzedMessages() {
  const speaker = $("finalizationSpeaker").value;
  const eventMessageId = Number($("finalizationEvent").value) || null;
  if (!state.finalizationWindowConfirmed || (!speaker && !eventMessageId)) {
    state.analyzedMessages = [];
    state.selectedAnalyzedMessages.clear();
    hideQuickRegistryPrompt();
    hideDeskSubjectEditor();
    renderAnalyzedMessages();
    return;
  }
  const params = new URLSearchParams();
  if (speaker) params.set("speaker", speaker);
  if (eventMessageId) params.set("event_message_id", String(eventMessageId));
  if ($("finalizationGeneralTopic").value) params.set("general_topic", $("finalizationGeneralTopic").value);
  if ($("finalizationSpecificTopic").value) params.set("specific_topic", $("finalizationSpecificTopic").value);
  const windowParams = jalaliWindowParams("finalizationDateFrom", "finalizationTimeFrom", "finalizationDateTo", "finalizationTimeTo");
  windowParams.forEach((value, key) => params.set(key, value));
  const rows = await api(`/admin/api/analysis/messages?${params}`);
  state.analyzedMessages = groupAnalyzedMessages(rows);
  state.analyzedMessages.forEach((item) => state.messageSearchTexts.set(Number(item.id), String(item.text || item.caption || "").trim()));
  state.selectedAnalyzedMessages = new Set(state.analyzedMessages.map((item) => Number(item.id)));
  renderQuickRegistryPrompt();
  renderDeskSubjectEditor();
  renderAnalyzedMessages();
}

function renderAnalyzedMessages() {
  const speaker = $("finalizationSpeaker").value;
  const eventMessageId = Number($("finalizationEvent").value) || null;
  const isEvent = Boolean(eventMessageId);
  const selectionLabel = isEvent ? "رویداد مستقل" : speaker;
  const rows = state.analyzedMessages;
  $("analyzedMessageCount").textContent = `${n(rows.length)} خبر`;
  $("createDraftFromAnalysis").disabled = (!speaker && !eventMessageId) || !state.selectedAnalyzedMessages.size;
  $("analyzedSelectionHint").textContent = !state.finalizationWindowConfirmed
    ? "ابتدا بازهٔ روز و ساعت را تأیید کنید."
    : selectionLabel
    ? isEvent
      ? "ابتدا مشخصات رویداد را اصلاح کنید؛ سپس برای همین پیام «تدوین پیام» را بزنید."
      : `ابتدا مشخصات گوینده را اصلاح کنید؛ سپس برای هر پیام اقدام جداگانه انجام دهید. ${n(state.selectedAnalyzedMessages.size)} از ${n(rows.length)} خبر انتخاب شده است.`
    : "یک گوینده یا رویداد را انتخاب کنید.";
  $("analyzedMessageList").innerHTML = !selectionLabel
    ? `<div class="empty-mini">برای مشاهده خبرها، یک گوینده یا یک رویداد مستقل را از فیلتر بالا انتخاب کنید.</div>`
    : rows.length
      ? rows.map((item) => {
        const selected = state.selectedAnalyzedMessages.has(Number(item.id));
        const tags = item.tags || [];
        const itemIsEvent = String(item.analysis_content_type || "") === "event";
        const event = item.event || {};
        return `<article class="analyzed-card ${selected ? "selected" : ""}">
          <div>
            <h4>${esc(itemIsEvent ? (event.title || item.analysis_event_title || item.analysis_main_subject || "رویداد مهم") : (item.source_chat_title || item.source_chat_username || `پیام ${item.id}`))}</h4>
            <span class="analysis-date">${fdate(item.published_at || item.received_at)}</span>
          </div>
          <input class="select-check" type="checkbox" ${selected ? "checked" : ""} onchange="selectAnalyzedMessage(${item.id},this.checked)" aria-label="انتخاب خبر تحلیل‌شده">
          <div class="analyzed-card-meta">
            ${itemIsEvent
              ? `<span class="analysis-tag event-tag">رویداد مهم ایران و جهان</span><span class="analysis-tag">${esc(item.general_topic || item.analysis_general_topic || "نامشخص")}</span>`
              : tags.map((tag) => `<span class="analysis-tag">${esc(tag.general_topic || "نامشخص")}</span><span class="analysis-tag">${esc(tag.specific_topic || "نامشخص")}</span>`).join("")}
          </div>
          <p>${esc(item.text || item.caption || "[پیام رسانه‌ای]")}</p>
          ${itemIsEvent ? `<div class="analysis-evidence">${esc([event.location, event.time].filter((value) => value && value !== "نامشخص").join(" · ") || "رویداد مستقل؛ اطلاعات تکمیلی را در میز تدوین بررسی کنید.")}</div>` : tags.map((tag) => tag.speaker_evidence ? `<div class="analysis-evidence">شاهد گوینده: ${esc(tag.speaker_evidence)}</div>` : "").join("")}
          <div class="analyzed-card-actions desk-message-actions">
            <button type="button" class="danger-button" onclick="discardMessageFromDesk(${item.id})">کنار گذاشتن از صف</button>
            <button type="button" class="primary-button" onclick="composeMessageOnDesk(${item.id})">تدوین پیام</button>
            <button type="button" class="outline-button" onclick="searchMessageInGoogle(${item.id})">سرچ در گوگل این پیام</button>
          </div>
        </article>`;
      }).join("")
      : `<div class="empty-mini">خبری مطابق این انتخاب پیدا نشد.</div>`;
}

function selectAnalyzedMessage(id, selected) {
  selected
    ? state.selectedAnalyzedMessages.add(Number(id))
    : state.selectedAnalyzedMessages.delete(Number(id));
  renderAnalyzedMessages();
}

async function discardMessageFromDesk(messageId) {
  const id = Number(messageId);
  if (!confirm("این پیام از صف نهایی‌سازی کنار گذاشته شود؟")) return;
  try {
    await api(`/admin/api/analysis/messages/${id}/discard`, {
      method: "POST",
      body: JSON.stringify({}),
    });
    state.selectedAnalyzedMessages.delete(id);
    await loadAnalysisFilters();
    toast("پیام از صف نهایی‌سازی کنار گذاشته شد.");
  } catch (error) {
    toast(error.message, true);
  }
}

async function composeMessageOnDesk(messageId) {
  const id = Number(messageId);
  if (!state.analyzedMessages.some((item) => Number(item.id) === id)) {
    return toast("این پیام دیگر روی میز تدوین نیست.", true);
  }
  state.selectedAnalyzedMessages = new Set([id]);
  renderAnalyzedMessages();
  await createDraftFromAnalysis({fromComposeButton: true});
}

async function createDraftFromAnalysis(options = {}) {
  const fromComposeButton = Boolean(options.fromComposeButton);
  const speakerFilter = $("finalizationSpeaker").value;
  const eventMessageId = Number($("finalizationEvent").value) || null;
  const isEvent = Boolean(eventMessageId);
  const selected = state.analyzedMessages.filter((item) => state.selectedAnalyzedMessages.has(Number(item.id)));
  if ((!speakerFilter && !isEvent) || !selected.length) return toast("یک گوینده یا رویداد و حداقل یک خبر را انتخاب کنید.", true);
  if (isEvent && selected.length !== 1) return toast("هر رویداد فقط با یک پیام به میز تدوین می‌رود.", true);
  const button = fromComposeButton ? null : $("createDraftFromAnalysis");
  const oldText = button?.textContent;
  if (button) {
    button.disabled = true;
    button.textContent = isEvent ? "در حال باز کردن رویداد…" : "در حال ساخت پیش‌نویس…";
  }
  const topicCounts = new Map();
  for (const item of selected) {
    for (const tag of item.tags || []) {
      const topic = tag.general_topic || item.analysis_general_topic || "نامشخص";
      topicCounts.set(topic, (topicCounts.get(topic) || 0) + 1);
    }
  }
  const event = selected[0]?.event || {};
  const deskEventTitle = $("deskEventTitle")?.value.trim() || "";
  const deskEventLocation = $("deskEventLocation")?.value.trim() || "";
  const deskEventTime = $("deskEventTime")?.value.trim() || "";
  const deskPersonId = Number($("deskPersonId")?.value) || null;
  const deskPersonName = $("deskPersonName")?.value.trim() || $("deskNewPersonName")?.value.trim() || speakerFilter;
  const deskPersonPosition = $("deskPersonPosition")?.value.trim() || "";
  const deskPersonCategory = $("deskPersonCategory")?.value.trim() || "";
  const topicName = $("finalizationGeneralTopic").value
    || (isEvent ? (selected[0]?.analysis_general_topic || selected[0]?.general_topic) : null)
    || [...topicCounts.entries()].sort((left, right) => right[1] - left[1])[0]?.[0]
    || "نامشخص";
  const speakerName = isEvent ? null : (deskPersonName || speakerFilter);
  const person = isEvent
    ? null
    : (deskPersonId ? state.people.find((item) => Number(item.person_id) === deskPersonId) : state.people.find((item) => item.full_name === speakerName));
  const topic = state.topics.find((item) => item.name === topicName);
  const positionCounts = new Map();
  for (const item of selected) {
    for (const tag of item.tags || []) {
      const position = String(tag.speaker_position || "").trim();
      if (position && position !== "نامشخص") {
        positionCounts.set(position, (positionCounts.get(position) || 0) + 1);
      }
    }
  }
  const analyzedPosition = deskPersonPosition
    || [...positionCounts.entries()].sort((left, right) => right[1] - left[1])[0]?.[0]
    || null;
  const messageInputs = selected.map((item, index) => ({
    message_id: Number(item.id),
    selected_text: null,
    sort_order: index,
  }));
  const baseText = selected
    .map((item) => String(item.text || item.caption || "").trim())
    .filter(Boolean)
    .filter((text, index, all) => all.indexOf(text) === index)
    .join("\n\n");
  const eventTitle = deskEventTitle || event.title || selected[0]?.analysis_event_title || selected[0]?.analysis_main_subject || "رویداد مهم";
  try {
    const data = await api("/admin/api/editorial-drafts", {
      method: "POST",
      body: JSON.stringify({
        content_type: isEvent ? "event" : "person_statement",
        title: isEvent ? eventTitle : `${speakerName} — ${topicName}`,
        person_id: isEvent ? null : (person?.person_id || null),
        person_name: isEvent ? null : speakerName,
        position: isEvent ? null : analyzedPosition,
        topic_id: topic?.topic_id || null,
        topic_name: topicName,
        main_subject: selected[0]?.analysis_main_subject || eventTitle || topicName,
        category_name: isEvent ? "رویدادهای مهم ایران و جهان" : (deskPersonCategory || null),
        event_title: isEvent ? eventTitle : null,
        event_entities: isEvent ? (event.involved_entities || []) : [],
        event_location: isEvent ? (deskEventLocation || event.location || selected[0]?.analysis_event_location || null) : null,
        event_time: isEvent ? (deskEventTime || event.time || selected[0]?.analysis_event_time || null) : null,
        base_text: baseText,
        message_inputs: messageInputs,
      }),
    });
    await loadDrafts();
    await openDraft(data.draft_id);
    requestAnimationFrame(() => {
      $("draftEditor")?.scrollIntoView({behavior: "smooth", block: "start"});
      $("generateAllSummaries")?.focus({preventScroll: true});
    });
    if (keepQuickStartLocation()) {
      renderFinalizationView();
    } else {
      state.finalizationView = "drafts";
      renderFinalizationView();
      history.replaceState(null, "", "#finalization:drafts");
    }
    toast(isEvent
      ? "رویداد در انتهای صفحه باز شد. خلاصه‌ها فقط با دکمهٔ «تولید سه خلاصهٔ رویداد» ساخته می‌شوند."
      : "امکانات تدوین در انتهای صفحه باز شد. تا وقتی «تولید هر سه خلاصه» را نزنید، خلاصه‌ای تولید نمی‌شود.");
  } catch (error) {
    toast(error.message, true);
  } finally {
    if (button) {
      button.disabled = !state.selectedAnalyzedMessages.size;
      button.textContent = oldText;
    }
  }
}

async function createManualDraft() {
  const buttons = [...document.querySelectorAll("[data-create-manual-draft]")];
  const labels = buttons.map((button) => button.textContent);
  buttons.forEach((button) => {
    button.disabled = true;
    button.textContent = "در حال ساخت خبر دستی…";
  });
  try {
    const data = await api("/admin/api/editorial-drafts", {
      method: "POST",
      body: JSON.stringify({
        manual: true,
        title: "خبر دستی جدید",
        message_inputs: [],
      }),
    });
    state.selectedAnalyzedMessages.clear();
    await loadDrafts();
    await openDraft(data.draft_id);
    requestAnimationFrame(() => $("draftTitle")?.focus({preventScroll: true}));
    if (keepQuickStartLocation()) {
      renderFinalizationView();
    } else {
      state.finalizationView = "drafts";
      renderFinalizationView();
      history.replaceState(null, "", "#finalization:drafts");
    }
    toast("خبر دستیِ مستقل ساخته شد؛ هیچ پیام یا متن پایه‌ای از پیش‌نویس‌های قبلی در آن وارد نشده است.");
  } catch (error) {
    toast(error.message, true);
  } finally {
    buttons.forEach((button, index) => {
      button.disabled = false;
      button.textContent = labels[index];
    });
  }
}

function automationDate(value) {
  return value ? fdate(value) : "—";
}

function automationLabel(value) {
  return ({stopped: "متوقف", idle: "آماده", running: "در حال اجرا", completed: "انجام‌شده", failed: "نیازمند بررسی"})[String(value || "")] || "نامشخص";
}

function renderAutomationStatus(data) {
  state.automation = data || {};
  const analysisEnabled = Boolean(data?.analysis_enabled ?? data?.initial_analysis_enabled);
  const draftsEnabled = Boolean(data?.drafts_enabled);
  const enabledCount = Number(analysisEnabled) + Number(draftsEnabled);
  const badge = $("automationStatusBadge");
  badge.textContent = enabledCount === 2 ? "هر دو مرحله فعال‌اند" : (enabledCount ? "یک مرحله فعال است" : "همه مرحله‌ها متوقف‌اند");
  badge.className = `status-pill ${enabledCount ? "approved" : "pending"}`;
  $("analysisAutomationBadge").textContent = analysisEnabled ? "فعال" : "متوقف";
  $("analysisAutomationBadge").className = `status-pill ${analysisEnabled ? "approved" : "pending"}`;
  $("draftAutomationBadge").textContent = draftsEnabled ? "فعال" : "متوقف";
  $("draftAutomationBadge").className = `status-pill ${draftsEnabled ? "approved" : "pending"}`;
  $("startEditorialAutomation").classList.toggle("hidden", analysisEnabled);
  $("stopEditorialAutomation").classList.toggle("hidden", !analysisEnabled);
  $("startEditorialDraftAutomation").classList.toggle("hidden", draftsEnabled);
  $("stopEditorialDraftAutomation").classList.toggle("hidden", !draftsEnabled);
  $("runEditorialAnalysisNow").disabled = !analysisEnabled;
  $("runEditorialDraftsNow").disabled = !draftsEnabled;
  if (data?.analysis_start_at && document.activeElement !== $("analysisAutomationStartDate")) {
    $("analysisAutomationStartDate").value = jalaliInputDate(data.analysis_start_at);
    $("analysisAutomationStartTime").value = tehranTimeInput(data.analysis_start_at);
  }
  if (data?.draft_start_at && document.activeElement !== $("draftAutomationStartDate")) {
    $("draftAutomationStartDate").value = jalaliInputDate(data.draft_start_at);
    $("draftAutomationStartTime").value = tehranTimeInput(data.draft_start_at);
  }
  $("automationStatusDetails").innerHTML = `
    <div><small>تحلیل اولیه</small><b>${automationLabel(data?.analysis_status)}</b><span>از ${esc(automationDate(data?.analysis_start_at))} · آخرین اجرا: ${esc(automationDate(data?.analysis_last_completed_at))} · ${n(data?.analysis_last_processed || 0)} پیام</span></div>
    <div><small>پیش‌نویس خودکار</small><b>${automationLabel(data?.draft_status)}</b><span>از ${esc(automationDate(data?.draft_start_at))} · آخرین اجرا: ${esc(automationDate(data?.draft_last_completed_at))} · ${n(data?.draft_last_processed || 0)} پیش‌نویس</span></div>
    <div><small>زمان‌بندی مستقل</small><b>هر ${n(data?.analysis_interval_minutes || 10)} دقیقه / ${n(data?.draft_interval_hours || 3)} ساعت</b><span>${data?.analysis_running || data?.draft_running ? "فرایندی در حال اجراست" : "آمادهٔ اجرای بعدی"}</span></div>`;
}

async function loadEditorialAutomation() {
  try { renderAutomationStatus(await api("/admin/api/editorial-automation")); }
  catch (error) { toast(error.message, true); }
}

function automationStartPayload(dateId, timeId) {
  const date = $(dateId).value.trim();
  const time = $(timeId).value.trim();
  if (!date || !time) throw new Error("روز و ساعت شروع این فرایند را وارد کنید.");
  return {start_date_jalali: date, start_time: time, timezone: "Asia/Tehran"};
}

async function editorialAutomationAction(path, message, body = null) {
  try {
    const result = await api(path, {method: "POST", body: body ? JSON.stringify(body) : undefined});
    if (result.initial_analysis_enabled !== undefined) renderAutomationStatus(result);
    else await loadEditorialAutomation();
    toast(message || "عملیات خودکار ثبت شد.");
    await Promise.all([loadHumanControl(), loadDrafts()]);
  } catch (error) { toast(error.message, true); }
}

function humanControlEvidence(candidate) {
  const evidence = Array.isArray(candidate.web_evidence) ? candidate.web_evidence : [];
  if (!evidence.length) return `<p class="muted">برای این مورد، نتیجهٔ قابل اتکایی از جست‌وجوی بیرونی ثبت نشد؛ با متن پیام و شناسنامه بررسی کنید.</p>`;
  return `<details class="candidate-evidence"><summary>${n(evidence.length)} شاهد جست‌وجو</summary>${evidence.map((item) => `<a href="${esc(item.url || "#")}" target="_blank" rel="noopener"><b>${esc(item.title || "نتیجه")}</b><span>${esc(item.snippet || "")}</span></a>`).join("")}</details>`;
}

async function loadHumanControl() {
  const host = $("humanControlList");
  if (!host) return;
  if (!state.people.length) await loadReferenceData();
  const candidates = await api("/admin/api/person-candidates?status=pending");
  host.innerHTML = candidates.length ? candidates.map((candidate) => {
    const targetOptions = [`<option value="">انتخاب شخص در شناسنامه</option>`, ...state.people.map((person) => `<option value="${person.person_id}">${esc(person.full_name)}${person.position ? ` · ${esc(person.position)}` : ""}</option>`)].join("");
    const initialName = candidate.suggested_name || candidate.detected_name || "";
    const kind = candidate.candidate_kind === "position_only" ? "سمت بدون نام" : "نام نیازمند تطبیق";
    return `<article class="human-control-card">
      <section class="human-control-message"><div class="human-control-step"><span>۱</span><b>پیام مبنا</b></div><p>${esc(candidate.sample_text || candidate.sample_caption || "متن پیام مبنا در دسترس نیست؛ با تعداد پیام‌های مرتبط و شواهد زیر بررسی کنید.")}</p><small>${n(candidate.linked_message_count || 1)} پیام مرتبط · ${esc(candidate.candidate_kind === "position_only" ? "هوش فقط سمت را تشخیص داده است" : "نامزد از تحلیل و جست‌وجو استخراج شده است")}</small></section>
      <section class="human-control-candidate"><div class="human-control-step"><span>۲</span><b>نامزد گوینده و تصمیم</b></div><div class="human-control-copy"><span class="status-pill pending">${esc(kind)}</span><h4>${esc(initialName || "نامشخص")}</h4>
        <p>${candidate.detected_position ? `سمت تشخیص‌شده: ${esc(candidate.detected_position)}` : "سمت مشخص نشده"}</p>
        ${candidate.web_query ? `<p class="muted">عبارت جست‌وجو: ${esc(candidate.web_query)}</p>` : ""}${humanControlEvidence(candidate)}</div>
      <div class="human-control-form" data-candidate="${candidate.candidate_id}">
        <label>نام کامل برای ثبت<input id="candidateName_${candidate.candidate_id}" value="${esc(initialName)}"></label>
        <label>سمت / سمت افزوده<input id="candidatePosition_${candidate.candidate_id}" value="${esc(candidate.detected_position || "")}"></label>
        <label>دسته<select id="candidateCategory_${candidate.candidate_id}">${[`<option value="">انتخاب دسته</option>`, ...(state.personCategories || []).map((item) => `<option value="${esc(item)}" ${item === (candidate.category || "") ? "selected" : ""}>${esc(item)}</option>`)].join("")}</select></label>
        <label>تطبیق با شناسنامه<select id="candidateTarget_${candidate.candidate_id}">${targetOptions}</select></label>
        <div class="actions"><button class="blue" type="button" onclick="reviewHumanCandidate(${candidate.candidate_id},'merge')">تطبیق و تکمیل شناسنامه</button><button class="primary" type="button" onclick="reviewHumanCandidate(${candidate.candidate_id},'approve')">افزودن به شناسنامه</button><button class="danger" type="button" onclick="reviewHumanCandidate(${candidate.candidate_id},'reject')">رد پیشنهاد</button></div>
      </div></section></article>`;
  }).join("") : `<div class="empty-mini">موردی برای کنترل انسانی باقی نمانده است.</div>`;
}

async function reviewHumanCandidate(candidateId, action) {
  const target = Number($(`candidateTarget_${candidateId}`).value) || null;
  if (action === "merge" && !target) return toast("شخص مقصد را از شناسنامه انتخاب کنید.", true);
  const body = {
    action,
    merge_person_id: target,
    full_name: $(`candidateName_${candidateId}`).value.trim() || null,
    position: $(`candidatePosition_${candidateId}`).value.trim() || null,
    category: $(`candidateCategory_${candidateId}`).value.trim() || null,
    add_detected_alias: true,
    add_position: true,
  };
  try {
    await api(`/admin/api/person-candidates/${candidateId}`, {method: "PATCH", body: JSON.stringify(body)});
    toast(action === "reject" ? "پیشنهاد رد شد." : "شناسنامه و خبرهای مرتبط به‌روزرسانی شد.");
    await Promise.all([
      loadHumanControl(),
      loadReferenceData(),
      state.finalizationWindowConfirmed ? loadAnalysisFilters() : Promise.resolve(),
      state.finalizationView === "drafts" ? loadDrafts() : Promise.resolve(),
    ]);
  } catch (error) { toast(error.message, true); }
}

async function loadDrafts() {
  if (!state.people.length || !state.topics.length) await loadReferenceData();
  const suffix = state.draftFilter ? `?status=${state.draftFilter}` : "";
  state.drafts = await api(`/admin/api/editorial-drafts${suffix}`);
  const isAutomated = (item) => String(item.created_by || "").startsWith("automation_");
  const visible = state.draftOriginFilter === "automation"
    ? state.drafts.filter(isAutomated)
    : state.draftOriginFilter === "manual"
      ? state.drafts.filter((item) => !isAutomated(item))
      : state.drafts;
  $("draftList").innerHTML = visible.length ? visible.map((item) => `
    <article class="draft-item ${state.currentDraft?.draft_id === item.draft_id ? "active" : ""}" onclick="openDraft(${item.draft_id})">
      <div><span class="status-pill ${esc(item.status)}">${statusLabel(item.status)}</span><span class="draft-origin ${isAutomated(item) ? "automation" : "manual"}">${isAutomated(item) ? "خودکار" : "انسان"}</span></div>
      <h4>${esc(item.title || `${item.person_name || "خبر"} — ${item.topic_name || "بدون موضوع"}`)}</h4>
      <p>${esc(item.person_name || "شخص نامشخص")} · ${esc(item.topic_name || "موضوع نامشخص")} · ${n(item.input_count)} منبع</p>
      <p>ویرایش: ${fdate(item.updated_at)}</p>
    </article>`).join("") : `<div class="empty-mini">پیش‌نویسی با این منشأ در این بخش نیست.</div>`;
}

async function openDraft(id) {
  try {
    const draft = await api(`/admin/api/editorial-drafts/${id}`);
    state.currentDraft = draft;
    const isEvent = String(draft.content_type || "") === "event";
    $("emptyEditor").classList.add("hidden");
    $("draftEditor").classList.remove("hidden");
    $("draftHeading").textContent = draft.title || (isEvent ? (draft.event_title || "رویداد مهم") : `${draft.person_name || "خبر"} — ${draft.topic_name || "بدون موضوع"}`);
    $("draftStatus").textContent = statusLabel(draft.status);
    $("draftStatus").className = `status-pill ${draft.status}`;
    $("draftTitle").value = draft.title || "";
    setDraftSelectValue("draftPerson", draft.person_id, draft.person_name, "شخص تحلیل‌شده");
    setDraftSelectValue("draftTopic", draft.topic_id, draft.topic_name, "موضوع تحلیل‌شده");
    $("draftLocation").value = draft.oration_location || "";
    $("draftCategory").value = draft.category_name || draft.bulletin_fields?.category_name || "";
    $("mainSubject").value = draft.main_subject || draft.bulletin_fields?.main_subject || "";
    $("draftSourceUrl").value = draft.source_url || "";
    renderShortLinkPreview(draft);
    $("draftBase").value = draft.base_text || "";
    $("summaryParagraph").value = draft.summary_paragraph || "";
    $("summarySentence").value = draft.summary_sentence || "";
    $("summaryTitle").value = draft.detail || draft.summary_title || "";
    if ($("deskFootnote")) $("deskFootnote").value = draft.footnote || "";
    $("summaryBlockHeading").textContent = isEvent ? "خلاصه‌های رویداد" : "خلاصه‌های گوینده";
    $("summaryBlockHint").textContent = isEvent
      ? "مدل دوم، سه خلاصهٔ خبریِ رویداد مستقل را تولید می‌کند؛ سپس نتیجه را بازبینی کنید."
      : "مدل دوم، مطالب را از زبان همان گوینده یکپارچه می‌کند.";
    $("generateAllSummaries").textContent = isEvent ? "تولید سه خلاصهٔ رویداد" : "تولید هر سه خلاصه";
    $("generateAllSummaries").classList.remove("hidden");
    const finalizationMoment = draft.finalized_at || new Date().toISOString();
    $("draftFinalizationDate").value = jalaliInputDate(finalizationMoment);
    $("draftFinalizationTime").value = draft.flow_time || tehranTimeInput(finalizationMoment);
    renderBulletinCandidate(draft);
    $("inputCount").textContent = `(${n((draft.inputs || []).length)})`;
    const eventContext = isEvent ? `<div class="input-snippet"><b>اطلاعات تشخیص رویداد</b><p>${esc([draft.event?.title, draft.event?.location, draft.event?.time].filter((value) => value && value !== "نامشخص").join(" · ") || "اطلاعات تکمیلی در متن پیام موجود است.")}</p></div>` : "";
    const sourceInputs = (draft.inputs || []).map((item) => `<div class="input-snippet"><b>${esc(item.source_chat_title || item.source_chat_username || `پیام ${item.message_id}`)}</b><p>${esc(item.selected_text || item.text || item.caption || "[رسانه]")}</p><a href="#" onclick="openMessage(${item.message_id});return false">مشاهده اصل پیام</a></div>`).join("");
    $("draftInputs").innerHTML = eventContext + (sourceInputs || "<div class=\"empty-mini\">برای این خبر دستی، پیام مبنایی متصل نشده است.</div>");
    $("draftVersions").innerHTML = (draft.versions || []).map((item) => `<div class="version-row"><b>نسخه ${n(item.version_no)}</b> · ${fdate(item.created_at)} · ${esc(item.actor || "")}<br><small>${esc(item.change_reason || "")}</small>${item.version_no !== draft.current_version ? `<button type="button" class="text-button" onclick="restoreDraftVersion(${draft.draft_id},${item.version_id})">بازگردانی</button>` : ""}</div>`).join("");
    $("deleteDraft").classList.toggle("hidden", draft.status !== "draft");
    await loadDrafts();
  } catch (error) { toast(error.message, true); }
}

function setDraftSelectValue(selectId, numericId, name, prefix) {
  const select = $(selectId);
  if (numericId) {
    select.value = String(numericId);
    if (select.value) return;
  }
  const cleanName = String(name || "").trim();
  if (!cleanName) {
    select.value = "";
    return;
  }
  const customValue = `name:${encodeURIComponent(cleanName)}`;
  if (![...select.options].some((option) => option.value === customValue)) {
    select.insertAdjacentHTML(
      "beforeend",
      `<option value="${esc(customValue)}">${esc(cleanName)} — ${esc(prefix)}</option>`,
    );
  }
  select.value = customValue;
}

function selectedDraftReference(selectId, collection, idKey, nameKey) {
  const raw = $(selectId).value;
  if (raw.startsWith("name:")) {
    return {id: null, name: decodeURIComponent(raw.slice(5))};
  }
  const id = Number(raw) || null;
  const item = collection.find((candidate) => Number(candidate[idKey]) === id);
  return {id, name: item?.[nameKey] || null};
}

function draftPayload() {
  const person = selectedDraftReference("draftPerson", state.people, "person_id", "full_name");
  const topic = selectedDraftReference("draftTopic", state.topics, "topic_id", "name");
  return {
    expected_version: Number(state.currentDraft?.current_version) || null,
    title: $("draftTitle").value.trim() || null,
    base_text: $("draftBase").value.trim(),
    summary_paragraph: $("summaryParagraph").value.trim() || null,
    summary_sentence: $("summarySentence").value.trim() || null,
    summary_title: $("summaryTitle").value.trim() || null,
    detail: $("summaryTitle").value.trim() || null,
    category_name: $("draftCategory").value.trim() || null,
    person_id: person.id, person_name: person.name,
    topic_id: topic.id, topic_name: topic.name,
    main_subject: $("mainSubject").value.trim() || null,
    oration_location: $("draftLocation").value.trim() || null,
    source_url: $("draftSourceUrl").value.trim() || null,
    footnote: $("deskFootnote")?.value.trim() || null,
    change_reason: "ویرایش از میز تدوین",
  };
}

function renderShortLinkPreview(draft) {
  const shortUrl = String(draft?.short_url || "").trim();
  const box = $("shortLinkPreview");
  if (!shortUrl) {
    box.classList.add("hidden");
    $("draftQrCode").removeAttribute("src");
    return;
  }
  $("shortLinkAnchor").textContent = shortUrl;
  $("shortLinkAnchor").href = shortUrl;
  $("draftQrCode").src = `/admin/api/editorial-drafts/${draft.draft_id}/qr?updated=${encodeURIComponent(draft.updated_at || "")}`;
  box.classList.remove("hidden");
}

async function createShortLink() {
  if (!state.currentDraft) return toast("ابتدا یک پیش‌نویس را باز کنید.", true);
  const sourceUrl = $("draftSourceUrl").value.trim();
  if (!sourceUrl) return toast("ابتدا لینک منبع را وارد کنید.", true);
  try {
    const data = await api(`/admin/api/editorial-drafts/${state.currentDraft.draft_id}/source-link`, {
      method: "POST", body: JSON.stringify({source_url: sourceUrl}),
    });
    state.currentDraft = {...state.currentDraft, short_url: data.short_url};
    renderShortLinkPreview(state.currentDraft);
    toast("لینک کوتاه و کد QR ساخته شد.");
  } catch (error) { toast(error.message, true); }
}

async function saveDraft(showNotice = true) {
  if (!state.currentDraft) return;
  const data = draftPayload();
  if (!data.base_text) return toast("متن پایه نمی‌تواند خالی باشد.", true);
  try {
    await api(`/admin/api/editorial-drafts/${state.currentDraft.draft_id}`, {method: "PUT", body: JSON.stringify(data)});
    await openDraft(state.currentDraft.draft_id);
    if (showNotice) toast("نسخه جدید ذخیره شد.");
    return true;
  } catch (error) {
    toast(error.message, true);
    return false;
  }
}

async function generateDraft(kind, autoSave = false) {
  if (!state.currentDraft) return;
  try {
    if (kind !== "base" && !$("draftBase").value.trim()) return toast("ابتدا متن پایه را تولید یا وارد کنید.", true);
    toast("در حال تولید متن…");
    const data = await api(`/admin/api/editorial-drafts/${state.currentDraft.draft_id}/generate`, {
      method: "POST",
      body: JSON.stringify({
        kind,
        base_text: $("draftBase").value.trim() || null,
        persist: Boolean(autoSave),
        expected_version: Number(state.currentDraft.current_version) || null,
      }),
    });
    if (data.base_text != null) $("draftBase").value = data.base_text;
    if (data.summary_paragraph != null) $("summaryParagraph").value = data.summary_paragraph;
    if (data.summary_sentence != null) $("summarySentence").value = data.summary_sentence;
    if (data.detail != null || data.summary_title != null) $("summaryTitle").value = data.detail || data.summary_title;
    refreshCurrentCandidatePreview();
    if (autoSave) {
      await openDraft(state.currentDraft.draft_id);
    } else {
      toast(
        data.ai_used
          ? "متن با هوش مصنوعی تولید شد؛ لطفاً بازبینی کنید."
          : `متن پشتیبان آفلاین تولید شد. ${data.fallback_reason || "مدل دوم در دسترس نبود."}`,
        !data.ai_used,
      );
    }
    return data;
  } catch (error) {
    toast(error.message, true);
    return null;
  }
}

async function deleteCurrentDraft() {
  const draft = state.currentDraft;
  if (!draft || draft.status !== "draft") {
    return toast("فقط پیش‌نویس نهایی‌نشده قابل حذف است.", true);
  }
  if (!confirm(`پیش‌نویس «${draft.title || draft.person_name || draft.draft_id}» حذف شود؟ این کار قابل بازگشت نیست.`)) return;
  try {
    await api(`/admin/api/editorial-drafts/${draft.draft_id}`, {method: "DELETE"});
    state.currentDraft = null;
    renderBulletinCandidate(null);
    $("draftEditor").classList.add("hidden");
    $("emptyEditor").classList.remove("hidden");
    await loadDrafts();
    toast("پیش‌نویس حذف شد.");
  } catch (error) {
    toast(error.message, true);
  }
}

async function finalizeDraft() {
  if (!state.currentDraft) return;
  const finalizedDate = $("draftFinalizationDate").value.trim();
  const finalizedTime = $("draftFinalizationTime").value.trim();
  if (!finalizedDate || !finalizedTime) {
    return toast("روز و ساعت نهایی‌سازی را وارد کنید.", true);
  }
  try {
    if (!await saveDraft(false)) return;
    await api(`/admin/api/editorial-drafts/${state.currentDraft.draft_id}/finalize`, {
      method: "POST",
      body: JSON.stringify({
        finalized_date_jalali: finalizedDate,
        finalized_time: finalizedTime,
        timezone: "Asia/Tehran",
      }),
    });
    toast("خبر نهایی و برای خبرنامه آماده شد.");
    state.currentDraft = null;
    state.selectedAnalyzedMessages.clear();
    $("finalizationSpeaker").value = "";
    $("finalizationEvent").value = "";
    renderBulletinCandidate(null);
    $("draftEditor").classList.add("hidden");
    $("emptyEditor").classList.remove("hidden");
    $("emptyEditor").querySelector("h3").textContent = "خبر نهایی شد؛ شخص بعدی را انتخاب کنید";
    $("emptyEditor").querySelector("p").textContent = "برای ادامه، از فهرست بازهٔ تأییدشده یک گویندهٔ دیگر انتخاب کنید.";
    await loadDrafts();
    await loadAnalysisFilters();
    state.finalizationView = "workbench";
    state.finalizationStage = "desk";
    renderFinalizationView();
    if (!keepQuickStartLocation()) {
      history.replaceState(null, "", "#finalization:workbench");
    }
  } catch (error) { toast(error.message, true); }
}

async function restoreDraftVersion(draftId, versionId) {
  if (!confirm("این نسخه به‌عنوان یک نسخه جدید بازیابی شود؟")) return;
  try {
    await api(`/admin/api/editorial-drafts/${draftId}/restore/${versionId}`, {
      method: "POST",
      body: JSON.stringify({expected_version: Number(state.currentDraft?.current_version) || null}),
    });
    await openDraft(draftId);
    toast("نسخه بازیابی شد.");
  } catch (error) { toast(error.message, true); }
}

function highAttentionDayLabel(day) {
  return jalaliInputDate(`${day}T12:00:00+03:30`) || day;
}

function ensureSelectOption(select, value, label) {
  if (!select || !value) return;
  if (![...select.options].some((option) => option.value === value)) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = label || value;
    select.appendChild(option);
  }
  select.value = value;
}

function syncJalaliFlowInput(inputId, flowDay) {
  const input = $(inputId);
  if (!input) return;
  input.value = flowDay ? (highAttentionDayLabel(flowDay) || "") : "";
}

function applyJalaliFlowDay(rawValue, {selectId, stateKey} = {}) {
  if (!parseJalaliInput(rawValue)) {
    toast("تاریخ جلالی را مانند ۱۴۰۵/۰۵/۲۶ وارد کنید یا از تقویم انتخاب کنید.", true);
    return "";
  }
  const flowDate = jalaliValueToFlowDate(rawValue);
  if (stateKey) state[stateKey] = flowDate;
  ensureSelectOption($(selectId), flowDate, highAttentionDayLabel(flowDate));
  return flowDate;
}

function selectedHighAttentionDrafts() {
  const ids = [...document.querySelectorAll(".high-attention-draft-check:checked")]
    .map((item) => Number(item.value));
  return ids.map((id) => state.highAttentionDrafts.find((item) => item.draft_id === id)).filter(Boolean);
}

function renderHighAttentionDrafts() {
  const day = state.highAttentionDay;
  const drafts = state.highAttentionDrafts;
  const toolbar = $("highAttentionDraftsToolbar");
  const selectAll = $("selectAllHighAttentionDrafts");
  const hasDrafts = Boolean(day && drafts.length);
  toolbar?.classList.toggle("hidden", !hasDrafts);
  if (selectAll && !hasDrafts) {
    selectAll.checked = false;
    selectAll.indeterminate = false;
  }
  $("highAttentionDraftsSelectionHint").textContent = hasDrafts
    ? `${n(drafts.length)} خبر نهایی در این روز`
    : "خبرهای نهایی همین روز";
  $("highAttentionDrafts").innerHTML = !day
    ? `<div class="empty-mini">ابتدا روز نهایی‌سازی را انتخاب کنید.</div>`
    : drafts.length ? drafts.map((item) => `
      <label class="sortable-item"><span class="drag-handle">◉</span><div><b>${esc(item.title || `${item.person_name || "خبر"} — ${item.topic_name || "بدون موضوع"}`)}</b><p>${esc(item.person_name || item.event_title || "خبر") } · ${esc(item.topic_name || item.category || "بدون موضوع")} · ${esc(item.summary_sentence || item.summary_paragraph || "")}</p></div><input class="high-attention-draft-check" type="checkbox" value="${item.draft_id}" onchange="updateHighAttentionSelection()"></label>`).join("")
    : `<div class="empty-mini">برای این روز خبر نهایی‌شده‌ای وجود ندارد.</div>`;
  updateHighAttentionSelection();
}

function updateHighAttentionSelection() {
  const boxes = [...document.querySelectorAll(".high-attention-draft-check")];
  const count = boxes.filter((input) => input.checked).length;
  const selectAll = $("selectAllHighAttentionDrafts");
  if (selectAll) {
    selectAll.checked = Boolean(boxes.length) && count === boxes.length;
    selectAll.indeterminate = count > 0 && count < boxes.length;
  }
  $("generateHighAttention").disabled = !count;
  $("generateHighAttention").textContent = count
    ? `تولید پربازتاب از ${n(count)} خبر انتخاب‌شده`
    : "تولید پربازتاب";
}

function toggleAllHighAttentionDrafts(checked) {
  document.querySelectorAll(".high-attention-draft-check").forEach((input) => {
    input.checked = Boolean(checked);
  });
  updateHighAttentionSelection();
}

function renderHighAttentionEditor(run) {
  const editor = $("highAttentionEditor");
  if (!run) {
    editor.classList.add("hidden");
    $("highAttentionItems").innerHTML = "";
    return;
  }
  editor.classList.remove("hidden");
  const status = run.status || "draft";
  $("highAttentionStatus").textContent = statusLabel(status);
  $("highAttentionStatus").className = `status-pill ${status}`;
  const locked = status !== "draft";
  $("highAttentionItems").innerHTML = (run.items || []).length ? (run.items || []).map((item, index) => `
    <article class="high-attention-item-editor">
      <h4>محور ${n(index + 1)}</h4>
      <label>تیتر<input data-high-attention-title="${item.high_attention_item_id}" value="${esc(item.title || "")}" ${locked ? "disabled" : ""}></label>
      <label>توضیح<textarea rows="4" data-high-attention-summary="${item.high_attention_item_id}" ${locked ? "disabled" : ""}>${esc(item.summary || "")}</textarea></label>
    </article>`).join("") : `<div class="empty-mini">مدل برای این انتخاب محور پربازتابی پیدا نکرده است.</div>`;
  $("saveHighAttention").classList.toggle("hidden", locked);
  $("finalizeHighAttention").classList.toggle("hidden", locked);
}

function renderHighAttentionRuns() {
  const runs = state.highAttentionRuns;
  $("highAttentionRuns").innerHTML = !state.highAttentionDay
    ? `<div class="empty-mini">روز را انتخاب کنید.</div>`
    : runs.length ? runs.map((run) => `<article class="draft-item ${state.currentHighAttention?.high_attention_run_id === run.high_attention_run_id ? "active" : ""}" onclick="openHighAttentionRun(${run.high_attention_run_id})"><div><span class="status-pill ${esc(run.status)}">${statusLabel(run.status)}</span></div><h4>${n(run.item_count || 0)} محور پربازتاب</h4><p>مدل: ${esc(run.model || "نامشخص")} · ${fdate(run.updated_at || run.created_at)}</p></article>`).join("")
    : `<div class="empty-mini">هنوز تولیدی برای این روز ثبت نشده است.</div>`;
}

async function loadHighAttentionDay() {
  const day = state.highAttentionDay;
  state.currentHighAttention = null;
  renderHighAttentionEditor(null);
  if (!day) {
    state.highAttentionDrafts = [];
    state.highAttentionRuns = [];
    renderHighAttentionDrafts();
    renderHighAttentionRuns();
    return;
  }
  const [drafts, runs] = await Promise.all([
    api(`/admin/api/high-attention/drafts?source_day=${encodeURIComponent(day)}`),
    api(`/admin/api/high-attention?source_day=${encodeURIComponent(day)}`),
  ]);
  state.highAttentionDrafts = drafts;
  state.highAttentionRuns = runs;
  renderHighAttentionDrafts();
  renderHighAttentionRuns();
}

async function loadHighAttentionWorkspace() {
  const days = await api("/admin/api/high-attention/days");
  if (state.highAttentionDay && !days.some((item) => item.day === state.highAttentionDay)) {
    days.unshift({day: state.highAttentionDay, draft_count: 0});
  }
  $("highAttentionDay").innerHTML = `<option value="">انتخاب روز نهایی‌سازی</option>` + days.map((item) => `<option value="${esc(item.day)}">${esc(highAttentionDayLabel(item.day))} · ${n(item.draft_count)} خبر نهایی</option>`).join("");
  $("highAttentionDay").value = state.highAttentionDay;
  syncJalaliFlowInput("highAttentionJalaliDate", state.highAttentionDay);
  $("highAttentionDayHint").textContent = days.length
    ? `${n(days.length)} روز دارای خبر نهایی برای بررسی پربازتاب در دسترس است.`
    : "روز را از تقویم انتخاب کنید یا ابتدا خبر را نهایی کنید.";
  await loadHighAttentionDay();
}

async function generateHighAttention() {
  const drafts = selectedHighAttentionDrafts();
  if (!state.highAttentionDay || !drafts.length) return toast("روز و حداقل یک خبر نهایی را انتخاب کنید.", true);
  const button = $("generateHighAttention");
  const originalText = button.textContent;
  button.disabled = true;
  button.textContent = "در حال تولید با API شماره ۸…";
  try {
    const run = await api("/admin/api/high-attention/generate", {method: "POST", body: JSON.stringify({
      source_day: state.highAttentionDay, draft_ids: drafts.map((item) => item.draft_id),
    })});
    state.currentHighAttention = run;
    renderHighAttentionEditor(run);
    await loadHighAttentionDay();
    state.currentHighAttention = run;
    renderHighAttentionEditor(run);
    renderHighAttentionRuns();
    toast((run.items || []).length ? "پربازتاب تولید شد؛ تیتر و توضیح را بررسی و نهایی کنید." : "برای خبرهای انتخاب‌شده محور پربازتابی پیدا نشد.");
  } catch (error) {
    toast(error.message, true);
  } finally {
    button.disabled = false;
    button.textContent = originalText;
    updateHighAttentionSelection();
  }
}

function highAttentionEditorItems() {
  if (!state.currentHighAttention) return [];
  return (state.currentHighAttention.items || []).map((item) => ({
    high_attention_item_id: item.high_attention_item_id,
    title: document.querySelector(`[data-high-attention-title="${item.high_attention_item_id}"]`)?.value.trim() || "",
    summary: document.querySelector(`[data-high-attention-summary="${item.high_attention_item_id}"]`)?.value.trim() || "",
  }));
}

async function saveHighAttention({quiet = false} = {}) {
  const run = state.currentHighAttention;
  if (!run || run.status !== "draft") return null;
  const items = highAttentionEditorItems();
  if (!items.length) throw new Error("محوری برای ذخیره وجود ندارد.");
  const saved = await api(`/admin/api/high-attention/${run.high_attention_run_id}/items`, {method: "PUT", body: JSON.stringify({items})});
  state.currentHighAttention = saved;
  renderHighAttentionEditor(saved);
  if (!quiet) toast("تغییرات پربازتاب ذخیره شد.");
  return saved;
}

async function finalizeHighAttention() {
  try {
    const saved = await saveHighAttention({quiet: true});
    if (!saved) return;
    const result = await api(`/admin/api/high-attention/${saved.high_attention_run_id}/finalize`, {method: "POST"});
    state.currentHighAttention = {...saved, ...result, status: "finalized"};
    renderHighAttentionEditor(state.currentHighAttention);
    await loadHighAttentionDay();
    toast("پربازتاب نهایی شد و اکنون در بخش خبرنامه قابل انتخاب است.");
  } catch (error) { toast(error.message, true); }
}

async function openHighAttentionRun(runId) {
  try {
    const run = await api(`/admin/api/high-attention/${runId}`);
    state.currentHighAttention = run;
    renderHighAttentionEditor(run);
    renderHighAttentionRuns();
  } catch (error) { toast(error.message, true); }
}

async function loadBulletinHighAttentionItems() {
  const day = state.bulletinFinalizationDay;
  state.bulletinHighAttentionItems = day
    ? await api(`/admin/api/high-attention/finalized-items?source_day=${encodeURIComponent(day)}`)
    : [];
  const container = $("finalizedHighAttentionItems");
  container.innerHTML = !day
    ? `<div class="empty-mini">ابتدا روز نهایی‌سازی را انتخاب کنید.</div>`
    : state.bulletinHighAttentionItems.length ? state.bulletinHighAttentionItems.map((item) => `
      <label class="sortable-item"><span class="drag-handle">◉</span><div><b>${esc(item.title)}</b><p>${esc(item.summary)}</p></div><input class="bulletin-high-attention-check" type="checkbox" value="${item.high_attention_item_id}" onchange="updateBulletinPreview()"></label>`).join("")
    : `<div class="empty-mini">برای این روز پربازتاب نهایی‌شده‌ای وجود ندارد.</div>`;
  updateBulletinPreview();
}

function selectedBulletinHighAttentionItems() {
  const ids = [...document.querySelectorAll(".bulletin-high-attention-check:checked")].map((item) => Number(item.value));
  return ids.map((id) => state.bulletinHighAttentionItems.find((item) => item.high_attention_item_id === id)).filter(Boolean);
}

function bulletinDayInfo(draft) {
  const value = String(draft.flow_date || "").trim();
  if (!value) return null;
  return {value, label: jalaliInputDate(draft.finalized_at)};
}

function renderBulletinDayPicker() {
  const daysByValue = new Map();
  state.bulletinDrafts.forEach((draft) => {
    const info = bulletinDayInfo(draft);
    if (!info) return;
    const current = daysByValue.get(info.value) || {...info, count: 0};
    current.count += 1;
    daysByValue.set(info.value, current);
  });
  const days = [...daysByValue.values()].sort((left, right) => right.value.localeCompare(left.value));
  if (state.bulletinFinalizationDay && !daysByValue.has(state.bulletinFinalizationDay)) {
    days.unshift({
      value: state.bulletinFinalizationDay,
      label: highAttentionDayLabel(state.bulletinFinalizationDay),
      count: 0,
    });
  }
  const daySelect = $("bulletinFinalizationDay");
  daySelect.innerHTML = `<option value="">همهٔ خبرهای نهایی‌شده</option>` + days.map((item) =>
    `<option value="${esc(item.value)}">${esc(item.label)} · ${n(item.count)} خبر نهایی</option>`
  ).join("");
  daySelect.value = state.bulletinFinalizationDay;
  daySelect.disabled = false;
  syncJalaliFlowInput("bulletinJalaliDate", state.bulletinFinalizationDay);
  $("bulletinDayHint").textContent = days.length
    ? "برای نمایش خبرهای نهایی‌شدهٔ یک روز، روز را از فهرست یا تقویم انتخاب کنید؛ خروجی فقط از خبرهایی ساخته می‌شود که خودتان تیک زده‌اید."
    : "روز را از تقویم انتخاب کنید یا ابتدا خبر را نهایی کنید.";
}

function renderFinalizedDraftsForDay() {
  const selectedDay = state.bulletinFinalizationDay;
  const drafts = selectedDay
    ? state.bulletinDrafts.filter((draft) => bulletinDayInfo(draft)?.value === selectedDay)
    : state.bulletinDrafts;
  state.drafts = drafts;
  const toolbar = $("bulletinDraftsToolbar");
  const selectAll = $("selectAllFinalizedDrafts");
  if (!drafts.length) {
    toolbar?.classList.add("hidden");
    if (selectAll) selectAll.checked = false;
  } else {
    toolbar?.classList.remove("hidden");
    if (selectAll) selectAll.checked = false;
  }
  $("bulletinDraftsSelectionHint").textContent = drafts.length
    ? `${n(drafts.length)} خبر نهایی برای انتخاب`
    : "خبر نهایی در دسترس نیست";
  $("finalizedDrafts").innerHTML = drafts.length ? drafts.map((item, index) => `
    <label class="sortable-item"><span class="drag-handle">⋮⋮</span><div><b>${esc(item.title || `${item.person_name || "خبر"} — ${item.topic_name || "بدون موضوع"}`)}</b><p>${esc(item.person_name)} · ${esc(item.topic_name)} · جزئیات موضع: ${esc(item.detail || item.summary_title || "نامشخص")}</p></div><input class="bulletin-check" type="checkbox" value="${item.draft_id}" data-index="${index}" onchange="syncBulletinSelectAll()"></label>`).join("") : `<div class="empty-mini">هنوز خبر نهایی وجود ندارد.</div>`;
  updateBulletinPreview();
}

function selectedFinalDrafts() {
  const ids = [...document.querySelectorAll(".bulletin-check:checked")].map((item) => Number(item.value));
  return ids.map((id) => state.drafts.find((item) => item.draft_id === id)).filter(Boolean);
}

function toggleAllFinalizedDrafts(checked) {
  document.querySelectorAll(".bulletin-check").forEach((input) => { input.checked = Boolean(checked); });
  updateBulletinPreview();
}

function syncBulletinSelectAll() {
  const boxes = [...document.querySelectorAll(".bulletin-check")];
  const selectAll = $("selectAllFinalizedDrafts");
  if (selectAll) {
    selectAll.checked = Boolean(boxes.length) && boxes.every((input) => input.checked);
    selectAll.indeterminate = boxes.some((input) => input.checked) && !selectAll.checked;
  }
  updateBulletinPreview();
}

function updateBulletinPreview() {
  $("previewTitle").textContent = $("bulletinTitle").value || "خبرنامه گرایه";
  const selected = selectedFinalDrafts();
  const highAttention = selectedBulletinHighAttentionItems();
  const newsPreview = selected.map((item, index) => `<article class="preview-news"><h4>${n(index + 1)}. ${esc(item.title || `${item.person_name || "خبر"} — ${item.topic_name || "بدون موضوع"}`)}</h4><p><b>${esc(item.person_name || "")}</b> — ${esc(item.summary_sentence || item.summary_paragraph || "")}</p><small>جزئیات موضع: ${esc(item.detail || item.summary_title || "نامشخص")}</small></article>`).join("");
  const highAttentionPreview = highAttention.map((item, index) => `<article class="preview-news high-attention-preview"><span class="eyebrow">پربازتاب</span><h4>${n(index + 1)}. ${esc(item.title)}</h4><p>${esc(item.summary)}</p></article>`).join("");
  $("previewItems").innerHTML = (newsPreview || highAttentionPreview)
    ? `${newsPreview}${highAttentionPreview}`
    : `<div class="empty-mini">خبرها یا پربازتاب‌های نهایی انتخاب‌شده اینجا نمایش داده می‌شوند.</div>`;
}

function bulletinOutputState(run) {
  const stage = String(run.current_stage || "");
  if (["exports_queued", "build_json", "render_html_layout"].includes(stage)) {
    return `<span class="bulletin-run-pending">در حال تولید خروجی‌ها…</span>`;
  }
  if (stage === "export_failed") {
    const reason = String(run.error_text || "خطای نامشخص");
    return `<span class="bulletin-run-error" title="${esc(reason)}">تولید خروجی ناموفق بود</span>`;
  }
  const ready = Number(run.page_layout_html_ready) && Number(run.page_layout_word_ready) && Number(run.page_layout_pdf_ready);
  return ready
    ? `<span class="bulletin-run-ready">خروجی‌ها آماده‌اند</span>`
    : `<span class="bulletin-run-pending">خروجی هنوز کامل نشده است</span>`;
}

function renderBulletinRunActions(run) {
  const links = [];
  if (Number(run.page_layout_html_ready)) links.push(`<a href="/admin/preview/bulletins/${run.id}/layout" target="_blank" rel="noopener">پیش‌نمایش HTML</a>`);
  if (Number(run.page_layout_pdf_ready)) links.push(`<a href="/admin/download/bulletins/${run.id}/classic.pdf">PDF صفحه‌آرایی</a>`);
  if (Number(run.page_layout_word_ready)) links.push(`<a href="/admin/download/bulletins/${run.id}/concise.docx">Word صفحه‌آرایی</a>`);
  if (links.length) links.push(`<a href="/admin/download/bulletins/${run.id}/all.zip">بسته ZIP</a>`);
  return `<div class="bulletin-run-actions"><div class="download-links">${links.join("") || "—"}</div><div class="bulletin-run-controls"><button class="text-button" onclick="regenerateBulletinOutputs(${Number(run.id)})">تولید مجدد</button><button class="text-button bulletin-run-delete" onclick="deleteBulletinRun(${Number(run.id)})">حذف</button></div></div>`;
}

async function loadBulletinWorkspace() {
  const [drafts, runs] = await Promise.all([
    api("/admin/api/editorial-drafts?status=finalized"), api("/admin/api/bulletins"),
  ]);
  state.bulletinDrafts = drafts;
  state.bulletinRuns = runs;
  renderBulletinDayPicker();
  renderFinalizedDraftsForDay();
  await loadBulletinHighAttentionItems();
  $("bulletinRows").innerHTML = runs.length ? runs.map((run) => `
    <tr>
      <td>${n(run.id)}</td><td>${n(run.issue_number || "—")}</td><td>${fdate(run.created_at)}</td>
      <td><span class="status-pill ${esc(run.status)}">${statusLabel(run.status)}</span><br>${bulletinOutputState(run)}</td>
      <td>${n(run.item_count)}</td><td>${renderBulletinRunActions(run)}</td>
    </tr>`).join("") : `<tr><td colspan="6">هنوز خبرنامه‌ای ساخته نشده است.</td></tr>`;
  updateBulletinPreview();
}

function startBulletinOutputPolling(runId, attempts = 90) {
  clearTimeout(state.bulletinOutputPollTimer);
  const poll = async (remaining) => {
    try {
      await loadBulletinWorkspace();
      if (state.page === "quick-start") await refreshQuickStartOutput();
      const run = state.bulletinRuns.find((item) => Number(item.id) === Number(runId));
      const stage = String(run?.current_stage || "");
      if (!run || stage === "exports_ready") {
        if (run) toast("پیش‌نمایش HTML، Word و PDF خبرنامه آماده است.");
        return;
      }
      if (stage === "export_failed") {
        toast(`تولید خروجی ناموفق بود: ${run.error_text || "خطای نامشخص"}`, true);
        return;
      }
    } catch (error) {
      if (remaining <= 1) { toast(error.message, true); return; }
    }
    if (remaining > 1) state.bulletinOutputPollTimer = setTimeout(() => poll(remaining - 1), 2000);
  };
  state.bulletinOutputPollTimer = setTimeout(() => poll(attempts), 700);
}

async function createManualBulletin() {
  const draftIds = selectedFinalDrafts().map((item) => item.draft_id);
  const highAttentionItemIds = selectedBulletinHighAttentionItems().map((item) => item.high_attention_item_id);
  if (!draftIds.length) return toast("حداقل یک خبر نهایی را انتخاب کنید.", true);
  try {
    const data = await api("/admin/api/editorial-bulletins", {method: "POST", body: JSON.stringify({
      draft_ids: draftIds, title: $("bulletinTitle").value.trim() || null,
      issue_number: Number($("bulletinIssue").value) || null, report_mode: $("bulletinMode").value,
      high_attention_item_ids: highAttentionItemIds,
      introduction: $("bulletinIntroduction").value.trim() || null,
    })});
    toast(`خبرنامه ${n(data.run_id)} ثبت شد؛ خروجی‌ها در حال آماده‌سازی هستند.`);
    await loadBulletinWorkspace();
    startBulletinOutputPolling(data.run_id);
  } catch (error) { toast(error.message, true); }
}

async function regenerateBulletinOutputs(runId) {
  if (!confirm("خروجی‌های HTML، Word و PDF این خبرنامه دوباره ساخته شوند؟")) return;
  try {
    toast("بازسازی خروجی‌ها آغاز شد؛ لطفاً چند لحظه صبر کنید.");
    await api(`/admin/api/bulletins/${Number(runId)}/export`, {method: "POST"});
    await loadBulletinWorkspace();
    toast("خروجی‌های خبرنامه با موفقیت بازسازی شدند.");
  } catch (error) { toast(error.message, true); }
}

async function deleteBulletinRun(runId) {
  if (!confirm("این اجرا و تمام فایل‌های خروجی آن حذف شود؟ این کار قابل بازگشت نیست.")) return;
  try {
    const result = await api(`/admin/api/bulletins/${Number(runId)}`, {method: "DELETE"});
    await loadBulletinWorkspace();
    toast(`اجرای خبرنامه حذف شد (${n(result.files_removed || 0)} فایل).`);
  } catch (error) { toast(error.message, true); }
}

async function loadPeople() {
  const [people, categories] = await Promise.all([
    api("/admin/api/people"),
    api("/admin/api/people/categories").catch(() => ({ options: [] })),
  ]);
  state.people = people;
  state.personCategories = categories.options || [];
  fillPersonCategorySelects();
  renderPeople();
}
function fillPersonCategorySelects(selected = "") {
  const options = state.personCategories || [];
  const html = [`<option value="">انتخاب دسته</option>`, ...options.map((item) => `<option value="${esc(item)}">${esc(item)}</option>`)].join("");
  for (const id of ["personCategory", "quickRegistryPersonCategory", "deskPersonCategory", "categoryRenameOld"]) {
    const node = $(id);
    if (!node) continue;
    const current = selected || node.value || "";
    if (node.tagName === "SELECT") {
      const extra = current && !options.includes(current) ? `<option value="${esc(current)}">${esc(current)}</option>` : "";
      const createOption = id === "personCategory"
        ? '<option value="__new_person_category__">+ تعریف دستهٔ جدید…</option>'
        : "";
      node.innerHTML = html + extra + createOption;
      node.value = current;
    }
  }
}

async function createPersonCategory({value, prompt = false} = {}) {
  const raw = value ?? (prompt ? window.prompt("عنوان دستهٔ جدید را وارد کنید.") : $("categoryCreateNew")?.value);
  if (raw == null) {
    fillPersonCategorySelects("");
    return;
  }
  const category = raw.trim().replace(/\s+/g, " ");
  if (!category) return toast("عنوان دسته نمی‌تواند خالی باشد.", true);
  const result = await api("/admin/api/people/categories", {
    method: "POST",
    body: JSON.stringify({title: category}),
  });
  await loadPeople();
  fillPersonCategorySelects(result.title);
  $("personCategory").value = result.title;
  if ($("categoryCreateNew")) $("categoryCreateNew").value = "";
  toast(result.created ? "دستهٔ جدید ایجاد و در همهٔ فهرست‌ها قابل انتخاب شد." : "این دسته از قبل در فهرست موجود است.");
}
function renderPeople() {
  const query = $("peopleSearch").value.trim().toLowerCase();
  const rows = state.people.filter((item) => !query || `${item.full_name} ${item.position || ""} ${item.category || ""} ${item.aliases || ""}`.toLowerCase().includes(query));
  $("peopleRows").innerHTML = rows.length ? rows.map((item) => {
    const initials = String(item.full_name || "؟").trim().split(/\s+/).slice(0, 2).map((part) => part[0]).join("") || "؟";
    const avatar = `<span class="person-avatar"><img src="/admin/portraits/${item.person_id}" alt="تصویر ${esc(item.full_name)}" onload="this.nextElementSibling.style.display='none'" onerror="this.remove()"><i>${esc(initials)}</i></span>`;
    const inactive = Number(item.active) === 0;
    return `<tr class="${inactive ? "inactive-row" : ""}"><td><div class="person-name-cell">${avatar}<b>${esc(item.full_name)}</b></div></td><td>${esc(item.position || "—")}</td><td>${esc(item.category || "—")}</td><td><span class="status-pill ${item.registry_status === "inside" ? "approved" : "pending"}">${item.registry_status === "inside" ? "داخل شناسنامه" : "خارج شناسنامه"}</span>${inactive ? ` <span class="status-pill rejected">حذف‌شده</span>` : ""}</td><td>${esc(item.aliases || "—")}</td><td>${n(item.channel_count)}</td><td><div class="table-actions"><button class="text-button" onclick="viewPerson(${item.person_id})">پروفایل</button><button class="text-button" onclick="editPerson(${item.person_id})">ویرایش</button>${inactive ? "" : `<button class="text-button danger-text" onclick="deletePerson(${item.person_id})">حذف</button>`}</div></td></tr>`;
  }).join("") : `<tr><td colspan="7">شخصی پیدا نشد.</td></tr>`;
}
async function deletePerson(id) {
  const person = state.people.find((item) => item.person_id === id);
  if (!person) return;
  if (!confirm(`شناسنامه «${person.full_name}» حذف شود؟ پیوند پیام‌ها قطع و شخص غیرفعال می‌شود.`)) return;
  try {
    await api(`/admin/api/people/${id}`, { method: "DELETE" });
    await loadPeople();
    toast("شناسنامه حذف شد.");
  } catch (error) {
    toast(error.message, true);
  }
}

function personProfileMarkup(person) {
  const aliases = (person.alias_rows || []).map((item) => item.alias_text).filter((item) => item !== person.full_name);
  const channels = person.channels || [];
  const news = person.news || [];
  const status = person.registry_status === "inside" ? "داخل شناسنامه" : "خارج شناسنامه";
  const initials = String(person.full_name || "؟").trim().split(/\s+/).slice(0, 2).map((part) => part[0]).join("") || "؟";
  const channelMarkup = channels.length
    ? channels.map((item) => `<li><b>${esc(item.username ? `@${item.username}` : (item.chat_id || "کانال بدون نام"))}</b>${item.chat_id ? `<small>شناسه: ${esc(item.chat_id)}</small>` : ""}</li>`).join("")
    : `<li class="empty-mini">کانال وابسته‌ای ثبت نشده است.</li>`;
  const newsMarkup = news.length
    ? news.map((item) => `<article class="person-profile-news-card"><header><b>${esc(item.detected_topic_name || "بدون موضوع")}</b><span class="status-pill ${esc(item.status || "pending")}">${esc(statusLabel(item.status))}</span></header><p>${esc(item.text || item.caption || "[پیام رسانه‌ای]")}</p><small>${esc(fdate(item.published_at))}</small></article>`).join("")
    : `<div class="empty-mini">هنوز خبری به این شخص منتسب نشده است.</div>`;
  return `<header class="person-profile-hero"><div class="person-profile-portrait"><img src="/admin/portraits/${person.person_id}" alt="تصویر ${esc(person.full_name)}" onerror="this.remove()"><b>${esc(initials)}</b></div><div><span class="eyebrow">پروفایل شخص</span><h2>${esc(person.full_name)}</h2><p>${esc(person.position || "بدون سمت ثبت‌شده")}</p><span class="status-pill ${person.registry_status === "inside" ? "approved" : "pending"}">${status}</span></div><div class="person-profile-count"><b>${n(news.length)}</b><small>خبر منتسب</small></div></header><section class="detail-grid person-profile-facts"><div class="detail-field"><small>دسته</small>${esc(person.category || "—")}</div><div class="detail-field"><small>اولویت</small>${n(person.priority)}</div><div class="detail-field"><small>نام‌های دیگر</small>${esc(aliases.join("، ") || "—")}</div><div class="detail-field"><small>وضعیت ثبت</small>${status}</div></section><section class="person-profile-section"><div class="profile-section-title"><div><h4>کانال‌های وابسته</h4><p>منابعی که برای این شخص ثبت شده‌اند.</p></div><span>${n(channels.length)} کانال</span></div><ul class="person-profile-channels">${channelMarkup}</ul></section><section class="person-profile-section"><div class="profile-section-title"><div><h4>خبرهای منتسب</h4><p>همهٔ پیام‌های منتسب به این شخص، از جدید به قدیم.</p></div><span>${n(news.length)} خبر</span></div><div class="person-profile-news">${newsMarkup}</div></section>`;
}

async function loadPersonProfile() {
  const id = Number(state.personProfileId || state.pageSection);
  if (!Number.isInteger(id) || id < 1) return showPage("people");
  try {
    const person = await api(`/admin/api/people/${id}`);
    state.personProfileId = Number(person.person_id);
    $("personProfileContent").innerHTML = personProfileMarkup(person);
    $("editProfilePerson").disabled = false;
  } catch (error) {
    $("personProfileContent").innerHTML = `<div class="empty-mini">پروفایل شخص در دسترس نیست.</div>`;
    $("editProfilePerson").disabled = true;
    toast(error.message, true);
  }
}

function viewPerson(id) {
  showPage("person-profile", String(id));
}
async function editPerson(id) {
  let person = state.people.find((item) => item.person_id === id);
  if (!person) {
    try {
      person = await api(`/admin/api/people/${id}`);
    } catch (error) {
      toast(error.message, true);
      return;
    }
  }
  $("personForm").classList.remove("hidden");
  $("personId").value = person.person_id;
  $("personName").value = person.full_name || "";
  $("personPosition").value = person.position || "";
  fillPersonCategorySelects(person.category || "");
  $("personCategory").value = person.category || "";
  $("personRegistry").value = person.registry_status || "inside";
  $("personAliases").value = person.aliases || "";
  $("personUsername").value = "";
  window.scrollTo({top: 0, behavior: "smooth"});
}
function clearPersonForm() {
  $("personForm").reset(); $("personId").value = ""; fillPersonCategorySelects(""); $("personForm").classList.add("hidden");
}

const permissionTitles = {
  "*": "همه دسترسی‌ها", "dashboard.view": "مشاهده داشبورد",
  "messages.view": "مشاهده پیام‌ها", "messages.review": "امتیازدهی و تحلیل پیام",
  "editorial.view": "مشاهده تدوین", "editorial.manage": "مدیریت تدوین",
  "bulletins.view": "مشاهده خبرنامه", "bulletins.manage": "ساخت خبرنامه",
  "bulletins.export": "دریافت خروجی", "people.view": "مشاهده اشخاص",
  "people.manage": "مدیریت اشخاص", "sources.view": "مشاهده منابع",
  "sources.manage": "مدیریت منابع", "settings.manage": "تنظیمات و مدل‌ها",
  "system.manage": "مدیریت سامانه", "users.manage": "مدیریت کاربران",
};
function renderSenderProfileOptions(selected = "") {
  const currentUserId = Number($("adminUserId").value || 0);
  $("adminSenderKey").innerHTML = `<option value="">بدون پیوند به ارسال‌کننده</option>` + state.senderCandidates.map((item) => {
    const ownedByAnother = item.profile_user_id && Number(item.profile_user_id) !== currentUserId;
    const name = item.sender_name || "ارسال‌کننده نامشخص";
    const username = item.sender_username ? ` · @${item.sender_username}` : "";
    const suffix = ownedByAnother ? ` · متصل به ${item.profile_full_name || "پروفایل دیگر"}` : "";
    return `<option value="${esc(item.sender_key)}" ${ownedByAnother ? "disabled" : ""}>${esc(name + username + suffix)}</option>`;
  }).join("");
  $("adminSenderKey").value = selected || "";
}
function renderRolePermissions() {
  const role = state.roles.find((item) => item.role === $("adminRole").value);
  $("rolePermissions").innerHTML = role ? `<b>مجوزهای نقش ${esc(role.title)}:</b><br>${role.permissions.map((item) => `<span class="permission-tag">${esc(permissionTitles[item] || item)}</span>`).join("")}` : "";
}

function applySenderProfileChoice() {
  const sender = state.senderCandidates.find((item) => item.sender_key === $("adminSenderKey").value);
  if (!sender || $("adminUserId").value || $("adminFullName").value.trim()) return;
  $("adminFullName").value = sender.sender_name || "";
}

function userProfileInitials(user) {
  return String(user?.full_name || user?.username || "ک").trim().split(/\s+/).slice(0, 2)
    .map((part) => part[0]).join("") || "ک";
}

function adminUserAvatar(user, extraClass = "") {
  const revision = user?.avatar_updated_at ? `?v=${encodeURIComponent(user.avatar_updated_at)}` : "";
  const label = esc(user?.full_name || user?.username || "کاربر");
  return `<span class="admin-user-avatar ${esc(extraClass)}"><img src="/admin/user-portraits/${Number(user?.user_id)}${revision}" alt="تصویر ${label}" onload="this.nextElementSibling.style.display='none'" onerror="this.remove()"><i>${esc(userProfileInitials(user))}</i></span>`;
}

async function loadAdminUsers() {
  const [users, roles, senders] = await Promise.all([
    api("/admin/api/users"), api("/admin/api/users/roles"), api("/admin/api/users/senders"),
  ]);
  state.adminUsers = users; state.roles = roles; state.senderCandidates = senders;
  state.senderProfiles = new Map((senders || []).filter((item) => item.sender_key).map((item) => [String(item.sender_key), item]));
  $("adminRole").innerHTML = roles.map((item) => `<option value="${esc(item.role)}">${esc(item.title)}</option>`).join("");
  renderSenderProfileOptions($("adminSenderKey").value);
  $("usersRows").innerHTML = users.length ? users.map((item) => {
    const linked = state.senderCandidates.find((sender) => sender.sender_key === item.sender_key);
    const linkedLabel = linked
      ? `${linked.sender_name || "ارسال‌کننده"}${linked.sender_username ? ` · @${linked.sender_username}` : ""}`
      : (item.sender_key ? "هویت ثبت‌شده" : "—");
    return `<tr><td>${adminUserAvatar(item, "table")}</td><td><b>${esc(item.username)}</b></td><td>${esc(item.full_name)}</td><td>${esc(linkedLabel)}</td><td>${esc(item.role_title)}</td><td><span class="status-pill ${Number(item.active) ? "approved" : "rejected"}">${Number(item.active) ? "فعال" : "غیرفعال"}</span></td><td>${fdate(item.last_login_at)}</td><td>${esc(item.created_by || "—")}</td><td><div class="table-actions"><button class="text-button" onclick="viewAdminUserProfile(${item.user_id})">نمایه</button><button class="text-button" onclick="editAdminUser(${item.user_id})">ویرایش</button></div></td></tr>`;
  }).join("") : `<tr><td colspan="9">کاربری ثبت نشده است.</td></tr>`;
  renderRolePermissions();
}

function editAdminUser(id) {
  const user = state.adminUsers.find((item) => item.user_id === id);
  if (!user) return;
  $("userForm").classList.remove("hidden");
  $("adminUserId").value = user.user_id;
  $("adminUsername").value = user.username;
  $("adminFullName").value = user.full_name;
  $("adminRole").value = user.role;
  $("adminPassword").value = "";
  $("adminUserPortrait").value = "";
  $("adminActive").checked = Boolean(Number(user.active));
  renderSenderProfileOptions(user.sender_key || "");
  renderRolePermissions();
  window.scrollTo({top: 0, behavior: "smooth"});
}

function clearAdminUserForm() {
  $("userForm").reset(); $("adminUserId").value = ""; $("adminActive").checked = true;
  $("adminUserPortrait").value = "";
  renderSenderProfileOptions("");
  $("userForm").classList.add("hidden"); renderRolePermissions();
}

async function viewAdminUserProfile(id) {
  try {
    const profile = await api(`/admin/api/users/${id}/profile?days=${Number($("monitoringDays")?.value || 30)}`);
    $("userProfileDetail").innerHTML = userProfileMarkup(profile);
    $("userProfileDialog").showModal();
  } catch (error) { toast(error.message, true); }
}

function renderCrawlerRecoveryStatus(payload) {
  const target = $("crawlerRecoveryStatus");
  const button = $("startCrawlerRecovery");
  if (!target || !button) return;
  const run = payload?.run || null;
  const running = Boolean(payload?.process_running);
  button.disabled = running;
  button.textContent = running ? "بازیابی در حال اجراست…" : "بازیابی صف ۴۸ ساعت";
  if (!run) {
    target.className = "crawler-recovery-status";
    target.textContent = "هنوز بازیابی صف ۴۸ساعته اجرا نشده است.";
    return;
  }
  const status = running ? "running" : String(run.status || "queued");
  const statusLabel = {
    queued: "در صف اجرا", running: "در حال بازیابی صف ربات", completed: "پایان‌یافته",
    failed: "ناموفق", cancelled: "متوقف‌شده",
  }[status] || status;
  const details = run.details || {};
  const imported = Array.isArray(details.imported_messages) ? details.imported_messages : [];
  const messageList = imported.length
    ? `<details><summary>${n(imported.length)} پیام جاافتادهٔ واردشده را ببینید</summary><ol class="crawler-recovery-list">${imported.map((item) => `<li><b>${esc(item.channel || "کانال")}</b> · ${esc(fdate(item.published_at) || item.published_at || "زمان نامشخص")}<br><span>${esc(item.text || "پیام بدون متن")}</span></li>`).join("")}</ol></details>`
    : "";
  target.className = `crawler-recovery-status ${esc(status)}`;
  const modeNote = "فقط صف API ربات و آرشیو خامِ قبلی بررسی می‌شوند؛ پیام خارج از این دو منبع بازیابی نمی‌شود.";
  target.innerHTML = `<b>${esc(statusLabel)}</b><p>${status === "running" ? "صف ربات و آرشیو خام در حال تطبیق هستند؛ پس از پایان، نتیجه در همین بخش نمایش داده می‌شود." : (run.error_text ? esc(run.error_text) : modeNote)}</p><div class="recovery-counts"><span>بررسی‌شده: <b>${n(run.scanned_count || 0)}</b></span><span>از قبل ثبت‌شده: <b>${n(run.existing_count || 0)}</b></span><span>افزوده‌شده: <b>${n(run.imported_count || 0)}</b></span><span>ناموفق: <b>${n(run.failed_count || 0)}</b></span></div>${messageList}`;
}

function stopBotQueueRecoveryPolling() {
  if (state.botQueueRecoveryPollTimer) {
    clearInterval(state.botQueueRecoveryPollTimer);
    state.botQueueRecoveryPollTimer = null;
  }
}

function startBotQueueRecoveryPolling() {
  stopBotQueueRecoveryPolling();
  state.botQueueRecoveryPollTimer = setInterval(async () => {
    try {
      const result = await api("/admin/api/crawler/recovery");
      renderCrawlerRecoveryStatus(result);
      if (!result?.process_running) stopBotQueueRecoveryPolling();
    } catch (_) {
      // The regular refresh button remains available; polling failures should
      // not hide the last trustworthy result from the operator.
      stopBotQueueRecoveryPolling();
    }
  }, 1500);
}

async function loadSystem() {
  const [data, recovery, gapgpt] = await Promise.all([
    api("/admin/api/system"),
    api("/admin/api/crawler/recovery"),
    api("/admin/api/system/gapgpt-status").catch((error) => ({ok: false, error: error.message, groups: []})),
  ]);
  const db = data.database || {}, config = data.configuration || {}, counts = data.stats?.counts || {};
  $("systemMetrics").innerHTML = [
    ["پیام ذخیره‌شده", counts.total, "◫", ""], ["منبع فعال", counts.active_sources, "⌁", "green"],
    ["کلید هوش", config.ai_key_count, "✦", "amber"], ["رویداد اخیر", (data.events || []).length, "≡", "red"],
  ].map(([label, value, icon, tone]) => `<article class="metric-card"><span class="metric-icon ${tone}">${icon}</span><div><b>${n(value)}</b><small>${label}</small></div></article>`).join("");
  const checks = [
    ["پایگاه داده", String(db.quick_check || "").toLowerCase() === "ok", db.quick_check || "نامشخص"],
    ["ساختار جداول", !(db.missing_tables || []).length, (db.missing_tables || []).length ? `${db.missing_tables.length} جدول ناقص` : "کامل"],
    ["هوش مصنوعی", Boolean(config.ai_ready), config.ai_ready ? `${config.ai_provider} / ${config.ai_model}` : "حالت آفلاین فعال"],
    ["زمان‌بندی", Boolean(config.scheduler_enabled), config.scheduler_enabled ? "فعال" : "غیرفعال"],
    ["دریافت بله", config.bot_mode !== "disabled", statusLabel(config.bot_mode)],
    ["MiniApp", Boolean(config.miniapp_ready), config.miniapp_ready ? "آماده" : "تنظیم نشده"],
    [
      "کرولر Selenium",
      Boolean(config.crawler?.enabled && config.crawler?.destination_configured),
      config.crawler?.enabled
        ? `${config.crawler?.pid_recorded ? "اجراشده" : "منتظر اجرا"} · هر ${n(Math.round((config.crawler?.repeat_seconds || 1800) / 60))} دقیقه`
        : "غیرفعال",
    ],
  ];
  const profiles = config.ai_profiles || {};
  for (const [key, title] of [["analysis", "مدل تحلیل خبر"], ["editorial", "مدل تدوین و خلاصه"]]) {
    const profile = profiles[key] || {};
    checks.push([
      title,
      Boolean(profile.enabled),
      profile.enabled
        ? `${profile.provider} · ${profile.model} · ${profile.pool?.key_count || 0} کلید`
        : (profile.configuration_error || `${profile.model || "مدل تنظیم نشده"} · غیرفعال`),
    ]);
  }
  $("healthChecks").innerHTML = checks.map(([label, ok, text]) => `<div class="health-item ${ok ? "" : "warning"}"><span class="health-mark">${ok ? "✓" : "!"}</span><div><b>${esc(label)}</b><small>${esc(text)}</small></div></div>`).join("");
  const usage = data.ai_usage || {};
  const money = (value) => value == null ? "تعریف نشده" : `$${Number(value).toFixed(6)}`;
  $("aiUsageTotal").textContent = money(usage.total_cost_usd || 0);
  $("aiUsageRows").innerHTML = (usage.models || []).length
    ? usage.models.map((item) => `<tr><td>${esc(item.model)}</td><td>${n(item.input_tokens)}</td><td>${n(item.output_tokens)}</td><td>${item.input_price_per_million_usd == null ? "تعریف نشده" : `$${item.input_price_per_million_usd}`}</td><td>${item.output_price_per_million_usd == null ? "تعریف نشده" : `$${item.output_price_per_million_usd}`}</td><td>${money(item.total_cost_usd)}</td></tr>`).join("")
    : `<tr><td colspan="6">هنوز مصرف توکن ثبت نشده است.</td></tr>`;
  $("eventList").innerHTML = (data.events || []).map((item) => `<div class="event-row ${String(item.level || "").toLowerCase()}"><b>${esc(item.component)} · ${esc(item.event_type)}</b><p>${esc(item.message || "")}</p><p>${fdate(item.created_at)}</p></div>`).join("") || `<div class="empty-mini">رویدادی ثبت نشده است.</div>`;
  $("connectionLabel").textContent = String(db.quick_check || "").toLowerCase() === "ok" ? "سامانه در دسترس است" : "نیازمند بررسی";
  renderCrawlerRecoveryStatus(recovery);
  renderLiveUpdateStatus(data.update || {version: data.version});
  renderGapgptStatus(gapgpt);
}

function gapgptStatusLabel(status) {
  return {operational: "سالم", degraded: "کاهش کیفیت", incident: "اختلال", unknown: "نامشخص"}[status] || status || "نامشخص";
}

function renderGapgptStatus(payload) {
  const box = $("gapgptStatus");
  if (!box) return;
  if (!payload?.ok) {
    box.innerHTML = `<div class="empty-mini">خواندن صفحهٔ وضعیت گپ‌جی‌پی‌تی ممکن نشد. ${esc(payload?.error || "")}</div>`;
    return;
  }
  const groups = payload.groups || [];
  const overall = payload.overall_status || "unknown";
  box.innerHTML = `
    <div class="gapgpt-overall">
      <div><b>وضعیت کلی APIها</b><p style="margin:4px 0 0;color:var(--muted);font-size:12px">آخرین به‌روزرسانی منبع: ${esc(payload.generated_at ? fdate(payload.generated_at) : "—")}</p></div>
      <span class="gapgpt-status-pill ${esc(overall)}">${esc(gapgptStatusLabel(overall))}</span>
    </div>
    ${groups.map((group) => {
      const children = (group.children || []).map((child) => `
        <div class="gapgpt-child">
          <div class="panel-heading" style="margin-bottom:8px"><div><h4>${esc(child.name)}</h4><p>${esc(child.description || "")}</p></div><span class="gapgpt-status-pill ${esc(child.status)}">${esc(gapgptStatusLabel(child.status))}</span></div>
          ${renderGapgptProbes(child.probes || [])}
        </div>`).join("");
      return `<article class="gapgpt-group">
        <div class="panel-heading"><div><h4>${esc(group.name)}</h4><p>${esc(group.description || "")}</p></div><span class="gapgpt-status-pill ${esc(group.status)}">${esc(gapgptStatusLabel(group.status))}</span></div>
        ${children || renderGapgptProbes(group.probes || [])}
      </article>`;
    }).join("")}`;
}

function renderGapgptProbes(probes) {
  return `<div class="gapgpt-probes">${(probes || []).map((item) => `
    <article class="gapgpt-probe">
      <div>
        <b>${esc(item.name || item.id)}</b>
        <small>${esc(item.title || "")}${item.latency_ms != null ? ` · ${n(item.latency_ms)} میلی‌ثانیه` : ""}${item.checked_at ? ` · ${fdate(item.checked_at)}` : ""}</small>
        <div class="gapgpt-uptime" aria-hidden="true">${(item.history || []).map((row) => `<i class="${esc(row.status || "unknown")}" title="${esc(row.date || "")} · ${esc(gapgptStatusLabel(row.status))}"></i>`).join("")}</div>
      </div>
      <span class="gapgpt-status-pill ${esc(item.status || "unknown")}">${esc(gapgptStatusLabel(item.status))}</span>
    </article>`).join("")}</div>`;
}

async function loadApiKeys() {
  const data = await api("/admin/api/api-keys");
  renderApiKeyRows(data.items || []);
}

function renderApiKeyRows(items) {
  const rows = $("apiKeyRows");
  if (!rows) return;
  rows.innerHTML = items.length ? items.map((item) => {
    const revoked = Boolean(item.revoked_at);
    return `<tr class="${revoked ? "inactive-row" : ""}"><td><b>${esc(item.name)}</b></td><td dir="ltr">${esc(item.token_prefix)}…</td><td>${fdate(item.created_at)}</td><td>${item.last_used_at ? fdate(item.last_used_at) : "—"}</td><td><span class="status-pill ${revoked ? "rejected" : "approved"}">${revoked ? "باطل‌شده" : "فعال"}</span></td><td>${revoked ? "" : `<button class="text-button danger-text" type="button" onclick="revokeApiKey(${item.api_key_id})">ابطال</button>`}</td></tr>`;
  }).join("") : `<tr><td colspan="6">هنوز کلیدی ساخته نشده است.</td></tr>`;
}

function showCreatedApiKey(payload) {
  const box = $("apiKeySecretBox");
  if (!box) return;
  box.classList.remove("hidden");
  box.innerHTML = `<b>کلید فقط همین یک‌بار نمایش داده می‌شود.</b><code id="apiKeySecretValue">${esc(payload.token)}</code><div class="button-row"><button type="button" class="outline-button" id="copyApiKey">کپی کلید</button></div>`;
  $("copyApiKey")?.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(payload.token);
      toast("کلید در حافظه کپی شد.");
    } catch (_) {
      toast("کپی خودکار ممکن نشد؛ کلید را دستی کپی کنید.", true);
    }
  });
}

async function revokeApiKey(id) {
  if (!confirm("این کلید باطل شود؟ فراخوانی‌های بعدی با آن رد می‌شود.")) return;
  try {
    await api(`/admin/api/api-keys/${id}`, {method: "DELETE"});
    toast("کلید باطل شد.");
    await loadApiKeys();
  } catch (error) { toast(error.message, true); }
}

function renderLiveUpdateStatus(payload, extraMessage = "") {
  const box = $("liveUpdateStatus");
  if (!box) return;
  const version = payload?.version || state.me?.version || "";
  const applied = payload?.applied;
  const files = Number(payload?.file_count || 0);
  const when = payload?.applied_at ? fdate(payload.applied_at) : "";
  box.innerHTML = extraMessage
    ? `<b>${esc(extraMessage)}</b><p>نسخه جاری: ${esc(faDigits(version) || version || "—")}</p>`
    : applied
      ? `<b>آخرین بسته اعمال‌شده</b><p>نسخه ${esc(faDigits(version) || version)} · ${n(files)} فایل · ${esc(when)}</p>`
      : `<b>آماده دریافت بسته ZIP</b><p>نسخه جاری سامانه ${esc(faDigits(version) || version || "—")} است. data، .env و پشتیبان‌ها جایگزین نمی‌شوند.</p>`;
}

function userProfileMarkup(profile, {selfPortal = false} = {}) {
  const metric = profile.metrics || {};
  const dailyRows = Object.entries(metric.daily || {}).filter(([, count]) => Number(count) > 0).sort(([a], [b]) => String(a).localeCompare(String(b)));
  const largestDay = Math.max(1, ...dailyRows.map(([, count]) => Number(count)));
  const average = metric.average_rating == null ? "بدون امتیاز" : `${n(metric.average_rating)} از ۵`;
  const sender = metric.sender_name || (profile.sender_key ? "هویت ارسال‌کنندهٔ متصل" : "این حساب به ارسال‌کننده‌ای متصل نیست");
  const dailyVisual = dailyRows.length ? dailyRows.map(([date, count]) => {
    const percent = Math.max(7, Math.round((Number(count) / largestDay) * 100));
    return `<div class="profile-day-row"><small>${esc(monitoringDayLabel(date))}</small><div class="profile-day-track"><i style="width:${percent}%"></i></div><b>${n(count)}</b></div>`;
  }).join("") : `<div class="empty-mini">هنوز پیامی برای این حساب یا هویت متصل آن ثبت نشده است.</div>`;
  const editAction = selfPortal
    ? ""
    : `<button class="text-button" onclick="document.getElementById('userProfileDialog').close();editAdminUser(${Number(profile.user_id)})">ویرایش پروفایل</button>`;
  return `<article class="user-profile-card user-profile-redesigned"><header class="user-profile-hero">${adminUserAvatar(profile, "profile")}
    <div class="user-profile-heading"><p class="profile-overline">${selfPortal ? "پرتال کاربری شما" : "پروفایل کاربر سامانه"}</p><h2>${esc(profile.full_name)}</h2><p>@${esc(profile.username)} · ${esc(profile.role_title)}</p><span class="status-pill ${Number(profile.active) ? "approved" : "rejected"}">${Number(profile.active) ? "حساب فعال" : "حساب غیرفعال"}</span></div>
    <div class="profile-account-facts"><span>ساخته‌شده: ${fdate(profile.created_at)}</span><span>آخرین ورود: ${fdate(profile.last_login_at)}</span></div>
  </header><section class="user-profile-metrics profile-metrics-grid"><div><small>کل پیام‌های ارسالی</small><b>${n(metric.total_messages)}</b><span>در کل سابقه</span></div><div><small>پیام در ${n(profile.window_days)} روز اخیر</small><b>${n(metric.window_messages)}</b><span>بازهٔ فعال بررسی</span></div><div><small>میانگین امتیاز</small><b>${average}</b><span>${n(metric.rated_messages || 0)} پیام امتیازدهی‌شده</span></div></section>
  <section class="profile-sender-card"><div><small>هویت ارسال‌کنندهٔ متصل</small><b>${esc(sender)}</b><span>${profile.sender_key ? "آمار پیام‌ها از این هویت محاسبه می‌شود." : "برای مشاهدهٔ آمار واقعی، هویت ارسال‌کننده را در ویرایش پروفایل انتخاب کنید."}</span></div>${editAction}</section>
  <section class="user-profile-activity"><div class="profile-section-title"><div><h4>روند ارسال در روزهای فعال</h4><p>تعداد پیام‌های دریافت‌شده برای این پروفایل</p></div><span>${n(dailyRows.length)} روز فعال</span></div><div class="profile-day-chart">${dailyVisual}</div></section></article>`;
}

async function loadUserPortal() {
  const profile = await api("/admin/api/me/profile");
  $("userPortalProfile").innerHTML = userProfileMarkup(profile, {selfPortal: true});
  $("portalFullName").value = profile.full_name || "";
  $("portalPassword").value = "";
}

let qsAnalysisInFlight = false;

function emptyQuickStartState() {
  return {
    step: 1, dateFrom: "", dateTo: "", timeFrom: "00:00", timeTo: "23:59",
    analyzing: false, analysisDone: false, highAttentionDay: "", outputDay: "",
  };
}

function isQuickStartWorkspace() {
  return state.page === "quick-start";
}

function keepQuickStartLocation() {
  if (!isQuickStartWorkspace()) return false;
  history.replaceState(null, "", "#quick-start");
  return true;
}

function restoreEmbeddedDeskCopy() {
  const eyebrow = $("finalizationDeskView")?.querySelector(".eyebrow");
  if (eyebrow?.dataset.original) eyebrow.textContent = eyebrow.dataset.original;
  const draftsEyebrow = $("finalizationDrafts")?.querySelector(".eyebrow");
  if (draftsEyebrow?.dataset.original) draftsEyebrow.textContent = draftsEyebrow.dataset.original;
}

function mountQuickStartDesk() {
  const host = $("qsDeskHost");
  const desk = $("finalizationDeskView");
  const drafts = $("finalizationDrafts");
  if (!host || !desk || !drafts) return;
  if (!state.qsDeskHomes) {
    state.qsDeskHomes = {
      deskParent: desk.parentNode,
      deskNext: desk.nextSibling,
      draftsParent: drafts.parentNode,
      draftsNext: drafts.nextSibling,
    };
  }
  if (desk.parentNode !== host) host.append(desk, drafts);
  desk.classList.remove("hidden");
  drafts.classList.remove("hidden");
  $("finalizationRangeView")?.classList.add("hidden");
  const deskEyebrow = desk.querySelector(".eyebrow");
  if (deskEyebrow) {
    deskEyebrow.dataset.original = deskEyebrow.dataset.original || deskEyebrow.textContent;
    deskEyebrow.textContent = "شروع سریع · میز تدوین";
  }
  const draftsEyebrow = drafts.querySelector(".eyebrow");
  if (draftsEyebrow) {
    draftsEyebrow.dataset.original = draftsEyebrow.dataset.original || draftsEyebrow.textContent;
    draftsEyebrow.textContent = "شروع سریع · پیش‌نویس و نهایی‌سازی";
  }
}

function unmountQuickStartDesk() {
  const homes = state.qsDeskHomes;
  const desk = $("finalizationDeskView");
  const drafts = $("finalizationDrafts");
  if (!homes || !desk || desk.parentNode === homes.deskParent) {
    restoreEmbeddedDeskCopy();
    return;
  }
  if (homes.deskNext && homes.deskNext.parentNode === homes.deskParent) {
    homes.deskParent.insertBefore(desk, homes.deskNext);
  } else {
    homes.deskParent.appendChild(desk);
  }
  if (homes.draftsNext && homes.draftsNext.parentNode === homes.draftsParent) {
    homes.draftsParent.insertBefore(drafts, homes.draftsNext);
  } else {
    homes.draftsParent.appendChild(drafts);
  }
  restoreEmbeddedDeskCopy();
  if (state.page === "finalization") renderFinalizationView();
}

function persistQuickStart() {
  try { localStorage.setItem("garaye:quick-start", JSON.stringify(state.quickStart)); } catch (_) {}
}

function restoreQuickStart() {
  try {
    const saved = JSON.parse(localStorage.getItem("garaye:quick-start") || "null");
    if (saved && typeof saved === "object") {
      state.quickStart = {...emptyQuickStartState(), ...saved, analyzing: qsAnalysisInFlight};
    }
  } catch (_) {}
}

function setQuickStartAnalysisLocked(locked) {
  $("qsBackToWindow")?.toggleAttribute("disabled", Boolean(locked));
  $("resetQuickStart")?.toggleAttribute("disabled", Boolean(locked));
  document.querySelectorAll(".qs-step-tab").forEach((tab) => {
    tab.disabled = Boolean(locked);
    tab.classList.toggle("is-locked", Boolean(locked));
  });
}

function canEnterQuickStartStep(step) {
  if (qsAnalysisInFlight && step !== 2) {
    toast("تا پایان تحلیل خودکار نمی‌توانید این مرحله را ترک کنید.");
    return false;
  }
  if (step > 1 && !state.quickStart.dateFrom) {
    toast("ابتدا روز تحلیل را تأیید کنید.", true);
    return false;
  }
  if (step >= 3 && !state.quickStart.analysisDone) {
    toast("تحلیل اخبار این بازه هنوز کامل نشده است.", true);
    return false;
  }
  return true;
}

function qsWindowParams() {
  const dateFrom = state.quickStart.dateFrom;
  const dateTo = state.quickStart.dateTo || dateFrom;
  const timeFrom = clock24(state.quickStart.timeFrom || "00:00") || "00:00";
  const timeTo = clock24(state.quickStart.timeTo || "23:59") || "23:59";
  return {
    dateFrom,
    dateTo,
    timeFrom,
    timeTo,
    flowDate: jalaliValueToFlowDate(dateFrom),
  };
}

function qsWindowQuery() {
  const {dateFrom, dateTo, timeFrom, timeTo} = qsWindowParams();
  const params = new URLSearchParams();
  if (dateFrom) params.set("date_from_jalali", dateFrom);
  if (dateTo) params.set("date_to_jalali", dateTo);
  if (timeFrom) params.set("time_from", timeFrom);
  if (timeTo) params.set("time_to", timeTo);
  params.set("timezone", "Asia/Tehran");
  return params;
}

function syncQuickStartWindowToSystem() {
  const {dateFrom, dateTo, timeFrom, timeTo} = {
    dateFrom: state.quickStart.dateFrom,
    dateTo: state.quickStart.dateTo || state.quickStart.dateFrom,
    timeFrom: state.quickStart.timeFrom || "00:00",
    timeTo: state.quickStart.timeTo || "23:59",
  };
  if (!dateFrom) return;
  ["streamFrom", "finalizationDateFrom", "garayeFrom", "qsDate"].forEach((id) => { if ($(id)) $(id).value = dateFrom; });
  ["streamTo", "finalizationDateTo", "garayeTo", "qsDateTo"].forEach((id) => { if ($(id)) $(id).value = dateTo; });
  if ($("finalizationTimeFrom")) $("finalizationTimeFrom").value = timeFrom;
  if ($("finalizationTimeTo")) $("finalizationTimeTo").value = timeTo;
  if ($("streamTimeFrom")) $("streamTimeFrom").value = timeFrom;
  if ($("streamTimeTo")) $("streamTimeTo").value = timeTo;
  if ($("garayeFromTime")) $("garayeFromTime").value = timeFrom;
  if ($("garayeToTime")) $("garayeToTime").value = timeTo;
  if ($("garayeRangePreset")) $("garayeRangePreset").value = "custom";
  if ($("bulletinTitle") && $("qsBulletinTitle")?.value.trim()) $("bulletinTitle").value = $("qsBulletinTitle").value.trim();
  if ($("bulletinMode") && $("qsBulletinMode")) $("bulletinMode").value = $("qsBulletinMode").value;
  if ($("bulletinIntroduction") && $("qsBulletinIntroduction")) $("bulletinIntroduction").value = $("qsBulletinIntroduction").value;
}

function setQuickStartStep(step) {
  state.quickStart.step = Number(step) || 1;
  persistQuickStart();
  document.querySelectorAll(".qs-step-tab").forEach((tab) => {
    const value = Number(tab.dataset.qsStep);
    tab.classList.toggle("active", value === state.quickStart.step);
    tab.classList.toggle("done", value < state.quickStart.step);
  });
  for (let index = 1; index <= 5; index += 1) {
    $(`qsStep${index}`)?.classList.toggle("hidden", index !== state.quickStart.step);
  }
  if (state.quickStart.step !== 3) unmountQuickStartDesk();
}

async function loadQuickStart() {
  restoreQuickStart();
  if (state.quickStart.dateFrom && $("qsDate")) $("qsDate").value = state.quickStart.dateFrom;
  if (state.quickStart.dateTo && $("qsDateTo")) $("qsDateTo").value = state.quickStart.dateTo;
  if ($("qsTimeFrom")) $("qsTimeFrom").value = state.quickStart.timeFrom || "00:00";
  if ($("qsTimeTo")) $("qsTimeTo").value = state.quickStart.timeTo || "23:59";
  setQuickStartStep(state.quickStart.step);
  setQuickStartAnalysisLocked(qsAnalysisInFlight);
  if (state.quickStart.step === 2) {
    const progress = await refreshQuickStartAnalysis();
    if (!progress) {
      setQuickStartStep(1);
    } else if (Number(progress.remaining || 0)) {
      await runQuickStartAnalysis({autoAdvance: true});
    } else if (!qsAnalysisInFlight) {
      state.quickStart.analysisDone = true;
      persistQuickStart();
      setQuickStartStep(3);
    }
  }
  if (state.quickStart.step === 3) await refreshQuickStartFinalization();
  else unmountQuickStartDesk();
  if (state.quickStart.step >= 4) await refreshQuickStartHighAttention({fillDays: true});
  if (state.quickStart.step >= 5) await refreshQuickStartOutput({fillDays: true});
}

async function confirmQuickStartWindow() {
  const dateFrom = $("qsDate").value.trim();
  if (!parseJalaliInput(dateFrom)) return toast("روز تحلیل را از تقویم انتخاب کنید یا به‌صورت ۱۴۰۵/۰۵/۲۶ وارد کنید.", true);
  state.quickStart.dateFrom = dateFrom;
  state.quickStart.dateTo = $("qsDateTo").value.trim() || dateFrom;
  state.quickStart.timeFrom = clock24($("qsTimeFrom").value || "00:00") || "00:00";
  state.quickStart.timeTo = clock24($("qsTimeTo").value || "23:59") || "23:59";
  state.quickStart.analysisDone = false;
  syncQuickStartWindowToSystem();
  persistQuickStart();
  setQuickStartStep(2);
  await runQuickStartAnalysis({autoAdvance: true});
}

function renderQuickStartProgress(progress, {running = false} = {}) {
  const total = Number(progress?.total || 0);
  const analyzed = Number(progress?.analyzed || 0);
  const remaining = Number(progress?.remaining || 0);
  const percent = total ? Math.min(100, Math.round(analyzed / total * 100)) : (running ? 0 : 100);
  const status = running
    ? `${n(remaining)} خبر باقی مانده؛ تحلیل خودکار با موتور اول در حال اجرا است.`
    : remaining
      ? `${n(remaining)} خبر تا پایان تحلیل باقی مانده است.`
      : total
        ? "همه اخبار این بازه تحلیل شده‌اند."
        : "در این بازه خبری برای تحلیل نبود.";
  $("qsAnalysisProgress").innerHTML = `
    <span class="eyebrow">پیشرفت تحلیل</span>
    <b>${n(analyzed)} از ${n(total)} خبر تحلیل شده است</b>
    <small>${status}</small>
    <div class="qs-progress-track" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${percent}"><i style="width:${percent}%"></i></div>`;
}

async function refreshQuickStartAnalysis() {
  const {dateFrom} = qsWindowParams();
  if (!dateFrom) {
    $("qsAnalysisProgress").innerHTML = `<small>ابتدا روز تحلیل را تأیید کنید.</small>`;
    return null;
  }
  const progress = await api(`/admin/api/messages/progress?${qsWindowQuery()}`);
  if (!Number(progress.remaining || 0)) {
    state.quickStart.analysisDone = true;
    persistQuickStart();
  }
  renderQuickStartProgress(progress, {running: qsAnalysisInFlight});
  return progress;
}

async function qsUnanalyzedMessageIds(progress) {
  const fromProgress = (Array.isArray(progress?.remaining_ids) ? progress.remaining_ids : [])
    .map(Number)
    .filter((id) => id > 0);
  if (fromProgress.length) return fromProgress;
  const unanalyzedQuery = qsWindowQuery();
  unanalyzedQuery.set("status", "unanalyzed");
  try {
    const listed = await api(`/admin/api/messages/ids?${unanalyzedQuery}`);
    const ids = (listed.ids || []).map(Number).filter((id) => id > 0);
    if (ids.length) return ids;
  } catch (_) {}
  if (!Number(progress?.remaining || 0)) return [];
  const fallback = await api(`/admin/api/messages/ids?${qsWindowQuery()}`);
  return (fallback.ids || []).map(Number).filter((id) => id > 0);
}

async function runQuickStartAnalysis({autoAdvance = true} = {}) {
  if (qsAnalysisInFlight) return;
  if (!qsWindowParams().dateFrom) {
    toast("بازه زمانی را کامل کنید.", true);
    return;
  }
  qsAnalysisInFlight = true;
  state.quickStart.analyzing = true;
  persistQuickStart();
  setQuickStartAnalysisLocked(true);
  try {
    let lastRemaining = Infinity;
    let stallCount = 0;
    while (true) {
      const progress = await api(`/admin/api/messages/progress?${qsWindowQuery()}`);
      renderQuickStartProgress(progress, {running: true});
      const remaining = Number(progress.remaining || 0);
      const ids = await qsUnanalyzedMessageIds(progress);
      if (!remaining && !ids.length) {
        state.quickStart.analysisDone = true;
        persistQuickStart();
        renderQuickStartProgress(progress, {running: false});
        if (autoAdvance && state.quickStart.step === 2) {
          toast(progress.total ? "تحلیل اخبار این بازه انجام شد." : "در این بازه خبری برای تحلیل نبود.");
          setQuickStartStep(3);
          await refreshQuickStartFinalization();
        }
        return;
      }
      if (!ids.length) {
        toast("در این بازه خبری برای تحلیل یافت نشد.", true);
        return;
      }
      if (remaining && remaining >= lastRemaining) stallCount += 1;
      else stallCount = 0;
      lastRemaining = remaining || ids.length;
      if (stallCount >= 2) {
        toast("تحلیل متوقف شد؛ این مرحله را دوباره انتخاب کنید تا ادامه یابد.", true);
        return;
      }
      for (let index = 0; index < ids.length; index += 200) {
        const batch = ids.slice(index, index + 200);
        await api("/admin/api/messages/analyze", {method: "POST", body: JSON.stringify({message_ids: batch})});
        renderQuickStartProgress({
          total: progress.total || ids.length,
          analyzed: Number(progress.analyzed || 0) + index + batch.length,
          remaining: Math.max(0, (remaining || ids.length) - index - batch.length),
        }, {running: true});
      }
    }
  } catch (error) {
    toast(error.message, true);
    await refreshQuickStartAnalysis();
  } finally {
    qsAnalysisInFlight = false;
    state.quickStart.analyzing = false;
    persistQuickStart();
    setQuickStartAnalysisLocked(false);
  }
}

async function refreshQuickStartFinalization() {
  const {dateFrom} = qsWindowParams();
  if (!dateFrom) return;
  syncQuickStartWindowToSystem();
  mountQuickStartDesk();
  if (!state.people.length || !state.topics.length) await loadReferenceData();
  await loadAnalysisFilters();
  await loadDrafts();
}

async function fillQsFinalizationDaySelect(selectId, selectedDay) {
  const select = $(selectId);
  if (!select) return [];
  const days = await api("/admin/api/high-attention/days");
  if (selectedDay && !days.some((item) => item.day === selectedDay)) {
    days.unshift({day: selectedDay, draft_count: 0});
  }
  select.innerHTML = `<option value="">انتخاب روز نهایی‌سازی</option>` + days.map((item) =>
    `<option value="${esc(item.day)}">${esc(highAttentionDayLabel(item.day))} · ${n(item.draft_count)} خبر نهایی</option>`
  ).join("");
  select.value = selectedDay || "";
  return days;
}

function updateQuickStartHighAttentionSelection() {
  const boxes = [...document.querySelectorAll(".qs-ha-check")];
  const count = boxes.filter((input) => input.checked).length;
  const selectAll = $("qsSelectAllHighAttention");
  if (selectAll) {
    selectAll.checked = Boolean(boxes.length) && count === boxes.length;
    selectAll.indeterminate = count > 0 && count < boxes.length;
  }
  const button = $("qsGenerateHighAttention");
  if (!button) return;
  button.disabled = !count;
  button.textContent = count
    ? `تولید پربازتاب از ${n(count)} خبر انتخاب‌شده`
    : "تولید پربازتاب";
}

async function refreshQuickStartHighAttention({fillDays = false} = {}) {
  const selectedDay = state.quickStart.highAttentionDay || "";
  if (fillDays) {
    const days = await fillQsFinalizationDaySelect("qsHighAttentionDay", selectedDay);
    $("qsHighAttentionDayHint").textContent = days.length
      ? `${n(days.length)} روز دارای خبر نهایی برای بررسی پربازتاب در دسترس است.`
      : "ابتدا خبر را نهایی کنید تا روز آن اینجا بیاید.";
  }
  const toolbar = $("qsHighAttentionToolbar");
  const selectAll = $("qsSelectAllHighAttention");
  state.currentHighAttention = null;
  renderQuickStartHighAttentionEditor(null);
  if (!selectedDay) {
    toolbar?.classList.add("hidden");
    if (selectAll) {
      selectAll.checked = false;
      selectAll.indeterminate = false;
    }
    $("qsHighAttentionHint").textContent = "خبرهای نهایی همین روز";
    $("qsHighAttentionDrafts").innerHTML = `<div class="empty-mini">ابتدا روز نهایی‌سازی را انتخاب کنید.</div>`;
    updateQuickStartHighAttentionSelection();
    return;
  }
  const drafts = await api(`/admin/api/high-attention/drafts?source_day=${encodeURIComponent(selectedDay)}`);
  state.highAttentionDrafts = drafts;
  toolbar?.classList.toggle("hidden", !drafts.length);
  if (selectAll && !drafts.length) {
    selectAll.checked = false;
    selectAll.indeterminate = false;
  }
  $("qsHighAttentionHint").textContent = drafts.length
    ? `${n(drafts.length)} خبر نهایی در این روز`
    : "خبر نهایی‌شده‌ای برای این روز نیست";
  $("qsHighAttentionDrafts").innerHTML = drafts.length ? drafts.map((item) => `
    <label class="sortable-item"><span class="drag-handle">◉</span><div><b>${esc(item.title || item.person_name || "خبر")}</b><p>${esc(item.summary_sentence || item.topic_name || "")}</p></div><input class="qs-ha-check" type="checkbox" value="${item.draft_id}"></label>`).join("") : `<div class="empty-mini">برای این روز خبر نهایی‌شده‌ای وجود ندارد.</div>`;
  updateQuickStartHighAttentionSelection();
}

function renderQuickStartHighAttentionEditor(run) {
  const editor = $("qsHighAttentionEditor");
  if (!editor) return;
  if (!run) { editor.classList.add("hidden"); return; }
  editor.classList.remove("hidden");
  const locked = run.status === "finalized";
  $("qsHighAttentionItems").innerHTML = (run.items || []).map((item) => `
    <article class="high-attention-item-editor">
      <h4>محور ${n(item.sort_order + 1)}</h4>
      <label>تیتر<input data-qs-ha-title="${item.high_attention_item_id}" value="${esc(item.title || "")}" ${locked ? "disabled" : ""}></label>
      <label>توضیح<textarea rows="4" data-qs-ha-summary="${item.high_attention_item_id}" ${locked ? "disabled" : ""}>${esc(item.summary || "")}</textarea></label>
    </article>`).join("");
}

async function generateQuickStartHighAttention() {
  const sourceDay = state.quickStart.highAttentionDay || $("qsHighAttentionDay")?.value;
  const ids = [...document.querySelectorAll(".qs-ha-check:checked")].map((item) => Number(item.value)).filter(Boolean);
  if (!sourceDay) return toast("روز نهایی‌سازی را انتخاب کنید.", true);
  if (!ids.length) return toast("حداقل یک خبر نهایی را انتخاب کنید.", true);
  const button = $("qsGenerateHighAttention");
  if (button) {
    button.disabled = true;
    button.textContent = "در حال تولید…";
  }
  try {
    const run = await api("/admin/api/high-attention/generate", {method: "POST", body: JSON.stringify({source_day: sourceDay, draft_ids: ids})});
    state.currentHighAttention = run;
    renderQuickStartHighAttentionEditor(run);
    toast((run.items || []).length ? "پربازتاب تولید شد؛ تیترها را بررسی و نهایی کنید." : "برای خبرهای انتخاب‌شده محور پربازتابی پیدا نشد.");
  } catch (error) {
    toast(error.message, true);
  } finally {
    updateQuickStartHighAttentionSelection();
  }
}

async function saveQuickStartHighAttention() {
  const run = state.currentHighAttention;
  if (!run || run.status !== "draft") return null;
  const items = (run.items || []).map((item) => ({
    high_attention_item_id: item.high_attention_item_id,
    title: document.querySelector(`[data-qs-ha-title="${item.high_attention_item_id}"]`)?.value.trim() || "",
    summary: document.querySelector(`[data-qs-ha-summary="${item.high_attention_item_id}"]`)?.value.trim() || "",
  }));
  const saved = await api(`/admin/api/high-attention/${run.high_attention_run_id}/items`, {method: "PUT", body: JSON.stringify({items})});
  state.currentHighAttention = saved;
  renderQuickStartHighAttentionEditor(saved);
  toast("تغییرات پربازتاب ذخیره شد.");
  return saved;
}

async function finalizeQuickStartHighAttention() {
  try {
    const saved = await saveQuickStartHighAttention();
    if (!saved) return;
    await api(`/admin/api/high-attention/${saved.high_attention_run_id}/finalize`, {method: "POST"});
    toast("پربازتاب نهایی شد.");
    await refreshQuickStartHighAttention();
  } catch (error) { toast(error.message, true); }
}

function updateQuickStartOutputActions() {
  const day = state.quickStart.outputDay;
  const count = document.querySelectorAll(".qs-bulletin-check:checked").length;
  const selectAll = $("qsSelectAllBulletin");
  const boxes = [...document.querySelectorAll(".qs-bulletin-check")];
  if (selectAll) {
    selectAll.checked = Boolean(boxes.length) && boxes.every((input) => input.checked);
    selectAll.indeterminate = boxes.some((input) => input.checked) && !selectAll.checked;
  }
  const button = $("qsCreateBulletin");
  if (button) button.disabled = !day || !count;
}

async function refreshQuickStartOutput({fillDays = false} = {}) {
  const selectedDay = state.quickStart.outputDay || "";
  if (fillDays) {
    const days = await fillQsFinalizationDaySelect("qsOutputDay", selectedDay);
    $("qsOutputDayHint").textContent = days.length
      ? "خروجی فقط از خبرهایی ساخته می‌شود که خودتان تیک زده‌اید."
      : "ابتدا خبر را نهایی کنید تا روز آن اینجا بیاید.";
  }
  const toolbar = $("qsOutputToolbar");
  if (!selectedDay) {
    toolbar?.classList.add("hidden");
    $("qsOutputHint").textContent = "خبرهای نهایی همین روز";
    $("qsBulletinDrafts").innerHTML = `<div class="empty-mini">ابتدا روز نهایی‌سازی را انتخاب کنید.</div>`;
    $("qsBulletinHighAttention").innerHTML = `<div class="empty-mini">ابتدا روز نهایی‌سازی را انتخاب کنید.</div>`;
    updateQuickStartOutputActions();
    const runs = await api("/admin/api/bulletins");
    $("qsBulletinRuns").innerHTML = runs.length ? `<table><thead><tr><th>شناسه</th><th>وضعیت</th><th>خروجی</th></tr></thead><tbody>${runs.slice(0, 8).map((run) => `<tr><td>${n(run.id)}</td><td>${statusLabel(run.status)}</td><td>${renderBulletinRunActions(run)}</td></tr>`).join("")}</tbody></table>` : "";
    return;
  }
  const [drafts, runs, highAttention] = await Promise.all([
    api("/admin/api/editorial-drafts?status=finalized"),
    api("/admin/api/bulletins"),
    api(`/admin/api/high-attention/finalized-items?source_day=${encodeURIComponent(selectedDay)}`),
  ]);
  const dayDrafts = drafts.filter((item) => String(item.flow_date || "") === selectedDay);
  toolbar?.classList.toggle("hidden", !dayDrafts.length);
  $("qsOutputHint").textContent = dayDrafts.length
    ? `${n(dayDrafts.length)} خبر نهایی برای انتخاب`
    : "خبر نهایی این روز وجود ندارد";
  $("qsBulletinDrafts").innerHTML = dayDrafts.length ? dayDrafts.map((item) => `
    <label class="sortable-item"><span class="drag-handle">◉</span><div><b>${esc(item.title || item.person_name || "خبر")}</b><p>${esc(item.summary_sentence || "")}</p></div><input class="qs-bulletin-check" type="checkbox" value="${item.draft_id}"></label>`).join("") : `<div class="empty-mini">خبر نهایی این روز وجود ندارد.</div>`;
  $("qsBulletinHighAttention").innerHTML = (highAttention || []).length ? highAttention.map((item) => `
    <label class="sortable-item"><span class="drag-handle">◉</span><div><b>${esc(item.title)}</b><p>${esc(item.summary)}</p></div><input class="qs-bulletin-ha-check" type="checkbox" value="${item.high_attention_item_id}"></label>`).join("") : `<div class="empty-mini">پربازتاب نهایی این روز وجود ندارد.</div>`;
  $("qsBulletinRuns").innerHTML = runs.length ? `<table><thead><tr><th>شناسه</th><th>وضعیت</th><th>خروجی</th></tr></thead><tbody>${runs.slice(0, 8).map((run) => `<tr><td>${n(run.id)}</td><td>${statusLabel(run.status)}</td><td>${renderBulletinRunActions(run)}</td></tr>`).join("")}</tbody></table>` : "";
  updateQuickStartOutputActions();
}

async function createQuickStartBulletin() {
  const draftIds = [...document.querySelectorAll(".qs-bulletin-check:checked")].map((item) => Number(item.value));
  const highAttentionItemIds = [...document.querySelectorAll(".qs-bulletin-ha-check:checked")].map((item) => Number(item.value));
  if (!state.quickStart.outputDay) return toast("روز نهایی‌سازی را انتخاب کنید.", true);
  if (!draftIds.length) return toast("حداقل یک خبر نهایی را انتخاب کنید.", true);
  try {
    const result = await api("/admin/api/editorial-bulletins", {
      method: "POST",
      body: JSON.stringify({
        draft_ids: draftIds,
        high_attention_item_ids: highAttentionItemIds,
        title: $("qsBulletinTitle").value.trim() || "خبرنامه گرایه",
        report_mode: $("qsBulletinMode").value,
        introduction: $("qsBulletinIntroduction").value.trim() || null,
      }),
    });
    toast("ساخت خبرنامه آغاز شد؛ خروجی‌ها در همین صفحه ظاهر می‌شوند.");
    if (result?.run_id) startBulletinOutputPolling(result.run_id);
    await refreshQuickStartOutput();
  } catch (error) { toast(error.message, true); }
}

document.querySelectorAll(".nav-item").forEach((item) => item.addEventListener("click", () => {
  showPage(item.dataset.page, item.dataset.section || "");
  closeNavigationMenus();
}));
document.querySelectorAll("[data-go]").forEach((item) => item.addEventListener("click", () => showPage(item.dataset.go)));
$("mobileMenu").addEventListener("click", toggleMobileNav);
$("closeNav")?.addEventListener("click", closeMobileNav);
$("navBackdrop")?.addEventListener("click", closeMobileNav);
$("backToPeople").addEventListener("click", () => showPage("people"));
$("editProfilePerson").addEventListener("click", () => {
  if (!state.personProfileId) return;
  showPage("people");
  editPerson(state.personProfileId);
});
$("logoutButton").addEventListener("click", async () => {
  try { await api("/auth/logout", {method: "POST"}); } catch (_) {}
  location.replace("/login");
});
$("refreshPage").addEventListener("click", () => loadPage(state.page).then(() => toast("اطلاعات تازه شد.")).catch((error) => toast(error.message, true)));
$("calendarPrevious").addEventListener("click", () => moveCalendarMonth(-1));
$("calendarNext").addEventListener("click", () => moveCalendarMonth(1));
$("applyStreamFilters").addEventListener("click", () => {
  state.selectedMessages.clear();
  loadStream().catch((error) => toast(error.message, true));
});
$("streamQuery").addEventListener("keydown", (event) => { if (event.key === "Enter") loadStream(); });
$("analyzeSelected").addEventListener("click", analyzeSelectedMessages);
$("selectVisibleMessages").addEventListener("click", selectVisibleMessages);
$("clearMessageSelection").addEventListener("click", clearMessageSelection);
$("monitoringDays").addEventListener("change", () => { state.monitoringFocusDay = null; loadMonitoring().catch((error) => toast(error.message, true)); });
$("monitoringResetChart").addEventListener("click", () => { state.monitoringFocusDay = null; if (state.monitoring) renderMonitoringChart(state.monitoring); });
$("applyGarayeFilters").addEventListener("click", () => loadGarayeInsights().catch((error) => toast(error.message, true)));
$("garayeRangePreset").addEventListener("change", () => setGarayeRangePreset($("garayeRangePreset").value, {load: true}));
["garayeFrom", "garayeFromTime", "garayeTo", "garayeToTime"].forEach((id) => $(id).addEventListener("input", () => { $("garayeRangePreset").value = "custom"; }));
$("garayeWordTrendSelect").addEventListener("change", () => { state.garayeWordTrendWord = $("garayeWordTrendSelect").value; renderGarayeWordTrend(); });
$("openEitanGara")?.addEventListener("click", () => showPage("eitan-gara"));
$("backToGarayeFromEitan")?.addEventListener("click", () => showPage("garaye"));
$("cancelEitanCreate")?.addEventListener("click", () => toggleEitanCreate(false));
$("eitanCreatePanel")?.addEventListener("submit", (event) => submitEitanCreate(event).catch((error) => toast(error.message, true)));
$("loadMoreEitan")?.addEventListener("click", () => loadEitanMessages({append: true}).catch((error) => toast(error.message, true)));
$("startEditorialAutomation").addEventListener("click", () => {
  try {
    return editorialAutomationAction(
      "/admin/api/editorial-automation/analysis/start",
      "تحلیل خودکار از زمان انتخابی شروع شد و هر ۱۰ دقیقه ادامه می‌یابد.",
      automationStartPayload("analysisAutomationStartDate", "analysisAutomationStartTime"),
    );
  } catch (error) { toast(error.message, true); return null; }
});
$("stopEditorialAutomation").addEventListener("click", () => editorialAutomationAction("/admin/api/editorial-automation/analysis/stop", "چرخهٔ خودکار متوقف شد."));
$("runEditorialAnalysisNow").addEventListener("click", () => editorialAutomationAction("/admin/api/editorial-automation/analysis/run-now", "اجرای فوری تحلیل برای همان بازه انجام شد."));
$("startEditorialDraftAutomation").addEventListener("click", () => {
  try {
    return editorialAutomationAction(
      "/admin/api/editorial-automation/drafts/start",
      "تولید پیش‌نویس خودکار از زمان انتخابی شروع شد و هر ۳ ساعت ادامه می‌یابد.",
      automationStartPayload("draftAutomationStartDate", "draftAutomationStartTime"),
    );
  } catch (error) { toast(error.message, true); return null; }
});
$("stopEditorialDraftAutomation").addEventListener("click", () => editorialAutomationAction("/admin/api/editorial-automation/drafts/stop", "تولید پیش‌نویس خودکار متوقف شد."));
$("runEditorialDraftsNow").addEventListener("click", () => editorialAutomationAction("/admin/api/editorial-automation/drafts/run-now", "ساخت فوری پیش‌نویس برای همان بازه انجام شد."));
$("refreshHumanControl").addEventListener("click", () => loadHumanControl().catch((error) => toast(error.message, true)));
$("refreshAnalysisFilters").addEventListener("click", () => loadAnalysisFilters().catch((error) => toast(error.message, true)));
$("applyFinalizationWindow").addEventListener("click", () => loadAnalysisFilters().catch((error) => toast(error.message, true)));
$("changeFinalizationRange").addEventListener("click", () => {
  if (isQuickStartWorkspace()) return;
  state.finalizationStage = "range";
  renderFinalizationView();
  $("finalizationDateFrom").focus();
});
$("finalizationSpeaker").addEventListener("change", () => {
  if ($("finalizationSpeaker").value) $("finalizationEvent").value = "";
  resetDeskSubjectEditorTouch();
  loadAnalyzedMessages().catch((error) => toast(error.message, true));
});
$("finalizationEvent").addEventListener("change", () => {
  if ($("finalizationEvent").value) $("finalizationSpeaker").value = "";
  resetDeskSubjectEditorTouch();
  loadAnalyzedMessages().catch((error) => toast(error.message, true));
});
$("finalizationGeneralTopic").addEventListener("change", () => loadAnalyzedMessages().catch((error) => toast(error.message, true)));
$("finalizationSpecificTopic").addEventListener("change", () => loadAnalyzedMessages().catch((error) => toast(error.message, true)));
["deskPersonName", "deskPersonPosition", "deskPersonCategory", "deskEventTitle", "deskEventLocation", "deskEventTime", "deskNewPersonName"].forEach((id) => {
  $(id)?.addEventListener("input", (event) => {
    event.currentTarget.dataset.touched = "1";
    if (id === "deskNewPersonName") $("deskPersonName").value = event.currentTarget.value.trim();
    markDeskSubjectUnconfirmed();
  });
  $(id)?.addEventListener("change", () => markDeskSubjectUnconfirmed());
});
$("deskPersonSearch")?.addEventListener("focus", () => renderDeskPersonOptions($("deskPersonSearch").value));
$("deskPersonSearch")?.addEventListener("input", (event) => {
  event.currentTarget.dataset.touched = "1";
  renderDeskPersonOptions(event.currentTarget.value);
});
$("confirmDeskSubject")?.addEventListener("click", () => confirmDeskSubject().catch((error) => toast(error.message, true)));
document.addEventListener("click", (event) => {
  if (!$("deskPersonCombobox")?.contains(event.target)) closeDeskPersonOptions();
});
  ["finalizationDateFrom", "finalizationTimeFrom", "finalizationDateTo", "finalizationTimeTo"].forEach((id) => {
    $(id).addEventListener("change", invalidateFinalizationWindow);
  });
  $("createDraftFromAnalysis").addEventListener("click", () => createDraftFromAnalysis());
document.querySelectorAll("[data-create-manual-draft]").forEach((button) => {
  button.addEventListener("click", () => createManualDraft().catch((error) => toast(error.message, true)));
});
$("selectAllFinalizedDrafts")?.addEventListener("change", (event) => {
  toggleAllFinalizedDrafts(event.currentTarget.checked);
});
$("saveDraft").addEventListener("click", () => saveDraft());
$("finalizeDraft").addEventListener("click", finalizeDraft);
$("deleteDraft").addEventListener("click", deleteCurrentDraft);
$("generateBase").addEventListener("click", () => generateDraft("base"));
  $("generateAllSummaries").addEventListener("click", () => generateDraft("summaries"));
  $("createShortLink").addEventListener("click", createShortLink);
  ["draftPerson", "draftCategory", "draftTopic", "summaryParagraph", "summarySentence", "summaryTitle", "deskFootnote"].forEach((id) => {
    $(id)?.addEventListener("input", refreshCurrentCandidatePreview);
    $(id)?.addEventListener("change", refreshCurrentCandidatePreview);
  });
  $("cancelQuickRegistryPerson").addEventListener("click", hideQuickRegistryPrompt);
  $("quickRegistryPersonForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const messageId = Number($("quickRegistrySourceMessageId").value);
    const tagId = Number($("quickRegistryPersonForm").dataset.tagId);
    if (!messageId || !tagId) return toast("برچسب گوینده برای افزودن به شناسنامه پیدا نشد.", true);
    const button = $("addDetectedPersonToRegistry");
    const originalText = button.textContent;
    button.disabled = true;
    button.textContent = "در حال افزودن…";
    try {
      const result = await api(`/admin/api/analysis/messages/${messageId}/speakers/${tagId}/promote-person`, {
        method: "POST",
        body: JSON.stringify({category: $("quickRegistryPersonCategory").value.trim() || null}),
      });
      await loadReferenceData();
      await loadAnalysisFilters();
      toast(result.created ? "شخص به شناسنامه افزوده و تگ‌های هم‌نام متصل شد." : "شناسنامه موجود به تگ‌های تحلیل‌شده متصل شد.");
    } catch (error) {
      toast(error.message, true);
    } finally {
      button.disabled = false;
      button.textContent = originalText;
    }
  });
$("createManualBulletin").addEventListener("click", createManualBulletin);
$("highAttentionDay").addEventListener("change", () => {
  state.highAttentionDay = $("highAttentionDay").value;
  syncJalaliFlowInput("highAttentionJalaliDate", state.highAttentionDay);
  loadHighAttentionDay().catch((error) => toast(error.message, true));
});
$("highAttentionJalaliDate")?.addEventListener("change", () => {
  const flowDate = applyJalaliFlowDay($("highAttentionJalaliDate").value, {
    selectId: "highAttentionDay",
    stateKey: "highAttentionDay",
  });
  if (flowDate) loadHighAttentionDay().catch((error) => toast(error.message, true));
});
$("selectAllHighAttentionDrafts")?.addEventListener("change", (event) => {
  toggleAllHighAttentionDrafts(event.currentTarget.checked);
});
$("generateHighAttention").addEventListener("click", generateHighAttention);
$("saveHighAttention").addEventListener("click", () => {
  saveHighAttention().catch((error) => toast(error.message, true));
});
$("finalizeHighAttention").addEventListener("click", finalizeHighAttention);
$("bulletinFinalizationDay").addEventListener("change", () => {
  state.bulletinFinalizationDay = $("bulletinFinalizationDay").value;
  syncJalaliFlowInput("bulletinJalaliDate", state.bulletinFinalizationDay);
  renderFinalizedDraftsForDay();
  loadBulletinHighAttentionItems().catch((error) => toast(error.message, true));
});
$("bulletinJalaliDate")?.addEventListener("change", () => {
  const raw = $("bulletinJalaliDate").value.trim();
  if (!raw) {
    state.bulletinFinalizationDay = "";
    $("bulletinFinalizationDay").value = "";
    renderFinalizedDraftsForDay();
    loadBulletinHighAttentionItems().catch((error) => toast(error.message, true));
    return;
  }
  const flowDate = applyJalaliFlowDay(raw, {
    selectId: "bulletinFinalizationDay",
    stateKey: "bulletinFinalizationDay",
  });
  if (!flowDate) return;
  renderFinalizedDraftsForDay();
  loadBulletinHighAttentionItems().catch((error) => toast(error.message, true));
});
$("bulletinTitle").addEventListener("input", updateBulletinPreview);
$("showPersonForm").addEventListener("click", () => {
  fillPersonCategorySelects("");
  $("personForm").classList.remove("hidden");
});
$("showPeopleSettings")?.addEventListener("click", () => {
  const panel = $("peopleSettingsPanel");
  const nowOpen = panel.classList.toggle("hidden");
  const open = !nowOpen;
  $("showPeopleSettings").setAttribute("aria-expanded", String(open));
  if (open) fillPersonCategorySelects();
});
$("cancelPerson").addEventListener("click", clearPersonForm);
$("renamePersonCategory")?.addEventListener("click", async () => {
  const oldCategory = $("categoryRenameOld")?.value?.trim();
  const newCategory = $("categoryRenameNew")?.value?.trim();
  if (!oldCategory || !newCategory) return toast("دسته قدیم و جدید را مشخص کنید.", true);
  if (!confirm(`دسته «${oldCategory}» برای همه افراد وابسته به «${newCategory}» تغییر کند؟`)) return;
  try {
    const result = await api("/admin/api/people/categories/rename", {
      method: "POST",
      body: JSON.stringify({ old_category: oldCategory, new_category: newCategory }),
    });
    await loadPeople();
    $("categoryRenameNew").value = "";
    toast(`دسته به‌روزرسانی شد (${n(result.updated_people || 0)} نفر).`);
  } catch (error) {
    toast(error.message, true);
  }
});
$("showUserForm").addEventListener("click", () => { clearAdminUserForm(); $("userForm").classList.remove("hidden"); });
$("cancelUser").addEventListener("click", clearAdminUserForm);
$("adminRole").addEventListener("change", renderRolePermissions);
$("adminSenderKey").addEventListener("change", applySenderProfileChoice);
$("peopleSearch").addEventListener("input", renderPeople);
$("createPersonCategory").addEventListener("click", () => {
  createPersonCategory({value: $("categoryCreateNew").value}).catch((error) => toast(error.message, true));
});
$("personCategory").addEventListener("change", () => {
  if ($("personCategory").value === "__new_person_category__") {
    createPersonCategory({prompt: true}).catch((error) => toast(error.message, true));
  }
});
$("personForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const body = {
    person_id: Number($("personId").value) || null, full_name: $("personName").value.trim(),
    position: $("personPosition").value.trim() || null, category: $("personCategory").value.trim() || null,
    registry_status: $("personRegistry").value, aliases: $("personAliases").value.split("|").map((x) => x.trim()).filter(Boolean),
    username: $("personUsername").value.trim() || null, active: true, replace_aliases: true,
  };
  try {
    const saved = await api("/admin/api/people", {method: "POST", body: JSON.stringify(body)});
    const portrait = $("personPortrait").files[0];
    if (portrait) {
      const data = new FormData();
      data.append("file", portrait);
      await api(`/admin/api/people/${saved.person_id}/portrait`, {method: "POST", body: data});
    }
    clearPersonForm(); await loadPeople(); toast("شناسنامه ذخیره شد.");
  } catch (error) { toast(error.message, true); }
});
$("peopleImportFile").addEventListener("change", async () => {
  const file = $("peopleImportFile").files[0];
  if (!file) return;
  const data = new FormData();
  data.append("file", file);
  try {
    const result = await api("/admin/api/people/import", {method: "POST", body: data});
    const errors = result.errors || [];
    toast(errors.length ? `${n(result.imported)} نفر وارد شد؛ ${n(errors.length)} ردیف خطا دارد.` : `${n(result.imported)} شناسنامه از فایل وارد شد.`, Boolean(errors.length));
    $("peopleImportFile").value = "";
    await loadPeople();
  } catch (error) { toast(error.message, true); }
});
$("sourceForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const body = {
    chat_id: Number($("sourceChatId").value) || null, username: $("sourceUsername").value.trim() || null,
    title: $("sourceTitle").value.trim() || null, chat_type: $("sourceType").value,
  };
  if (!body.chat_id && !body.username) return toast("شناسه عددی یا نام کاربری منبع لازم است.", true);
  try {
    await api("/admin/api/sources", {method: "POST", body: JSON.stringify(body)});
    $("sourceForm").reset(); await loadSources(); toast("منبع پایش ثبت شد.");
  } catch (error) { toast(error.message, true); }
});
$("crawlerChannelsForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const channels = $("crawlerChannels").value
    .split(/\r?\n|,/)
    .map((item) => item.trim().replace(/^@/, ""))
    .filter(Boolean);
  try {
    await api("/admin/api/crawler/channels", {
      method: "PUT",
      body: JSON.stringify({channels}),
    });
    await loadSources(true);
    toast("فهرست کانال‌های کرولر ذخیره شد.");
  } catch (error) { toast(error.message, true); }
});
$("crawlerEnabledToggle").addEventListener("click", toggleCrawlerRuntime);
$("startCrawler").addEventListener("click", startCrawlerFromDashboard);
$("stopCrawler").addEventListener("click", stopCrawlerFromDashboard);
$("startCrawlerRecovery")?.addEventListener("click", async () => {
  const button = $("startCrawlerRecovery");
  button.disabled = true;
  try {
    const result = await api("/admin/api/crawler/recovery", {
      method: "POST", body: JSON.stringify({hours: 48}),
    });
    renderCrawlerRecoveryStatus(result);
    startBotQueueRecoveryPolling();
    toast("بازیابی صف ربات آغاز شد؛ نتیجه در همین بخش به‌روزرسانی می‌شود.");
  } catch (error) {
    toast(error.message, true);
    button.disabled = false;
  }
});
$("userForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const body = {
    user_id: Number($("adminUserId").value) || null,
    username: $("adminUsername").value.trim(),
    full_name: $("adminFullName").value.trim(),
    role: $("adminRole").value,
    active: $("adminActive").checked,
    password: $("adminPassword").value || null,
    sender_key: $("adminSenderKey").value || null,
  };
  if (!body.user_id && !body.password) return toast("برای کاربر جدید رمز عبور لازم است.", true);
  try {
    const saved = await api("/admin/api/users", {method: "POST", body: JSON.stringify(body)});
    const portrait = $("adminUserPortrait").files[0];
    if (portrait) {
      const formData = new FormData();
      formData.append("file", portrait);
      await api(`/admin/api/users/${Number(saved.user_id)}/portrait`, {method: "POST", body: formData});
    }
    clearAdminUserForm(); await loadAdminUsers(); toast("حساب کاربری ذخیره شد.");
  } catch (error) { toast(error.message, true); }
});
$("backupNow").addEventListener("click", async () => {
  try { await api("/admin/api/backup", {method: "POST"}); toast("نسخه پشتیبان با موفقیت ساخته شد."); await loadSystem(); }
  catch (error) { toast(error.message, true); }
});
$("apiKeyForm")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const name = $("apiKeyName")?.value.trim();
  if (!name) return toast("نام کلید را وارد کنید.", true);
  try {
    const created = await api("/admin/api/api-keys", {method: "POST", body: JSON.stringify({name})});
    $("apiKeyName").value = "";
    showCreatedApiKey(created);
    await loadApiKeys();
    toast("کلید ساخته شد؛ مقدار کامل را همین حالا کپی کنید.");
  } catch (error) { toast(error.message, true); }
});
document.querySelectorAll("[data-draft-filter]").forEach((item) => item.addEventListener("click", () => {
  document.querySelectorAll("[data-draft-filter]").forEach((button) => button.classList.remove("active"));
  item.classList.add("active"); state.draftFilter = item.dataset.draftFilter; loadDrafts();
}));
document.querySelectorAll("[data-draft-origin]").forEach((item) => item.addEventListener("click", () => {
  document.querySelectorAll("[data-draft-origin]").forEach((button) => button.classList.remove("active"));
  item.classList.add("active"); state.draftOriginFilter = item.dataset.draftOrigin; loadDrafts();
}));
document.querySelectorAll("[data-close-dialog]").forEach((item) => item.addEventListener("click", () => item.closest("dialog").close()));

window.selectMessage = selectMessage; window.selectVisibleMessages = selectVisibleMessages; window.clearMessageSelection = clearMessageSelection; window.openMessage = openMessage;
window.selectAnalyzedMessage = selectAnalyzedMessage; window.searchMessageInGoogle = searchMessageInGoogle;
window.discardMessageFromDesk = discardMessageFromDesk;
window.correctAnalyzedSpeaker = correctAnalyzedSpeaker;
window.viewPerson = viewPerson;
window.openDraft = openDraft; window.restoreDraftVersion = restoreDraftVersion; window.toggleSource = toggleSource;
window.saveSourceTitle = saveSourceTitle;
window.revokeApiKey = revokeApiKey;
$("loadMoreStream")?.addEventListener("click", () => loadStream({append: true}).catch((error) => toast(error.message, true)));
$("currentUser")?.addEventListener("click", () => showPage("portal"));
document.querySelectorAll(".quick-start-nav").forEach((item) => item.addEventListener("click", () => showPage(item.dataset.page)));
document.querySelectorAll(".qs-step-tab").forEach((tab) => tab.addEventListener("click", () => {
  const step = Number(tab.dataset.qsStep);
  if (!canEnterQuickStartStep(step)) return;
  setQuickStartStep(step);
  if (step === 2) {
    refreshQuickStartAnalysis().then((progress) => {
      if (!state.quickStart.analysisDone || Number(progress?.remaining || 0)) {
        return runQuickStartAnalysis({autoAdvance: !state.quickStart.analysisDone});
      }
      return null;
    }).catch((error) => toast(error.message, true));
    return;
  }
  if (step === 3) refreshQuickStartFinalization().catch((error) => toast(error.message, true));
  if (step === 4) refreshQuickStartHighAttention({fillDays: true}).catch((error) => toast(error.message, true));
  if (step === 5) refreshQuickStartOutput({fillDays: true}).catch((error) => toast(error.message, true));
}));
$("qsConfirmWindow")?.addEventListener("click", () => confirmQuickStartWindow().catch((error) => toast(error.message, true)));
$("qsBackToWindow")?.addEventListener("click", () => {
  if (!canEnterQuickStartStep(1)) return;
  setQuickStartStep(1);
});
$("qsBackToAnalysis")?.addEventListener("click", () => {
  if (!canEnterQuickStartStep(2)) return;
  setQuickStartStep(2);
  refreshQuickStartAnalysis().then((progress) => {
    if (Number(progress?.remaining || 0)) {
      return runQuickStartAnalysis({autoAdvance: true});
    }
    return null;
  }).catch((error) => toast(error.message, true));
});
$("qsGoHighAttention")?.addEventListener("click", () => {
  if (!canEnterQuickStartStep(4)) return;
  setQuickStartStep(4);
  refreshQuickStartHighAttention({fillDays: true}).catch((error) => toast(error.message, true));
});
$("qsHighAttentionDay")?.addEventListener("change", () => {
  state.quickStart.highAttentionDay = $("qsHighAttentionDay").value;
  persistQuickStart();
  refreshQuickStartHighAttention().catch((error) => toast(error.message, true));
});
$("qsHighAttentionDrafts")?.addEventListener("change", (event) => {
  if (event.target?.classList?.contains("qs-ha-check")) updateQuickStartHighAttentionSelection();
});
$("qsSelectAllHighAttention")?.addEventListener("change", (event) => {
  document.querySelectorAll(".qs-ha-check").forEach((input) => { input.checked = event.currentTarget.checked; });
  updateQuickStartHighAttentionSelection();
});
$("qsGenerateHighAttention")?.addEventListener("click", () => generateQuickStartHighAttention());
$("qsSaveHighAttention")?.addEventListener("click", () => saveQuickStartHighAttention().catch((error) => toast(error.message, true)));
$("qsFinalizeHighAttention")?.addEventListener("click", () => finalizeQuickStartHighAttention());
$("qsBackToFinalization")?.addEventListener("click", () => setQuickStartStep(3));
$("qsGoOutput")?.addEventListener("click", () => {
  if (!canEnterQuickStartStep(5)) return;
  setQuickStartStep(5);
  refreshQuickStartOutput({fillDays: true}).catch((error) => toast(error.message, true));
});
$("qsBackToHighAttention")?.addEventListener("click", () => {
  setQuickStartStep(4);
  refreshQuickStartHighAttention({fillDays: true}).catch((error) => toast(error.message, true));
});
$("qsOutputDay")?.addEventListener("change", () => {
  state.quickStart.outputDay = $("qsOutputDay").value;
  persistQuickStart();
  refreshQuickStartOutput().catch((error) => toast(error.message, true));
});
$("qsSelectAllBulletin")?.addEventListener("change", (event) => {
  document.querySelectorAll(".qs-bulletin-check").forEach((input) => { input.checked = event.currentTarget.checked; });
  updateQuickStartOutputActions();
});
$("qsBulletinDrafts")?.addEventListener("change", (event) => {
  if (event.target?.classList?.contains("qs-bulletin-check")) updateQuickStartOutputActions();
});
$("qsCreateBulletin")?.addEventListener("click", () => createQuickStartBulletin());
$("qsOpenBulletins")?.addEventListener("click", () => {
  const outputDay = state.quickStart.outputDay;
  if (outputDay) {
    state.bulletinFinalizationDay = outputDay;
    ensureSelectOption($("bulletinFinalizationDay"), outputDay, highAttentionDayLabel(outputDay));
    syncJalaliFlowInput("bulletinJalaliDate", outputDay);
  }
  showPage("bulletins");
});
$("resetQuickStart")?.addEventListener("click", () => {
  if (qsAnalysisInFlight) return toast("تا پایان تحلیل خودکار نمی‌توانید شروع سریع را از ابتدا بازنشانی کنید.");
  state.quickStart = emptyQuickStartState();
  persistQuickStart();
  if ($("qsDate")) $("qsDate").value = "";
  if ($("qsDateTo")) $("qsDateTo").value = "";
  if ($("qsHighAttentionDay")) $("qsHighAttentionDay").value = "";
  if ($("qsOutputDay")) $("qsOutputDay").value = "";
  renderQuickStartHighAttentionEditor(null);
  setQuickStartStep(1);
});
$("portalProfileForm")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await api("/admin/api/me", {method: "POST", body: JSON.stringify({
      full_name: $("portalFullName").value.trim(),
      password: $("portalPassword").value || null,
    })});
    const portrait = $("portalPortrait").files[0];
    if (portrait) {
      const data = new FormData();
      data.append("file", portrait);
      await api("/admin/api/me/portrait", {method: "POST", body: data});
    }
    await loadCurrentUser();
    await loadUserPortal();
    toast("پرتال کاربری ذخیره شد.");
  } catch (error) { toast(error.message, true); }
});
$("liveUpdateForm")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const file = $("liveUpdateFile").files[0];
  if (!file) return toast("فایل ZIP نسخه جدید را انتخاب کنید.", true);
  if (!confirm("بسته روی فایل‌های برنامه اعمال شود؟ داده و تنظیمات .env حفظ می‌شوند و سامانه در صورت اجرای نظارت‌شده خودش راه‌اندازی مجدد می‌گردد.")) return;
  const button = $("applyLiveUpdate");
  const oldText = button.textContent;
  button.disabled = true;
  button.textContent = "در حال اعمال…";
  try {
    const data = new FormData();
    data.append("file", file);
    const result = await api("/admin/api/system/update", {method: "POST", body: data});
    renderLiveUpdateStatus(result, result.message || "بسته اعمال شد.");
    toast(result.message || "به‌روزرسانی اعمال شد.");
    if (result.restarting) {
      const deadline = Date.now() + (result.requirements_changed ? 120000 : 90000);
      const poll = async () => {
        try {
          const health = await fetch("/health", {cache: "no-store"});
          if (health.ok) {
            location.reload();
            return;
          }
        } catch (_) {}
        if (Date.now() < deadline) setTimeout(poll, 1200);
        else location.reload();
      };
      setTimeout(poll, result.requirements_changed ? 8000 : 3500);
    } else {
      setTimeout(() => location.reload(), 800);
    }
  } catch (error) { toast(error.message, true); }
  finally { button.disabled = false; button.textContent = oldText; }
});
$("jalaliPickerPrev")?.addEventListener("click", () => {
  state.jalaliPicker.month -= 1;
  if (state.jalaliPicker.month < 1) { state.jalaliPicker.month = 12; state.jalaliPicker.year -= 1; }
  renderJalaliPickerDays();
});
$("jalaliPickerNext")?.addEventListener("click", () => {
  state.jalaliPicker.month += 1;
  if (state.jalaliPicker.month > 12) { state.jalaliPicker.month = 1; state.jalaliPicker.year += 1; }
  renderJalaliPickerDays();
});
$("jalaliPickerToday")?.addEventListener("click", () => {
  const today = jalaliToday();
  if (state.jalaliPicker.input) {
    state.jalaliPicker.input.value = jalaliInputValue(today);
    state.jalaliPicker.input.dispatchEvent(new Event("change", {bubbles: true}));
  }
  closeJalaliPicker();
});
$("jalaliPickerClose")?.addEventListener("click", closeJalaliPicker);
$("jalaliPickerDays")?.addEventListener("click", (event) => {
  const day = Number(event.target?.dataset?.pickerDay);
  if (!day || !state.jalaliPicker.input) return;
  state.jalaliPicker.input.value = jalaliInputValue({year: state.jalaliPicker.year, month: state.jalaliPicker.month, day});
  state.jalaliPicker.input.dispatchEvent(new Event("change", {bubbles: true}));
  closeJalaliPicker();
});
document.addEventListener("click", (event) => {
  const picker = $("jalaliPicker");
  if (!picker || picker.hidden) return;
  if (picker.contains(event.target) || event.target?.hasAttribute?.("data-jalali-date") || event.target?.closest?.(".jalali-date-open")) return;
  closeJalaliPicker();
});

window.updateBulletinPreview = updateBulletinPreview; window.updateHighAttentionSelection = updateHighAttentionSelection;
window.openHighAttentionRun = openHighAttentionRun; window.editPerson = editPerson;
window.editAdminUser = editAdminUser;
window.showMonitoringDayShare = showMonitoringDayShare;
window.viewAdminUserProfile = viewAdminUserProfile;
window.regenerateBulletinOutputs = regenerateBulletinOutputs;
window.deleteBulletinRun = deleteBulletinRun;

(async function boot() {
  try {
    await loadCurrentUser();
    await loadSenderProfiles();
    await loadSources(true);
    setGarayeRangePreset($("garayeRangePreset").value);
    bindJalaliDatePickers();
    bindTime24Pickers();
    restoreQuickStart();
    const [hashPage, hashSection] = location.hash.replace("#", "").split(":", 2);
    showPage(pageMeta[hashPage] ? hashPage : "overview", hashSection || "");
    $("connectionLabel").textContent = "سامانه در دسترس است";
  } catch (error) {
    $("connectionLabel").textContent = "خطا در اتصال";
    toast(error.message, true);
  }
})();
