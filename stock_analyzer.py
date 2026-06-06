# -*- coding: utf-8 -*-
"""
=============================================================
       محلل الأسهم الأمريكية - Stock Analyzer Pro
=============================================================
تطبيق سطح مكتب لتحليل الأسهم الأمريكية تحليلاً كاملاً:
  - تحليل القوائم المالية والفلترة الشرعية (متوافق مع الشريعة)
  - التحليل الفني (دعم/مقاومة، متوسطات، RSI، MACD، Bollinger، Fibonacci)
  - تحديد القمم والقيعان ونقاط الدخول والخروج
  - توصية ملخصة آلية

يعتمد على مكتبة yfinance لجلب البيانات (مجاناً وبدون مفتاح API).

المتطلبات:
  pip install yfinance pandas numpy matplotlib

التشغيل:
  python stock_analyzer.py
=============================================================
"""

import threading
import datetime as dt
import tkinter as tk
from tkinter import ttk, messagebox, font as tkfont, filedialog

import numpy as np
import pandas as pd

import matplotlib
matplotlib.use("TkAgg")
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

try:
    import yfinance as yf
except ImportError:
    yf = None

# للتصدير إلى PDF
from matplotlib.backends.backend_pdf import PdfPages
import os
import json

# إشعار ويندوز سطح المكتب (اختياري)
try:
    from plyer import notification as _plyer_notify
except Exception:
    _plyer_notify = None

# الصوت (ويندوز)
try:
    import winsound
except Exception:
    winsound = None

# ملف حفظ المفضلة والإعدادات
CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".stock_analyzer_config.json")


def load_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"favorites": [], "refresh_seconds": 30}


def save_config(cfg):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# =============================================================
#  ثوابت المعايير الشرعية (وفق المعايير الشرعية المعتمدة - AAOIFI)
# =============================================================
SHARIAH = {
    "max_debt_ratio": 0.30,        # الديون بفائدة / القيمة السوقية < 30%
    "max_interest_assets": 0.30,   # النقد والأصول المولّدة للفوائد < 30%
    "max_haram_income": 0.05,      # الدخل من أنشطة محرّمة < 5%
}

# قطاعات/أنشطة محظورة شرعاً (فحص أولي حسب القطاع والصناعة)
HARAM_KEYWORDS = [
    "bank", "insurance", "alcohol", "brewer", "distiller", "wine",
    "tobacco", "casino", "gambling", "gaming", "betting",
    "adult", "weapon", "defense", "financial - credit",
    "mortgage", "reit - hotel", "pork", "cannabis", "marijuana",
]


# =============================================================
#  المؤشرات الفنية
# =============================================================
def sma(series, window):
    return series.rolling(window=window).mean()


def ema(series, window):
    return series.ewm(span=window, adjust=False).mean()


def rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(window=period).mean()
    avg_loss = loss.rolling(window=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def macd(series, fast=12, slow=26, signal=9):
    macd_line = ema(series, fast) - ema(series, slow)
    signal_line = ema(macd_line, signal)
    hist = macd_line - signal_line
    return macd_line, signal_line, hist


def bollinger(series, window=20, num_std=2):
    mid = sma(series, window)
    std = series.rolling(window=window).std()
    upper = mid + num_std * std
    lower = mid - num_std * std
    return upper, mid, lower


def fibonacci_levels(high, low):
    diff = high - low
    return {
        "0.0% (قمة)": high,
        "23.6%": high - 0.236 * diff,
        "38.2%": high - 0.382 * diff,
        "50.0%": high - 0.500 * diff,
        "61.8%": high - 0.618 * diff,
        "78.6%": high - 0.786 * diff,
        "100% (قاع)": low,
    }


def find_peaks_troughs(prices, order=5):
    """تحديد القمم والقيعان المحلية باستخدام نافذة محلية."""
    peaks, troughs = [], []
    p = prices.values
    n = len(p)
    for i in range(order, n - order):
        window = p[i - order: i + order + 1]
        if p[i] == window.max() and p[i] > p[i - 1]:
            peaks.append((prices.index[i], p[i]))
        if p[i] == window.min() and p[i] < p[i - 1]:
            troughs.append((prices.index[i], p[i]))
    return peaks, troughs


def support_resistance(prices, troughs, peaks, current):
    """أقرب مستويات دعم ومقاومة من القيعان والقمم."""
    trough_vals = [v for _, v in troughs]
    peak_vals = [v for _, v in peaks]
    supports = sorted([v for v in trough_vals if v < current], reverse=True)
    resistances = sorted([v for v in peak_vals if v > current])
    return supports[:3], resistances[:3]


# =============================================================
#  جلب وتحليل البيانات
# =============================================================
def fetch_data(ticker):
    tk_obj = yf.Ticker(ticker)
    hist = tk_obj.history(period="2y")
    if hist is None or hist.empty:
        raise ValueError("لم يتم العثور على بيانات لهذا الرمز.")
    info = {}
    try:
        info = tk_obj.info or {}
    except Exception:
        info = {}
    # القوائم المالية
    try:
        bs = tk_obj.balance_sheet
    except Exception:
        bs = pd.DataFrame()
    try:
        fin = tk_obj.financials
    except Exception:
        fin = pd.DataFrame()
    return hist, info, bs, fin


def fetch_quick(ticker):
    """جلب سريع للسعر الحالي والرسم اليومي فقط (للتحديث التلقائي)."""
    tk_obj = yf.Ticker(ticker)
    intraday = tk_obj.history(period="1d", interval="5m")
    if intraday is None or intraday.empty:
        intraday = tk_obj.history(period="5d")
    price = float(intraday["Close"].iloc[-1])
    return price, intraday


def _get_row(df, names):
    """البحث عن صف في القائمة المالية بأسماء محتملة."""
    if df is None or df.empty:
        return None
    for name in names:
        for idx in df.index:
            if name.lower() in str(idx).lower():
                try:
                    val = df.loc[idx].iloc[0]
                    if pd.notna(val):
                        return float(val)
                except Exception:
                    continue
    return None


def shariah_screen(info, bs):
    """الفلترة الشرعية بناءً على القطاع والنسب المالية."""
    result = {"flags": [], "ratios": {}, "verdict": "غير محدد", "passed": True}

    sector = str(info.get("sector", "")).lower()
    industry = str(info.get("industry", "")).lower()
    name = str(info.get("longName", "")).lower()
    blob = f"{sector} {industry} {name}"

    # 1) فحص النشاط
    for kw in HARAM_KEYWORDS:
        if kw in blob:
            result["flags"].append(f"النشاط قد يتضمن مجالاً محظوراً: «{kw}»")
            result["passed"] = False

    market_cap = info.get("marketCap") or info.get("enterpriseValue")

    # 2) نسبة الديون
    total_debt = info.get("totalDebt")
    if total_debt is None:
        total_debt = _get_row(bs, ["Total Debt", "Long Term Debt", "Short Long Term Debt"])
    if total_debt is not None and market_cap:
        debt_ratio = total_debt / market_cap
        result["ratios"]["نسبة الديون بفائدة"] = debt_ratio
        if debt_ratio >= SHARIAH["max_debt_ratio"]:
            result["flags"].append(
                f"نسبة الديون {debt_ratio:.1%} تتجاوز الحد {SHARIAH['max_debt_ratio']:.0%}"
            )
            result["passed"] = False

    # 3) النقد والأصول المولّدة للفوائد
    cash = info.get("totalCash")
    if cash is None:
        cash = _get_row(bs, ["Cash And Cash Equivalents", "Cash", "Short Term Investments"])
    if cash is not None and market_cap:
        cash_ratio = cash / market_cap
        result["ratios"]["نسبة النقد والاستثمارات قصيرة الأجل"] = cash_ratio
        if cash_ratio >= SHARIAH["max_interest_assets"]:
            result["flags"].append(
                f"نسبة النقد/الأصول المولّدة للفوائد {cash_ratio:.1%} تتجاوز {SHARIAH['max_interest_assets']:.0%}"
            )
            result["passed"] = False

    # الخلاصة
    if not result["flags"]:
        result["verdict"] = "✅ متوافق مبدئياً مع الشريعة"
    elif result["passed"]:
        result["verdict"] = "⚠️ يحتاج مراجعة"
    else:
        result["verdict"] = "❌ غير متوافق (وفق الفحص الأولي)"
    return result


def technical_analysis(hist):
    close = hist["Close"]
    high = hist["High"]
    low = hist["Low"]
    current = float(close.iloc[-1])

    ind = {}
    ind["current"] = current
    ind["sma20"] = float(sma(close, 20).iloc[-1])
    ind["sma50"] = float(sma(close, 50).iloc[-1])
    ind["sma200"] = float(sma(close, 200).iloc[-1]) if len(close) >= 200 else None
    ind["rsi"] = float(rsi(close).iloc[-1])
    macd_line, signal_line, hist_macd = macd(close)
    ind["macd"] = float(macd_line.iloc[-1])
    ind["macd_signal"] = float(signal_line.iloc[-1])
    ind["macd_hist"] = float(hist_macd.iloc[-1])
    ub, mb, lb = bollinger(close)
    ind["bb_upper"] = float(ub.iloc[-1])
    ind["bb_lower"] = float(lb.iloc[-1])

    period_high = float(high.max())
    period_low = float(low.min())
    ind["fib"] = fibonacci_levels(period_high, period_low)

    peaks, troughs = find_peaks_troughs(close, order=5)
    supports, resistances = support_resistance(close, troughs, peaks, current)
    ind["supports"] = supports
    ind["resistances"] = resistances
    ind["peaks"] = peaks[-5:]
    ind["troughs"] = troughs[-5:]

    # نقاط الدخول والخروج المقترحة
    entry = supports[0] if supports else round(current * 0.97, 2)
    stop = round(entry * 0.95, 2)
    target = resistances[0] if resistances else round(current * 1.08, 2)
    ind["entry"] = round(entry, 2)
    ind["stop"] = round(stop, 2)
    ind["target"] = round(target, 2)

    return ind


def build_recommendation(ind, shariah):
    """توصية ملخصة آلية بنظام نقاط."""
    score = 0
    reasons = []
    c = ind["current"]

    if c > ind["sma50"]:
        score += 1; reasons.append("السعر فوق متوسط 50 يوم (اتجاه إيجابي)")
    else:
        score -= 1; reasons.append("السعر تحت متوسط 50 يوم (اتجاه سلبي)")

    if ind["sma200"]:
        if c > ind["sma200"]:
            score += 1; reasons.append("السعر فوق متوسط 200 يوم (اتجاه طويل صاعد)")
        else:
            score -= 1; reasons.append("السعر تحت متوسط 200 يوم (اتجاه طويل هابط)")

    r = ind["rsi"]
    if r < 30:
        score += 1; reasons.append(f"RSI={r:.0f} منطقة تشبّع بيعي (فرصة شراء)")
    elif r > 70:
        score -= 1; reasons.append(f"RSI={r:.0f} منطقة تشبّع شرائي (حذر)")
    else:
        reasons.append(f"RSI={r:.0f} منطقة محايدة")

    if ind["macd"] > ind["macd_signal"]:
        score += 1; reasons.append("MACD فوق خط الإشارة (زخم إيجابي)")
    else:
        score -= 1; reasons.append("MACD تحت خط الإشارة (زخم سلبي)")

    if score >= 2:
        verdict = "🟢 شراء / إيجابي"
    elif score <= -2:
        verdict = "🔴 بيع / سلبي"
    else:
        verdict = "🟡 حيادي / انتظار"

    if not shariah["passed"]:
        verdict += "  (تنبيه شرعي: غير متوافق وفق الفحص)"

    return verdict, score, reasons


# =============================================================
#  واجهة المستخدم الرسومية
# =============================================================
class StockApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("محلل الأسهم الأمريكية - Stock Analyzer Pro")
        self.geometry("1100x780")
        self.configure(bg="#0f1419")

        self.col_bg = "#0f1419"
        self.col_panel = "#1a2027"
        self.col_accent = "#2dd4bf"
        self.col_text = "#e6edf3"

        # إعدادات وحالة
        self.cfg = load_config()
        self.favorites = self.cfg.get("favorites", [])
        self.refresh_seconds = self.cfg.get("refresh_seconds", 30)
        self.auto_on = False
        self._refresh_job = None
        self.alert_levels = {}       # {نوع: سعر} نقاط التنبيه للسهم الحالي
        self.alert_fired = set()     # لتفادي تكرار نفس التنبيه

        self._build_styles()
        self._build_header()
        self._build_body()
        self.last = None
        self._refresh_fav_list()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        if yf is None:
            messagebox.showwarning(
                "مكتبة مفقودة",
                "مكتبة yfinance غير مثبتة.\nشغّل الأمر:\n\npip install yfinance pandas numpy matplotlib"
            )

    def _on_close(self):
        self.cfg["favorites"] = self.favorites
        self.cfg["refresh_seconds"] = self.refresh_seconds
        save_config(self.cfg)
        if self._refresh_job:
            try:
                self.after_cancel(self._refresh_job)
            except Exception:
                pass
        self.destroy()

    def _build_styles(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("TNotebook", background=self.col_bg, borderwidth=0)
        style.configure("TNotebook.Tab", background=self.col_panel,
                        foreground=self.col_text, padding=[16, 8], font=("Segoe UI", 10, "bold"))
        style.map("TNotebook.Tab", background=[("selected", self.col_accent)],
                  foreground=[("selected", "#0f1419")])

    def _build_header(self):
        head = tk.Frame(self, bg=self.col_bg)
        head.pack(fill="x", padx=20, pady=16)

        tk.Label(head, text="📊  محلل الأسهم الأمريكية",
                 bg=self.col_bg, fg=self.col_accent,
                 font=("Segoe UI", 20, "bold")).pack(side="left")

        entry_frame = tk.Frame(head, bg=self.col_bg)
        entry_frame.pack(side="right")

        tk.Label(entry_frame, text="رمز السهم:", bg=self.col_bg, fg=self.col_text,
                 font=("Segoe UI", 12)).pack(side="left", padx=6)

        self.ticker_var = tk.StringVar(value="AAPL")
        self.entry = tk.Entry(entry_frame, textvariable=self.ticker_var, width=12,
                              font=("Consolas", 14, "bold"), justify="center",
                              bg="#0b0f14", fg=self.col_accent, insertbackground=self.col_accent,
                              relief="flat")
        self.entry.pack(side="left", ipady=6, padx=6)
        self.entry.bind("<Return>", lambda e: self.analyze())

        self.btn = tk.Button(entry_frame, text="🔍 حلّل", command=self.analyze,
                             bg=self.col_accent, fg="#0f1419", font=("Segoe UI", 12, "bold"),
                             relief="flat", padx=20, pady=6, cursor="hand2")
        self.btn.pack(side="left", padx=6)

        self.pdf_btn = tk.Button(entry_frame, text="📄 PDF", command=self.export_pdf,
                                 bg="#30363d", fg=self.col_text, font=("Segoe UI", 11, "bold"),
                                 relief="flat", padx=14, pady=6, cursor="hand2", state="disabled")
        self.pdf_btn.pack(side="left", padx=4)

        self.cmp_btn = tk.Button(entry_frame, text="⚖️ قارن", command=self.open_compare,
                                 bg="#30363d", fg=self.col_text, font=("Segoe UI", 11, "bold"),
                                 relief="flat", padx=14, pady=6, cursor="hand2")
        self.cmp_btn.pack(side="left", padx=4)

        # صف ثانٍ: المفضلة والتحديث التلقائي
        bar = tk.Frame(self, bg=self.col_bg)
        bar.pack(fill="x", padx=20, pady=(0, 4))

        self.fav_btn = tk.Button(bar, text="⭐ أضف للمفضلة", command=self.toggle_favorite,
                                 bg="#30363d", fg="#d29922", font=("Segoe UI", 10, "bold"),
                                 relief="flat", padx=12, pady=4, cursor="hand2")
        self.fav_btn.pack(side="left", padx=(0, 6))

        tk.Label(bar, text="المفضلة:", bg=self.col_bg, fg=self.col_text,
                 font=("Segoe UI", 10)).pack(side="left")
        self.fav_var = tk.StringVar()
        self.fav_menu = ttk.Combobox(bar, textvariable=self.fav_var, width=10,
                                     state="readonly", font=("Consolas", 11))
        self.fav_menu.pack(side="left", padx=6)
        self.fav_menu.bind("<<ComboboxSelected>>", self._on_fav_select)

        self.del_fav_btn = tk.Button(bar, text="🗑", command=self.remove_favorite,
                                     bg="#30363d", fg="#f85149", font=("Segoe UI", 10, "bold"),
                                     relief="flat", padx=8, pady=4, cursor="hand2")
        self.del_fav_btn.pack(side="left")

        # التحديث التلقائي
        self.auto_btn = tk.Button(bar, text="▶ تحديث تلقائي", command=self.toggle_auto,
                                  bg="#30363d", fg="#3fb950", font=("Segoe UI", 10, "bold"),
                                  relief="flat", padx=12, pady=4, cursor="hand2")
        self.auto_btn.pack(side="right")
        tk.Label(bar, text="ثانية", bg=self.col_bg, fg=self.col_text,
                 font=("Segoe UI", 10)).pack(side="right", padx=4)
        self.sec_var = tk.StringVar(value=str(self.refresh_seconds))
        self.sec_entry = tk.Entry(bar, textvariable=self.sec_var, width=5, justify="center",
                                  font=("Consolas", 11), bg="#0b0f14", fg=self.col_accent,
                                  relief="flat", insertbackground=self.col_accent)
        self.sec_entry.pack(side="right", ipady=2, padx=4)
        tk.Label(bar, text="كل", bg=self.col_bg, fg=self.col_text,
                 font=("Segoe UI", 10)).pack(side="right", padx=2)

        self.status = tk.Label(self, text="أدخل رمز السهم (مثل AAPL, MSFT, TSLA) واضغط حلّل",
                               bg=self.col_bg, fg="#8b949e", font=("Segoe UI", 10))
        self.status.pack(fill="x", padx=20)

    def _build_body(self):
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=20, pady=12)

        # تبويب الملخص والتوصية
        self.tab_summary = tk.Frame(self.nb, bg=self.col_panel)
        self.nb.add(self.tab_summary, text="📋 الملخص والتوصية")
        self.summary_text = self._make_text(self.tab_summary)

        # تبويب التحليل الشرعي
        self.tab_shariah = tk.Frame(self.nb, bg=self.col_panel)
        self.nb.add(self.tab_shariah, text="🕌 التحليل الشرعي")
        self.shariah_text = self._make_text(self.tab_shariah)

        # تبويب التحليل الفني
        self.tab_tech = tk.Frame(self.nb, bg=self.col_panel)
        self.nb.add(self.tab_tech, text="📈 التحليل الفني")
        self.tech_text = self._make_text(self.tab_tech)

        # تبويب الرسم البياني
        self.tab_chart = tk.Frame(self.nb, bg=self.col_panel)
        self.nb.add(self.tab_chart, text="📉 الرسم البياني")
        self.chart_holder = tk.Frame(self.tab_chart, bg=self.col_panel)
        self.chart_holder.pack(fill="both", expand=True)

    def _make_text(self, parent):
        txt = tk.Text(parent, bg=self.col_panel, fg=self.col_text,
                      font=("Consolas", 12), relief="flat", wrap="word",
                      padx=20, pady=20, spacing1=4, spacing3=4)
        txt.pack(fill="both", expand=True)
        txt.tag_configure("title", font=("Segoe UI", 15, "bold"), foreground=self.col_accent)
        txt.tag_configure("good", foreground="#3fb950")
        txt.tag_configure("bad", foreground="#f85149")
        txt.tag_configure("warn", foreground="#d29922")
        txt.config(state="disabled")
        return txt

    def _set_text(self, widget, lines):
        widget.config(state="normal")
        widget.delete("1.0", "end")
        for text, tag in lines:
            widget.insert("end", text + "\n", tag)
        widget.config(state="disabled")

    # ---------------------------------------------------------
    def analyze(self):
        if yf is None:
            messagebox.showerror("خطأ", "مكتبة yfinance غير مثبتة.")
            return
        ticker = self.ticker_var.get().strip().upper()
        if not ticker:
            return
        self.btn.config(state="disabled", text="...")
        self.status.config(text=f"جاري جلب وتحليل بيانات {ticker} ...", fg=self.col_accent)
        threading.Thread(target=self._worker, args=(ticker,), daemon=True).start()

    def _worker(self, ticker):
        try:
            hist, info, bs, fin = fetch_data(ticker)
            shariah = shariah_screen(info, bs)
            ind = technical_analysis(hist)
            verdict, score, reasons = build_recommendation(ind, shariah)
            self.after(0, self._render, ticker, hist, info, shariah, ind, verdict, score, reasons)
        except Exception as e:
            self.after(0, self._error, str(e))

    def _error(self, msg):
        self.btn.config(state="normal", text="🔍 حلّل")
        self.status.config(text=f"خطأ: {msg}", fg="#f85149")
        messagebox.showerror("خطأ في التحليل", msg)

    def _render(self, ticker, hist, info, shariah, ind, verdict, score, reasons):
        self.btn.config(state="normal", text="🔍 حلّل")
        self.pdf_btn.config(state="normal")
        self.status.config(text=f"اكتمل تحليل {ticker} ✓", fg="#3fb950")

        # تخزين النتائج لاستخدامها في تصدير PDF
        self.last = dict(ticker=ticker, hist=hist, info=info, shariah=shariah,
                         ind=ind, verdict=verdict, score=score, reasons=reasons)

        # ضبط نقاط التنبيه الآلية (الدخول/الهدف/وقف الخسارة)
        self.alert_levels = {
            "نقطة الدخول": ind["entry"],
            "الهدف (خروج)": ind["target"],
            "وقف الخسارة": ind["stop"],
        }
        self.alert_fired = set()
        self._update_fav_btn(ticker)

        name = info.get("longName", ticker)
        price = ind["current"]
        cur = info.get("currency", "USD")

        # --- الملخص والتوصية ---
        L = []
        L.append((f"{name} ({ticker})", "title"))
        L.append((f"السعر الحالي: {price:.2f} {cur}", None))
        L.append(("", None))
        L.append(("◆ التوصية الآلية الملخصة:", "title"))
        tag = "good" if "🟢" in verdict else "bad" if "🔴" in verdict else "warn"
        L.append((f"   {verdict}   (نقاط التقييم: {score})", tag))
        L.append(("", None))
        L.append(("◆ الأسباب:", None))
        for r in reasons:
            L.append((f"   • {r}", None))
        L.append(("", None))
        L.append(("◆ خطة التداول المقترحة:", "title"))
        L.append((f"   نقطة الدخول المقترحة : {ind['entry']:.2f}", "good"))
        L.append((f"   وقف الخسارة          : {ind['stop']:.2f}", "bad"))
        L.append((f"   الهدف / الخروج       : {ind['target']:.2f}", "good"))
        rr = (ind['target'] - ind['entry']) / max(ind['entry'] - ind['stop'], 0.01)
        L.append((f"   نسبة العائد/المخاطرة : 1 : {rr:.2f}", None))
        L.append(("", None))
        L.append((f"◆ الحكم الشرعي المبدئي: {shariah['verdict']}",
                  "good" if shariah["passed"] and not shariah["flags"] else "bad"))
        L.append(("", None))
        L.append(("⚠️ تنبيه: هذا التحليل آلي لأغراض تعليمية وليس توصية استثمارية أو فتوى شرعية ملزمة.", "warn"))
        self._set_text(self.summary_text, L)

        # --- التحليل الشرعي ---
        S = []
        S.append((f"التحليل الشرعي - {ticker}", "title"))
        S.append((f"القطاع: {info.get('sector','غير معروف')}", None))
        S.append((f"الصناعة: {info.get('industry','غير معروف')}", None))
        S.append(("", None))
        S.append((f"الحكم المبدئي: {shariah['verdict']}",
                  "good" if shariah["passed"] and not shariah["flags"] else "bad"))
        S.append(("", None))
        S.append(("◆ النسب المالية المفحوصة:", "title"))
        if shariah["ratios"]:
            for k, v in shariah["ratios"].items():
                S.append((f"   {k}: {v:.1%}", None))
        else:
            S.append(("   لم تتوفر بيانات مالية كافية من المصدر.", "warn"))
        S.append(("", None))
        S.append(("◆ الملاحظات / التنبيهات:", "title"))
        if shariah["flags"]:
            for f in shariah["flags"]:
                S.append((f"   ✗ {f}", "bad"))
        else:
            S.append(("   ✓ لا توجد مخالفات في الفحص الأولي.", "good"))
        S.append(("", None))
        S.append(("◆ المعايير المعتمدة (وفق المعايير الشرعية AAOIFI):", None))
        S.append(("   - الديون بفائدة < 30% من القيمة السوقية", None))
        S.append(("   - النقد والأصول المولّدة للفوائد < 30%", None))
        S.append(("   - الدخل من أنشطة محرّمة < 5%", None))
        S.append(("", None))
        S.append(("⚠️ هذا فحص آلي مبدئي. للحكم النهائي راجع هيئة شرعية متخصصة أو مؤشرات مثل Zoya / Musaffa.", "warn"))
        self._set_text(self.shariah_text, S)

        # --- التحليل الفني ---
        T = []
        T.append((f"التحليل الفني - {ticker}", "title"))
        T.append(("", None))
        T.append(("◆ المتوسطات المتحركة:", "title"))
        T.append((f"   SMA 20  : {ind['sma20']:.2f}", None))
        T.append((f"   SMA 50  : {ind['sma50']:.2f}", None))
        if ind["sma200"]:
            T.append((f"   SMA 200 : {ind['sma200']:.2f}", None))
        T.append(("", None))
        T.append(("◆ مؤشرات الزخم:", "title"))
        T.append((f"   RSI (14)     : {ind['rsi']:.1f}", None))
        T.append((f"   MACD         : {ind['macd']:.3f}", None))
        T.append((f"   MACD Signal  : {ind['macd_signal']:.3f}", None))
        T.append(("", None))
        T.append(("◆ نطاقات بولينجر:", "title"))
        T.append((f"   الحد العلوي : {ind['bb_upper']:.2f}", None))
        T.append((f"   الحد السفلي : {ind['bb_lower']:.2f}", None))
        T.append(("", None))
        T.append(("◆ مستويات الدعم (القيعان):", "title"))
        if ind["supports"]:
            for s in ind["supports"]:
                T.append((f"   دعم  : {s:.2f}", "good"))
        else:
            T.append(("   لا يوجد دعم واضح تحت السعر الحالي.", None))
        T.append(("", None))
        T.append(("◆ مستويات المقاومة (القمم):", "title"))
        if ind["resistances"]:
            for r in ind["resistances"]:
                T.append((f"   مقاومة: {r:.2f}", "bad"))
        else:
            T.append(("   لا توجد مقاومة واضحة فوق السعر الحالي.", None))
        T.append(("", None))
        T.append(("◆ مستويات فيبوناتشي (سنتين):", "title"))
        for k, v in ind["fib"].items():
            T.append((f"   {k:<14}: {v:.2f}", None))
        self._set_text(self.tech_text, T)

        # --- الرسم البياني ---
        self._draw_chart(ticker, hist, ind)

    def _draw_chart(self, ticker, hist, ind):
        for w in self.chart_holder.winfo_children():
            w.destroy()

        close = hist["Close"]
        fig = Figure(figsize=(10, 6), dpi=100, facecolor="#1a2027")
        ax = fig.add_subplot(111, facecolor="#0f1419")

        ax.plot(close.index, close.values, color="#2dd4bf", linewidth=1.5, label="السعر")
        ax.plot(close.index, sma(close, 20), color="#d29922", linewidth=1, label="SMA20")
        ax.plot(close.index, sma(close, 50), color="#a371f7", linewidth=1, label="SMA50")

        peaks, troughs = find_peaks_troughs(close, order=5)
        if peaks:
            px, py = zip(*peaks)
            ax.scatter(px, py, color="#f85149", marker="v", s=40, label="قمم", zorder=5)
        if troughs:
            tx, ty = zip(*troughs)
            ax.scatter(tx, ty, color="#3fb950", marker="^", s=40, label="قيعان", zorder=5)

        ax.axhline(ind["entry"], color="#3fb950", ls="--", lw=0.8, alpha=0.7)
        ax.axhline(ind["target"], color="#2dd4bf", ls="--", lw=0.8, alpha=0.7)
        ax.axhline(ind["stop"], color="#f85149", ls="--", lw=0.8, alpha=0.7)

        ax.set_title(f"{ticker} - السعر مع القمم والقيعان", color="#e6edf3")
        ax.tick_params(colors="#8b949e")
        for spine in ax.spines.values():
            spine.set_color("#30363d")
        ax.legend(facecolor="#1a2027", edgecolor="#30363d", labelcolor="#e6edf3", fontsize=8)
        ax.grid(True, color="#21262d", linewidth=0.5)
        fig.tight_layout()

        canvas = FigureCanvasTkAgg(fig, master=self.chart_holder)
        canvas.draw()
        canvas.get_tk_widget().pack(fill="both", expand=True)


    # =========================================================
    #  تصدير التحليل إلى PDF
    # =========================================================
    def export_pdf(self):
        if not self.last:
            messagebox.showinfo("تنبيه", "حلّل سهماً أولاً قبل التصدير.")
            return
        d = self.last
        ticker = d["ticker"]
        path = filedialog.asksaveasfilename(
            defaultextension=".pdf",
            initialfile=f"تحليل_{ticker}.pdf",
            filetypes=[("PDF", "*.pdf")])
        if not path:
            return
        try:
            self._write_pdf(path, d)
            self.status.config(text=f"تم حفظ التقرير: {os.path.basename(path)} ✓", fg="#3fb950")
            messagebox.showinfo("تم", f"حُفظ التقرير بنجاح:\n{path}")
        except Exception as e:
            messagebox.showerror("خطأ", f"تعذّر إنشاء PDF:\n{e}")

    def _ar(self, text):
        """تهيئة النص العربي للعرض الصحيح في matplotlib (إن توفرت المكتبات)."""
        try:
            import arabic_reshaper
            from bidi.algorithm import get_display
            return get_display(arabic_reshaper.reshape(text))
        except Exception:
            return text

    def _write_pdf(self, path, d):
        ind, sh = d["ind"], d["shariah"]
        info = d["info"]
        name = info.get("longName", d["ticker"])
        cur = info.get("currency", "USD")

        with PdfPages(path) as pdf:
            # صفحة 1: الملخص النصي
            fig = Figure(figsize=(8.27, 11.69), dpi=120, facecolor="white")
            ax = fig.add_subplot(111); ax.axis("off")
            lines = []
            lines.append((self._ar(f"تقرير تحليل السهم: {name} ({d['ticker']})"), 16, "bold", "#0b6e6e"))
            lines.append((self._ar(f"السعر الحالي: {ind['current']:.2f} {cur}"), 11, "normal", "black"))
            lines.append(("", 6, "normal", "black"))
            tag_col = "#15803d" if "🟢" in d["verdict"] else "#b91c1c" if "🔴" in d["verdict"] else "#a16207"
            lines.append((self._ar(f"التوصية الآلية: {d['verdict']}  (نقاط: {d['score']})"), 13, "bold", tag_col))
            lines.append(("", 4, "normal", "black"))
            lines.append((self._ar("الأسباب:"), 12, "bold", "black"))
            for r in d["reasons"]:
                lines.append((self._ar(f"• {r}"), 10, "normal", "black"))
            lines.append(("", 4, "normal", "black"))
            lines.append((self._ar("خطة التداول المقترحة:"), 12, "bold", "black"))
            lines.append((self._ar(f"نقطة الدخول: {ind['entry']:.2f}   |   وقف الخسارة: {ind['stop']:.2f}   |   الهدف: {ind['target']:.2f}"), 10, "normal", "black"))
            lines.append(("", 4, "normal", "black"))
            lines.append((self._ar(f"الحكم الشرعي المبدئي: {sh['verdict']}"), 12, "bold",
                          "#15803d" if sh["passed"] and not sh["flags"] else "#b91c1c"))
            for k, v in sh["ratios"].items():
                lines.append((self._ar(f"   {k}: {v:.1%}"), 10, "normal", "black"))
            for f in sh["flags"]:
                lines.append((self._ar(f"   ✗ {f}"), 10, "normal", "#b91c1c"))
            lines.append(("", 4, "normal", "black"))
            lines.append((self._ar("المؤشرات الفنية:"), 12, "bold", "black"))
            lines.append((self._ar(f"SMA20={ind['sma20']:.2f}  SMA50={ind['sma50']:.2f}  RSI={ind['rsi']:.1f}  MACD={ind['macd']:.3f}"), 10, "normal", "black"))
            lines.append(("", 8, "normal", "black"))
            lines.append((self._ar("تنبيه: تقرير آلي لأغراض تعليمية، وليس توصية استثمارية أو فتوى شرعية ملزمة."), 9, "italic", "#a16207"))

            y = 0.96
            for text, size, weight, color in lines:
                style = "italic" if weight == "italic" else "normal"
                w = "bold" if weight == "bold" else "normal"
                ax.text(0.05, y, text, fontsize=size, fontweight=w, fontstyle=style,
                        color=color, va="top", ha="left", transform=ax.transAxes)
                y -= (size / 380.0) + 0.012
            pdf.savefig(fig, facecolor="white")

            # صفحة 2: الرسم البياني
            hist = d["hist"]; close = hist["Close"]
            fig2 = Figure(figsize=(11.69, 8.27), dpi=120, facecolor="white")
            ax2 = fig2.add_subplot(111)
            ax2.plot(close.index, close.values, color="#0d9488", lw=1.4, label=self._ar("السعر"))
            ax2.plot(close.index, sma(close, 20), color="#d97706", lw=1, label="SMA20")
            ax2.plot(close.index, sma(close, 50), color="#7c3aed", lw=1, label="SMA50")
            peaks, troughs = find_peaks_troughs(close, order=5)
            if peaks:
                px, py = zip(*peaks); ax2.scatter(px, py, color="#dc2626", marker="v", s=35, label=self._ar("قمم"))
            if troughs:
                tx, ty = zip(*troughs); ax2.scatter(tx, ty, color="#16a34a", marker="^", s=35, label=self._ar("قيعان"))
            ax2.axhline(ind["entry"], color="#16a34a", ls="--", lw=0.8)
            ax2.axhline(ind["target"], color="#0d9488", ls="--", lw=0.8)
            ax2.axhline(ind["stop"], color="#dc2626", ls="--", lw=0.8)
            ax2.set_title(self._ar(f"{d['ticker']} - السعر مع القمم والقيعان"))
            ax2.legend(fontsize=8); ax2.grid(True, alpha=0.3)
            fig2.tight_layout()
            pdf.savefig(fig2, facecolor="white")

    # =========================================================
    #  مقارنة عدة أسهم
    # =========================================================
    def open_compare(self):
        win = tk.Toplevel(self)
        win.title("مقارنة عدة أسهم")
        win.geometry("900x600")
        win.configure(bg=self.col_bg)

        top = tk.Frame(win, bg=self.col_bg); top.pack(fill="x", padx=16, pady=12)
        tk.Label(top, text="أدخل الرموز مفصولة بفواصل (مثل: AAPL, MSFT, NVDA):",
                 bg=self.col_bg, fg=self.col_text, font=("Segoe UI", 11)).pack(side="left")
        var = tk.StringVar(value="AAPL, MSFT, NVDA")
        ent = tk.Entry(top, textvariable=var, width=30, font=("Consolas", 12),
                       bg="#0b0f14", fg=self.col_accent, relief="flat", insertbackground=self.col_accent)
        ent.pack(side="left", ipady=4, padx=8)

        cols = ("الرمز", "السعر", "RSI", "MACD", "التوصية", "شرعي")
        tree = ttk.Treeview(win, columns=cols, show="headings", height=14)
        for c in cols:
            tree.heading(c, text=c); tree.column(c, anchor="center", width=130)
        tree.pack(fill="both", expand=True, padx=16, pady=8)

        st = tk.Label(win, text="", bg=self.col_bg, fg="#8b949e", font=("Segoe UI", 10))
        st.pack(fill="x", padx=16, pady=4)

        def run():
            tickers = [t.strip().upper() for t in var.get().split(",") if t.strip()]
            for r in tree.get_children():
                tree.delete(r)
            st.config(text="جاري المقارنة ...", fg=self.col_accent)
            threading.Thread(target=worker, args=(tickers,), daemon=True).start()

        def worker(tickers):
            rows = []
            for t in tickers:
                try:
                    hist, info, bs, fin = fetch_data(t)
                    sh = shariah_screen(info, bs)
                    ind = technical_analysis(hist)
                    v, s, _ = build_recommendation(ind, sh)
                    short = "🟢" if "🟢" in v else "🔴" if "🔴" in v else "🟡"
                    halal = "✅" if sh["passed"] and not sh["flags"] else "❌"
                    rows.append((t, f"{ind['current']:.2f}", f"{ind['rsi']:.0f}",
                                 f"{ind['macd']:.2f}", short, halal))
                except Exception:
                    rows.append((t, "خطأ", "-", "-", "-", "-"))
            win.after(0, fill, rows)

        def fill(rows):
            for row in rows:
                tree.insert("", "end", values=row)
            st.config(text="اكتملت المقارنة ✓", fg="#3fb950")

        tk.Button(top, text="قارن", command=run, bg=self.col_accent, fg="#0f1419",
                  font=("Segoe UI", 11, "bold"), relief="flat", padx=18, cursor="hand2").pack(side="left")
        run()


    # =========================================================
    #  المفضلة
    # =========================================================
    def _refresh_fav_list(self):
        self.fav_menu["values"] = self.favorites
        if self.favorites and not self.fav_var.get():
            pass

    def _update_fav_btn(self, ticker):
        if ticker in self.favorites:
            self.fav_btn.config(text="★ في المفضلة", fg="#3fb950")
        else:
            self.fav_btn.config(text="⭐ أضف للمفضلة", fg="#d29922")

    def toggle_favorite(self):
        ticker = self.ticker_var.get().strip().upper()
        if not ticker:
            return
        if ticker in self.favorites:
            self.favorites.remove(ticker)
        else:
            self.favorites.append(ticker)
        self.cfg["favorites"] = self.favorites
        save_config(self.cfg)
        self._refresh_fav_list()
        self._update_fav_btn(ticker)

    def remove_favorite(self):
        sel = self.fav_var.get().strip().upper()
        if sel and sel in self.favorites:
            self.favorites.remove(sel)
            self.cfg["favorites"] = self.favorites
            save_config(self.cfg)
            self.fav_var.set("")
            self._refresh_fav_list()
            self._update_fav_btn(self.ticker_var.get().strip().upper())

    def _on_fav_select(self, event=None):
        sel = self.fav_var.get().strip().upper()
        if sel:
            self.ticker_var.set(sel)
            self.analyze()

    # =========================================================
    #  التحديث التلقائي
    # =========================================================
    def toggle_auto(self):
        if self.auto_on:
            self.auto_on = False
            self.auto_btn.config(text="▶ تحديث تلقائي", fg="#3fb950")
            if self._refresh_job:
                self.after_cancel(self._refresh_job)
                self._refresh_job = None
            self.status.config(text="أُوقف التحديث التلقائي.", fg="#8b949e")
        else:
            try:
                secs = max(5, int(self.sec_var.get()))
            except ValueError:
                secs = 30
            self.refresh_seconds = secs
            self.sec_var.set(str(secs))
            self.cfg["refresh_seconds"] = secs
            save_config(self.cfg)
            if not self.ticker_var.get().strip():
                messagebox.showinfo("تنبيه", "أدخل رمز سهم أولاً.")
                return
            self.auto_on = True
            self.auto_btn.config(text="⏸ إيقاف التحديث", fg="#f85149")
            self._schedule_refresh()

    def _schedule_refresh(self):
        if not self.auto_on:
            return
        ticker = self.ticker_var.get().strip().upper()
        threading.Thread(target=self._refresh_worker, args=(ticker,), daemon=True).start()
        self._refresh_job = self.after(self.refresh_seconds * 1000, self._schedule_refresh)

    def _refresh_worker(self, ticker):
        try:
            price, intraday = fetch_quick(ticker)
            self.after(0, self._apply_refresh, ticker, price, intraday)
        except Exception as e:
            self.after(0, lambda: self.status.config(
                text=f"تعذّر التحديث: {e}", fg="#f85149"))

    def _apply_refresh(self, ticker, price, intraday):
        now = dt.datetime.now().strftime("%H:%M:%S")
        self.status.config(text=f"آخر تحديث {ticker}: {price:.2f}  ({now})", fg=self.col_accent)
        # تحديث الرسم اليومي
        self._draw_intraday(ticker, intraday, price)
        # فحص نقاط التنبيه
        self._check_alerts(ticker, price)

    def _draw_intraday(self, ticker, intraday, price):
        for w in self.chart_holder.winfo_children():
            w.destroy()
        fig = Figure(figsize=(10, 6), dpi=100, facecolor="#1a2027")
        ax = fig.add_subplot(111, facecolor="#0f1419")
        c = intraday["Close"]
        ax.plot(c.index, c.values, color="#2dd4bf", lw=1.5)
        for label, lvl in self.alert_levels.items():
            col = "#3fb950" if "دخول" in label else "#f85149" if "وقف" in label else "#d29922"
            ax.axhline(lvl, color=col, ls="--", lw=0.9, alpha=0.8)
        ax.set_title(f"{ticker} - تحديث مباشر | السعر {price:.2f}", color="#e6edf3")
        ax.tick_params(colors="#8b949e")
        for s in ax.spines.values():
            s.set_color("#30363d")
        ax.grid(True, color="#21262d", linewidth=0.5)
        fig.tight_layout()
        canvas = FigureCanvasTkAgg(fig, master=self.chart_holder)
        canvas.draw()
        canvas.get_tk_widget().pack(fill="both", expand=True)

    # =========================================================
    #  التنبيهات
    # =========================================================
    def _check_alerts(self, ticker, price):
        for label, lvl in self.alert_levels.items():
            key = f"{ticker}:{label}"
            if key in self.alert_fired:
                continue
            # تنبيه عند الاقتراب من المستوى بنسبة 0.3%
            if abs(price - lvl) / max(lvl, 0.01) <= 0.003:
                self.alert_fired.add(key)
                self._fire_alert(ticker, label, lvl, price)

    def _fire_alert(self, ticker, label, lvl, price):
        msg = f"{ticker} وصل إلى {label} ({lvl:.2f}) | السعر الحالي {price:.2f}"
        # صوت
        if winsound:
            try:
                winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
            except Exception:
                pass
        else:
            self.bell()
        # إشعار ويندوز سطح المكتب
        if _plyer_notify:
            try:
                _plyer_notify.notify(title=f"تنبيه سعري - {ticker}",
                                     message=msg, timeout=10)
            except Exception:
                pass
        # نافذة منبثقة داخل التطبيق
        messagebox.showinfo("🔔 تنبيه سعري", msg)


if __name__ == "__main__":
    app = StockApp()
    app.mainloop()
