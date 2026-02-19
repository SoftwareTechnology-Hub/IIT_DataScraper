"""
╔══════════════════════════════════════════════════════════════╗
║   SmartExtract — Enterprise Keyword Intelligence             ║
║   Built with CustomTkinter · Green & White Corporate Theme   ║
╠══════════════════════════════════════════════════════════════╣
║   Install:  pip install customtkinter requests               ║
║             pip install beautifulsoup4 lxml                  ║
║   Run:      python gui.py                                    ║
╚══════════════════════════════════════════════════════════════╝
"""

import sys
import os
import re
import csv
import time
import threading
from datetime import datetime
from collections import defaultdict
from threading import Lock
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from urllib.parse import urljoin, urlparse
import tkinter as tk
from tkinter import filedialog, messagebox
import tkinter.ttk as ttk

import customtkinter as ctk

# ── Scraper import ────────────────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from scraper import (
        PageResult, fetch_page, make_session,
        extract_names, extract_emails, extract_phones,
        extract_departments, extract_snippets, count_keyword,
        normalize_url, is_valid_url, setup_logger,
    )
    from bs4 import BeautifulSoup
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    SCRAPER_OK = True
    SCRAPER_ERR = ""
except ImportError as e:
    SCRAPER_OK = False
    SCRAPER_ERR = str(e)

# ══════════════════════════════════════════════════════════════════════════════
#  THEME
# ══════════════════════════════════════════════════════════════════════════════
ctk.set_appearance_mode("light")
ctk.set_default_color_theme("green")

# ══════════════════════════════════════════════════════════════════════════════
#  PALETTES
# ══════════════════════════════════════════════════════════════════════════════
LIGHT = {
    "SB":       "#163D25",
    "SB2":      "#1E5230",
    # FIX: entry/slider trough — was #2ECC71 (too bright). Now a solid dark green.
    "SB3":      "#1A4731",
    "SB_HOVER": "#2E7D4F",
    "PRI":      "#1E8449",
    "BRT":      "#27AE60",
    "VVD":      "#2ECC71",
    "LGT":      "#A9DFBF",
    "XLT":      "#D5F5E3",
    "CARD":     "#FFFFFF",
    "CARD2":    "#F2FAF5",
    "ROOT":     "#EEF7F1",
    "GLOW":     "#58D68D",
    "TDK":      "#0D2B17",
    "TMED":     "#1E5631",
    "TLT":      "#6AAB80",
    "TXLT":     "#A8C9B2",
    "STXT":     "#E8F8EE",
    "STXT2":    "#7EC99A",
    "DGR":      "#C0392B",
    "DGR2":     "#E74C3C",
    "WRN":      "#E67E22",
    "INF":      "#2471A3",
    "WHT":      "#FFFFFF",
    "BDR":      "#D5EDE0",
    "LOG_BG":   "#FFFFFF",
    "LOG_FG":   "#1E5631",
    "SEP_CLR":  "#B2DFCC",
}

DARK = {
    "SB":       "#1A2E20",
    "SB2":      "#22382A",
    "SB3":      "#2A4733",
    "SB_HOVER": "#33573F",
    "PRI":      "#27AE60",
    "BRT":      "#2ECC71",
    "VVD":      "#58D68D",
    "LGT":      "#A9DFBF",
    "XLT":      "#1E3D2A",
    "CARD":     "#1E2D24",
    "CARD2":    "#233228",
    "ROOT":     "#181F1B",
    "GLOW":     "#58D68D",
    "TDK":      "#E8F8EE",
    "TMED":     "#A9DFBF",
    "TLT":      "#6AAB80",
    "TXLT":     "#4A7A5A",
    "STXT":     "#E8F8EE",
    "STXT2":    "#7EC99A",
    "DGR":      "#C0392B",
    "DGR2":     "#E74C3C",
    "WRN":      "#E67E22",
    "INF":      "#2980B9",
    "WHT":      "#FFFFFF",
    "BDR":      "#2A4733",
    "LOG_BG":   "#1E2D24",
    "LOG_FG":   "#A9DFBF",
    "SEP_CLR":  "#2A4733",
}

P = dict(LIGHT)

# ── Fonts ─────────────────────────────────────────────────────────────────────
F_BRAND  = ("Segoe UI", 14, "bold")
F_LG     = ("Segoe UI", 12, "bold")
F_MED    = ("Segoe UI", 11)
F_MED_B  = ("Segoe UI", 11, "bold")
F_SM     = ("Segoe UI", 9)
F_SM_B   = ("Segoe UI", 9, "bold")
F_XS     = ("Segoe UI", 8)
F_XS_B   = ("Segoe UI", 8, "bold")
F_MONO   = ("Consolas", 10)
F_MONO_S = ("Consolas", 9)
F_STAT   = ("Segoe UI", 26, "bold")


# ══════════════════════════════════════════════════════════════════════════════
#  CRAWLER
# ══════════════════════════════════════════════════════════════════════════════
class CrawlerWorker:
    def __init__(self, start_url, keywords, max_depth, max_workers,
                 rate_limit, allow_subdomains, timeout, max_pages,
                 log_cb, result_cb, progress_cb, done_cb):
        self.start_url        = start_url
        self.keywords         = [k.strip() for k in re.split(r"[,;]+", keywords) if k.strip()]
        self.max_depth        = max_depth
        self.max_workers      = max_workers
        self.rate_limit       = rate_limit
        self.allow_subdomains = allow_subdomains
        self.timeout          = timeout
        self.max_pages        = max_pages
        self._log      = log_cb
        self._result   = result_cb
        self._progress = progress_cb
        self._done     = done_cb
        self._stop     = False
        self.base_domain = urlparse(start_url).netloc.lower()
        self.visited   = set()
        self.lock      = Lock()
        self.stats     = defaultdict(int)
        self.session   = make_session(timeout)
        self.logger    = setup_logger(False)

    def stop(self): self._stop = True

    def _seen(self, url):
        with self.lock:
            if url in self.visited: return True
            self.visited.add(url); return False

    def _crawl(self, url, depth):
        if self._stop: return []
        time.sleep(self.rate_limit)
        self._log(f"[{depth}/{self.max_depth}]  {url}", "crawl")
        self.stats["crawled"] += 1
        resp = fetch_page(self.session, url, self.timeout, logger=self.logger)
        if not resp:
            self.stats["failed"] += 1; return []
        if "text/html" not in resp.headers.get("Content-Type", ""):
            return []
        soup  = BeautifulSoup(resp.text, "lxml")
        text  = soup.get_text(separator=" ")
        title = (soup.title.string.strip()
                 if soup.title and soup.title.string else "")
        hits  = sum(count_keyword(text, kw) for kw in self.keywords)
        links = []
        if hits > 0:
            snippets, matched = [], []
            for kw in self.keywords:
                if count_keyword(text, kw) > 0:
                    matched.append(kw)
                    snippets.extend(extract_snippets(text, kw, max_snippets=2))
            r = PageResult(
                url=url, names=extract_names(text),
                emails=extract_emails(text), phones=extract_phones(text),
                departments=extract_departments(text),
                matched_snippets=snippets, keyword_count=hits,
                page_title=title, depth=depth)
            r.matched_keywords = matched
            with self.lock: self.stats["matched"] += 1
            self._result(r)
            self._log(f"● MATCH  {title or url}  [{', '.join(matched)}]  ×{hits}", "match")
        self._progress(self.stats["crawled"], self.stats["matched"], self.max_pages)
        if depth < self.max_depth:
            for tag in soup.find_all("a", href=True):
                full = normalize_url(urljoin(url, tag["href"]))
                if is_valid_url(full, self.base_domain, self.allow_subdomains):
                    links.append(full)
        return links

    def run(self):
        self._log(f"Crawl started → {self.start_url}", "info")
        self._log(f"Keywords: {' · '.join(self.keywords)}", "info")
        frontier = [(self.start_url, 0)]
        self.visited.add(normalize_url(self.start_url))
        pending = 0
        with ThreadPoolExecutor(max_workers=self.max_workers) as ex:
            futures = {}
            def fill():
                nonlocal pending
                while (frontier and not self._stop
                       and len(futures)+pending < self.max_workers*4
                       and self.stats["crawled"]+pending < self.max_pages):
                    url, depth = frontier.pop(0)
                    f = ex.submit(self._crawl, url, depth)
                    futures[f] = (url, depth); pending += 1
            fill()
            while futures and not self._stop:
                done, _ = wait(futures, timeout=0.5, return_when=FIRST_COMPLETED)
                for f in done:
                    url, depth = futures.pop(f); pending -= 1
                    try:
                        for link in (f.result() or []):
                            if not self._seen(link):
                                frontier.append((link, depth+1))
                    except Exception as e:
                        self.logger.error(str(e))
                fill()
        s = "Stopped" if self._stop else "Complete"
        self._log(f"{s} — Crawled: {self.stats['crawled']}  "
                  f"Matched: {self.stats['matched']}  "
                  f"Failed: {self.stats['failed']}", "done")
        self._done(self.stats["crawled"], self.stats["matched"])


# ══════════════════════════════════════════════════════════════════════════════
#  WIDGETS
# ══════════════════════════════════════════════════════════════════════════════
class StatCard(ctk.CTkFrame):
    def __init__(self, parent, label, accent, **kw):
        super().__init__(parent, fg_color=P["CARD"], corner_radius=14,
                         border_width=1, border_color=P["XLT"], **kw)
        self._accent = accent
        ctk.CTkFrame(self, height=6, fg_color=accent, corner_radius=0).pack(fill="x")
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=16, pady=(10, 14))
        self.val = ctk.CTkLabel(body, text="0", font=F_STAT,
                                text_color=accent, anchor="w")
        self.val.pack(anchor="w")
        ctk.CTkLabel(body, text=label.upper(), font=F_XS_B,
                     text_color=P["TXLT"], anchor="w").pack(anchor="w")

    def set(self, v): self.val.configure(text=f"{v:,}")


class SliderRow(ctk.CTkFrame):
    def __init__(self, parent, label, var, lo, hi, cast=int, steps=100, **kw):
        super().__init__(parent, fg_color="transparent", **kw)
        self._cast = cast
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x")
        ctk.CTkLabel(top, text=label, font=F_XS,
                     text_color=P["STXT2"], anchor="w").pack(side="left")
        self._val_lbl = ctk.CTkLabel(top, text=str(var.get()), font=F_XS_B,
                                     text_color=P["GLOW"], width=44, anchor="e")
        self._val_lbl.pack(side="right")
        ctk.CTkSlider(self, variable=var, from_=lo, to=hi,
                      number_of_steps=steps,
                      progress_color=P["BRT"], button_color=P["GLOW"],
                      button_hover_color=P["VVD"], fg_color=P["SB3"],
                      height=16,
                      command=self._on_change).pack(fill="x", pady=(3, 0))

    def _on_change(self, v):
        self._val_lbl.configure(text=str(self._cast(float(v))))


class CardFrame(ctk.CTkFrame):
    def __init__(self, parent, title="", badge="", **kw):
        super().__init__(parent, fg_color=P["CARD"], corner_radius=14,
                         border_width=1, border_color=P["BDR"], **kw)
        if title:
            hdr = ctk.CTkFrame(self, fg_color=P["CARD2"], height=42, corner_radius=0)
            hdr.pack(fill="x"); hdr.pack_propagate(False)
            ctk.CTkLabel(hdr, text=title, font=F_SM_B,
                         text_color=P["TMED"], anchor="w").pack(side="left", padx=16)
            self._badge = ctk.CTkLabel(hdr, text=badge, font=F_XS, text_color=P["TXLT"])
            self._badge.pack(side="right", padx=16)
            ctk.CTkFrame(self, height=2, fg_color=P["SEP_CLR"], corner_radius=0).pack(fill="x")

    def set_badge(self, text):
        if hasattr(self, "_badge"):
            self._badge.configure(text=text)


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN APPLICATION
# ══════════════════════════════════════════════════════════════════════════════
class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("SmartExtract — Enterprise Keyword Intelligence")
        self.geometry("1400x880")
        self.minsize(1080, 680)
        self.configure(fg_color=P["ROOT"])

        self._results      = []   # ← persists across theme toggles
        self._crawler      = None
        self._running      = False
        self._pulse_job    = None
        self._pulse_step   = 0
        self._is_dark      = False
        self._crawl_stats  = (0, 0)   # (crawled, matched) — saved across toggle

        self._build()

    # ══════════════════════════════════════════════════════════════════════════
    #  LAYOUT
    # ══════════════════════════════════════════════════════════════════════════
    def _build(self):
        self.grid_columnconfigure(0, weight=0)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self._build_sidebar()
        self._build_main()
        # After rebuild, re-populate results table if data exists
        self._restore_results()

    def _restore_results(self):
        """Re-populate tree & stat cards from in-memory _results after rebuild."""
        if not self._results:
            return
        for r in self._results:
            mkw = getattr(r, "matched_keywords", [])
            tag = "even" if (self._tree.index(self._tree.get_children()[-1]) + 1
                             if self._tree.get_children() else 0) % 2 == 0 else "odd"
            self._tree.insert("", "end", tags=(tag,), values=(
                r.page_title or r.url,
                ", ".join(mkw),
                "; ".join(r.names[:2]) or "—",
                ", ".join(r.emails[:2]) or "—",
                r.keyword_count, r.depth))
        te = sum(len(x.emails) for x in self._results)
        tn = sum(len(x.names)  for x in self._results)
        crawled, matched = self._crawl_stats
        self._c_crawled.set(crawled)
        self._c_matched.set(len(self._results))
        self._c_emails.set(te)
        self._c_names.set(tn)
        self._res_card.set_badge(f"{len(self._results):,} result(s)")
        self._prog_lbl.configure(text=f"{crawled:,} pages crawled")
        self._prog.set(min(1.0, crawled / max(self._pages.get(), 1)))

    # ─────────────────────────────────────────────────────────────────────────
    #  SIDEBAR
    # ─────────────────────────────────────────────────────────────────────────
    def _build_sidebar(self):
        self._sb = ctk.CTkFrame(self, width=280, fg_color=P["SB"], corner_radius=0)
        self._sb.grid(row=0, column=0, sticky="nsew")
        self._sb.grid_propagate(False)
        self._sb.grid_columnconfigure(0, weight=1)
        self._sb.grid_rowconfigure(0, weight=0)
        self._sb.grid_rowconfigure(1, weight=1)
        self._sb.grid_rowconfigure(2, weight=0)

        # ── Brand ─────────────────────────────────────────────────────────
        self._brand = ctk.CTkFrame(self._sb, height=80, fg_color=P["SB2"], corner_radius=0)
        self._brand.grid(row=0, column=0, sticky="ew")
        self._brand.grid_propagate(False)

        logo_shadow = ctk.CTkFrame(self._brand, width=52, height=52,
                                   fg_color="#0D2B17", corner_radius=13)
        logo_shadow.place(x=16, y=14)
        logo_shadow.pack_propagate(False)
        logo = ctk.CTkFrame(logo_shadow, width=46, height=46,
                            fg_color=P["BRT"], corner_radius=11)
        logo.place(x=2, y=0)
        logo.pack_propagate(False)
        ctk.CTkLabel(logo, text="SE", font=("Segoe UI", 14, "bold"),
                     text_color="#FFFFFF").place(relx=0.5, rely=0.5, anchor="center")

        txt = ctk.CTkFrame(self._brand, fg_color="transparent")
        txt.place(x=80, y=18)
        ctk.CTkLabel(txt, text="SmartExtract", font=F_BRAND,
                     text_color=P["STXT"]).pack(anchor="w")
        ctk.CTkLabel(txt, text="Keyword Intelligence", font=F_XS,
                     text_color=P["STXT2"]).pack(anchor="w")

        # Theme toggle — top-right of brand bar
        toggle_icon = "☀️" if self._is_dark else "🌙"
        self._toggle_btn = ctk.CTkButton(
            self._brand, text=toggle_icon, width=34, height=34,
            command=self._toggle_theme,
            fg_color=P["SB3"], hover_color=P["SB_HOVER"],
            text_color=P["STXT"], font=("Segoe UI", 14),
            corner_radius=8)
        self._toggle_btn.place(x=230, y=23)

        ctk.CTkFrame(self._sb, height=2, fg_color=P["GLOW"],
                     corner_radius=0).grid(row=0, column=0, sticky="sew")

        # ── Scrollable config ─────────────────────────────────────────────
        scr = ctk.CTkScrollableFrame(self._sb, fg_color=P["SB"],
                                     scrollbar_button_color=P["SB3"],
                                     scrollbar_button_hover_color=P["SB_HOVER"])
        scr.grid(row=1, column=0, sticky="nsew")

        PD = 16

        def sec(text, pt=16):
            ctk.CTkFrame(scr, height=pt, fg_color="transparent").pack()
            ctk.CTkLabel(scr, text=text, font=F_XS_B,
                         text_color=P["STXT2"], anchor="w").pack(fill="x", padx=PD, pady=(0, 5))

        def hdiv():
            ctk.CTkFrame(scr, height=1, fg_color=P["SB3"],
                         corner_radius=0).pack(fill="x", padx=PD, pady=10)

        sec("TARGET URL")
        self._url = ctk.StringVar()
        ctk.CTkEntry(scr, textvariable=self._url,
                     placeholder_text="https://example.com",
                     fg_color=P["SB3"], border_color=P["SB_HOVER"],
                     text_color=P["STXT"], placeholder_text_color=P["STXT2"],
                     font=F_MONO_S, height=38, corner_radius=8
                     ).pack(fill="x", padx=PD, pady=(0, 6))

        sec("KEYWORDS")
        self._kw = ctk.StringVar()
        ctk.CTkEntry(scr, textvariable=self._kw,
                     placeholder_text="wireless, IoT, 5G, machine learning",
                     fg_color=P["SB3"], border_color=P["SB_HOVER"],
                     text_color=P["STXT"], placeholder_text_color=P["STXT2"],
                     font=F_MONO_S, height=38, corner_radius=8
                     ).pack(fill="x", padx=PD, pady=(0, 4))

        sec("CRAWL PARAMETERS", pt=10)
        self._depth   = ctk.IntVar(value=2)
        self._workers = ctk.IntVar(value=10)
        self._pages   = ctk.IntVar(value=300)
        self._rate    = ctk.DoubleVar(value=0.05)

        for lbl, var, lo, hi, cast, steps in [
            ("Depth",     self._depth,   1,    5,    int,   4),
            ("Workers",   self._workers, 1,   20,    int,   19),
            ("Max Pages", self._pages,   10, 1000,   int,   99),
            ("Rate (s)",  self._rate,    0.0,  1.0,  float, 20),
        ]:
            SliderRow(scr, lbl, var, lo, hi, cast=cast, steps=steps
                      ).pack(fill="x", padx=PD, pady=(0, 6))

        sec("OPTIONS", pt=6)
        self._subdomain = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(scr, text="Include subdomains",
                        variable=self._subdomain,
                        font=F_MED, text_color=P["STXT2"],
                        fg_color=P["BRT"], hover_color=P["VVD"],
                        border_color=P["SB_HOVER"], checkmark_color="#FFFFFF"
                        ).pack(anchor="w", padx=PD, pady=(0, 8))

        hdiv()

        self._btn_start = ctk.CTkButton(
            scr, text="▶   START CRAWL", command=self._start,
            fg_color=P["BRT"], hover_color=P["VVD"], text_color="#FFFFFF",
            font=F_MED_B, height=44, corner_radius=10)
        self._btn_start.pack(fill="x", padx=PD, pady=4)

        self._btn_stop = ctk.CTkButton(
            scr, text="■   STOP", command=self._stop_crawl,
            fg_color=P["DGR"], hover_color=P["DGR2"], text_color="#FFFFFF",
            font=F_MED_B, height=44, corner_radius=10, state="disabled")
        self._btn_stop.pack(fill="x", padx=PD, pady=4)

        self._btn_export = ctk.CTkButton(
            scr, text="↓   EXPORT CSV", command=self._export,
            fg_color=P["SB2"], hover_color=P["SB3"], text_color=P["STXT2"],
            border_width=1, border_color=P["SB3"],
            font=F_MED_B, height=40, corner_radius=10,
            state="normal" if self._results else "disabled")
        self._btn_export.pack(fill="x", padx=PD, pady=4)

        self._btn_clear = ctk.CTkButton(
            scr, text="✕   CLEAR ALL", command=self._clear,
            fg_color="transparent", hover_color=P["SB3"], text_color=P["STXT2"],
            border_width=1, border_color=P["SB3"],
            font=F_MED, height=38, corner_radius=10)
        self._btn_clear.pack(fill="x", padx=PD, pady=(4, 20))

        # ── Status bar ────────────────────────────────────────────────────
        sbar = ctk.CTkFrame(self._sb, height=52, fg_color=P["SB2"], corner_radius=0)
        sbar.grid(row=2, column=0, sticky="ew")
        sbar.grid_propagate(False)
        sbar.grid_columnconfigure(1, weight=1)

        self._dot = ctk.CTkLabel(sbar, text="●", font=("Segoe UI", 14),
                                  text_color=P["TXLT"])
        self._dot.grid(row=0, column=0, padx=(14, 6), pady=14)

        status_txt = "Running…" if self._running else "Idle — ready to crawl"
        status_clr = P["GLOW"] if self._running else P["STXT2"]
        self._status = ctk.CTkLabel(sbar, text=status_txt,
                                     font=F_SM, text_color=status_clr, anchor="w")
        self._status.grid(row=0, column=1, sticky="ew")

    # ─────────────────────────────────────────────────────────────────────────
    #  MAIN CONTENT
    # ─────────────────────────────────────────────────────────────────────────
    def _build_main(self):
        self._main = ctk.CTkFrame(self, fg_color=P["ROOT"], corner_radius=0)
        self._main.grid(row=0, column=1, sticky="nsew")
        self._main.grid_columnconfigure(0, weight=1)
        self._main.grid_rowconfigure(0, weight=0)
        self._main.grid_rowconfigure(1, weight=0)
        self._main.grid_rowconfigure(2, weight=1)
        self._build_topbar(self._main)
        self._build_cards(self._main)
        self._build_tabs(self._main)

    # ── Topbar ────────────────────────────────────────────────────────────────
    def _build_topbar(self, p):
        tb = ctk.CTkFrame(p, height=74, fg_color=P["CARD"], corner_radius=0)
        tb.grid(row=0, column=0, sticky="ew")
        tb.grid_propagate(False)
        tb.grid_columnconfigure(0, weight=1)

        lf = ctk.CTkFrame(tb, fg_color="transparent")
        lf.grid(row=0, column=0, padx=28, pady=14, sticky="w")
        ctk.CTkLabel(lf, text="Crawl Dashboard",
                     font=("Segoe UI", 16, "bold"), text_color=P["TDK"]
                     ).pack(anchor="w")
        ctk.CTkLabel(lf, text="Real-time keyword extraction & contact discovery",
                     font=F_XS, text_color=P["TLT"]).pack(anchor="w")

        rf = ctk.CTkFrame(tb, fg_color="transparent")
        rf.grid(row=0, column=1, padx=28, pady=14, sticky="e")
        ctk.CTkLabel(rf, text="CRAWL PROGRESS", font=F_XS_B,
                     text_color=P["TXLT"]).pack(anchor="e")
        self._prog = ctk.CTkProgressBar(rf, width=300, height=8,
                                         progress_color=P["BRT"], fg_color=P["XLT"],
                                         corner_radius=4)
        self._prog.set(0)
        self._prog.pack(anchor="e", pady=(4, 2))
        self._prog_lbl = ctk.CTkLabel(rf, text="0 / 0 pages",
                                       font=F_XS, text_color=P["TLT"])
        self._prog_lbl.pack(anchor="e")

        ctk.CTkFrame(p, height=1, fg_color=P["XLT"],
                     corner_radius=0).grid(row=0, column=0, sticky="sew")

    # ── Stat cards ────────────────────────────────────────────────────────────
    def _build_cards(self, p):
        row = ctk.CTkFrame(p, fg_color=P["ROOT"], corner_radius=0)
        row.grid(row=1, column=0, sticky="ew", padx=24, pady=(16, 8))
        for i in range(4):
            row.grid_columnconfigure(i, weight=1)

        self._c_crawled = StatCard(row, "Crawled", P["PRI"])
        self._c_matched = StatCard(row, "Matched", P["VVD"])
        self._c_emails  = StatCard(row, "Emails",  P["INF"])
        self._c_names   = StatCard(row, "Names",   P["WRN"])
        for i, c in enumerate((self._c_crawled, self._c_matched,
                                self._c_emails, self._c_names)):
            c.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else 10, 0))

    # ── Tab area ──────────────────────────────────────────────────────────────
    def _build_tabs(self, p):
        wrap = ctk.CTkFrame(p, fg_color=P["ROOT"], corner_radius=0)
        wrap.grid(row=2, column=0, sticky="nsew", padx=24, pady=(0, 16))
        wrap.grid_rowconfigure(1, weight=1)
        wrap.grid_columnconfigure(0, weight=1)

        tabbar = ctk.CTkFrame(wrap, height=44, fg_color=P["CARD2"],
                              corner_radius=0, border_width=1, border_color=P["BDR"])
        tabbar.grid(row=0, column=0, sticky="ew")
        tabbar.grid_propagate(False)

        self._tabs = {}
        self._active_tab = None

        content = ctk.CTkFrame(wrap, fg_color=P["ROOT"], corner_radius=0)
        content.grid(row=1, column=0, sticky="nsew")
        content.grid_rowconfigure(0, weight=1)
        content.grid_columnconfigure(0, weight=1)

        for name, icon_label in [
            ("log",     "  📋  Activity Log  "),
            ("results", "  📊  Results Table  "),
            ("detail",  "  🔍  Detail View  "),
        ]:
            btn = ctk.CTkButton(tabbar, text=icon_label,
                                command=lambda n=name: self._show_tab(n),
                                font=F_SM, height=44, corner_radius=0,
                                border_width=0, fg_color="transparent",
                                hover_color=P["XLT"], text_color=P["TLT"])
            btn.pack(side="left")
            frame = ctk.CTkFrame(content, fg_color=P["ROOT"], corner_radius=0)
            frame.grid_rowconfigure(0, weight=1)
            frame.grid_columnconfigure(0, weight=1)
            self._tabs[name] = (frame, btn)

        self._build_log_tab(self._tabs["log"][0])
        self._build_results_tab(self._tabs["results"][0])
        self._build_detail_tab(self._tabs["detail"][0])
        self._show_tab("log")

    def _show_tab(self, name):
        if self._active_tab:
            old_frame, old_btn = self._tabs[self._active_tab]
            old_frame.grid_remove()
            old_btn.configure(text_color=P["TLT"], fg_color="transparent", font=F_SM)
        frame, btn = self._tabs[name]
        frame.grid(row=0, column=0, sticky="nsew")
        btn.configure(text_color=P["PRI"], fg_color=P["CARD"], font=F_SM_B)
        self._active_tab = name

    # ── Log tab ───────────────────────────────────────────────────────────────
    def _build_log_tab(self, parent):
        card = CardFrame(parent, title="Live Activity Feed", badge="")
        card.grid(row=0, column=0, sticky="nsew")
        inner = ctk.CTkFrame(card, fg_color=P["CARD"], corner_radius=0)
        inner.pack(fill="both", expand=True)
        self._log = tk.Text(inner,
                            bg=P["LOG_BG"], fg=P["LOG_FG"], font=F_MONO_S,
                            relief="flat", bd=0, wrap="word",
                            state="disabled", padx=14, pady=12,
                            selectbackground=P["XLT"],
                            insertbackground=P["BRT"])
        vsb = ctk.CTkScrollbar(inner, command=self._log.yview,
                                button_color=P["LGT"], button_hover_color=P["BRT"])
        self._log.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y", padx=(0, 4), pady=6)
        self._log.pack(fill="both", expand=True, padx=(4, 0), pady=4)
        for tag, fg, bold in [
            ("ts",    P["TXLT"], False),
            ("crawl", P["TXLT"], False),
            ("match", P["PRI"],  True),
            ("info",  P["INF"],  False),
            ("done",  P["BRT"],  True),
            ("error", P["DGR"],  False),
            ("dot",   P["BRT"],  False),
        ]:
            kw = {"foreground": fg}
            if bold: kw["font"] = ("Consolas", 9, "bold")
            self._log.tag_configure(tag, **kw)
        # Full-width separator tag for "done" dividers
        self._log.tag_configure("fulldiv",
                                foreground=P["SEP_CLR"],
                                font=("Consolas", 7))

    # ── Results tab ───────────────────────────────────────────────────────────
    def _build_results_tab(self, parent):
        self._res_card = CardFrame(parent, title="Matched Pages",
                                   badge="No results yet")
        self._res_card.grid(row=0, column=0, sticky="nsew")

        style = ttk.Style()
        style.theme_use("clam")
        style.configure("SE.Treeview",
                        background=P["CARD"], foreground=P["TDK"],
                        fieldbackground=P["CARD"], rowheight=32,
                        font=("Segoe UI", 10), relief="flat", borderwidth=0)
        style.configure("SE.Treeview.Heading",
                        background=P["CARD2"], foreground=P["TMED"],
                        font=("Segoe UI", 9, "bold"), relief="flat",
                        borderwidth=0, padding=(10, 8))
        style.map("SE.Treeview",
                  background=[("selected", P["XLT"])],
                  foreground=[("selected", P["TDK"])])

        inner = ctk.CTkFrame(self._res_card, fg_color=P["CARD"], corner_radius=0)
        inner.pack(fill="both", expand=True)

        cols = ("title", "kw", "names", "emails", "hits", "d")
        self._tree = ttk.Treeview(inner, columns=cols,
                                   show="headings", selectmode="browse",
                                   style="SE.Treeview")
        for col, lbl, w, anch in [
            ("title",  "Page Title",   290, "w"),
            ("kw",     "Keywords Hit", 140, "w"),
            ("names",  "Names",        155, "w"),
            ("emails", "Email",        175, "w"),
            ("hits",   "Hits",          52, "center"),
            ("d",      "D",             36, "center"),
        ]:
            self._tree.heading(col, text=lbl, anchor=anch)
            self._tree.column(col, width=w, anchor=anch, minwidth=36)
        self._tree.tag_configure("odd",  background=P["CARD"])
        self._tree.tag_configure("even", background=P["CARD2"])

        vsb = ctk.CTkScrollbar(inner, command=self._tree.yview,
                                button_color=P["LGT"], button_hover_color=P["BRT"])
        hsb = ctk.CTkScrollbar(inner, orientation="horizontal",
                                command=self._tree.xview,
                                button_color=P["LGT"], button_hover_color=P["BRT"])
        self._tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        vsb.pack(side="right", fill="y", padx=(0, 4), pady=4)
        hsb.pack(side="bottom", fill="x", padx=4, pady=(0, 4))
        self._tree.pack(fill="both", expand=True, padx=(4, 0), pady=4)
        self._tree.bind("<<TreeviewSelect>>", self._on_select)

    # ── Detail tab ────────────────────────────────────────────────────────────
    def _build_detail_tab(self, parent):
        card = CardFrame(parent, title="Page Detail",
                         badge="Select a row in Results to inspect")
        card.grid(row=0, column=0, sticky="nsew")
        inner = ctk.CTkFrame(card, fg_color=P["CARD"], corner_radius=0)
        inner.pack(fill="both", expand=True)
        self._detail = tk.Text(inner,
                               bg=P["LOG_BG"], fg=P["LOG_FG"], font=F_MONO_S,
                               relief="flat", bd=0, wrap="none",
                               state="disabled", padx=18, pady=16,
                               selectbackground=P["XLT"],
                               insertbackground=P["BRT"])
        vsb = ctk.CTkScrollbar(inner, command=self._detail.yview,
                                button_color=P["LGT"], button_hover_color=P["BRT"])
        hsb = ctk.CTkScrollbar(inner, orientation="horizontal",
                                command=self._detail.xview,
                                button_color=P["LGT"], button_hover_color=P["BRT"])
        self._detail.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        vsb.pack(side="right", fill="y", padx=(0, 4), pady=6)
        hsb.pack(side="bottom", fill="x", padx=4, pady=(0, 4))
        self._detail.pack(fill="both", expand=True, padx=(4, 0), pady=4)

        self._detail.tag_configure("head",    foreground=P["TDK"],
                                   font=("Segoe UI", 13, "bold"))
        self._detail.tag_configure("section", foreground=P["PRI"],
                                   font=("Segoe UI", 8, "bold"))
        self._detail.tag_configure("label",   foreground=P["PRI"],
                                   font=("Segoe UI", 8, "bold"))
        self._detail.tag_configure("value",   foreground=P["TMED"], font=F_MONO_S)
        self._detail.tag_configure("url",     foreground=P["BRT"],
                                   font=F_MONO_S, underline=True)
        self._detail.tag_configure("snippet", foreground="#5D4037",
                                   background="#FFFBF0", font=F_MONO_S,
                                   lmargin1=20, lmargin2=20)
        # Full-width separator — uses background fill to span whole line
        self._detail.tag_configure("sep",
                                   foreground=P["SEP_CLR"],
                                   font=("Consolas", 7))

    # ══════════════════════════════════════════════════════════════════════════
    #  THEME TOGGLE
    # ══════════════════════════════════════════════════════════════════════════
    def _toggle_theme(self):
        # FIX: disabled during crawl — button is disabled while _running, so this
        # check is a safety net.
        if self._running:
            return

        self._is_dark = not self._is_dark
        palette = DARK if self._is_dark else LIGHT
        P.update(palette)
        ctk.set_appearance_mode("dark" if self._is_dark else "light")

        # Destroy & rebuild UI — _results list is NOT cleared (it's on self)
        for widget in self.winfo_children():
            widget.destroy()
        self._tabs = {}
        self._active_tab = None
        self.configure(fg_color=P["ROOT"])
        self._build()

    # ══════════════════════════════════════════════════════════════════════════
    #  ACTIONS
    # ══════════════════════════════════════════════════════════════════════════
    def _start(self):
        if not SCRAPER_OK:
            messagebox.showerror("Import Error",
                f"scraper.py could not be loaded:\n{SCRAPER_ERR}")
            return
        url = self._url.get().strip()
        kw  = self._kw.get().strip()
        if not url or url in ("https://", "http://"):
            messagebox.showwarning("Missing", "Enter a website URL."); return
        if not kw:
            messagebox.showwarning("Missing", "Enter at least one keyword."); return
        if not url.startswith("http"):
            url = "https://" + url

        self._results.clear()
        self._crawl_stats = (0, 0)
        for row in self._tree.get_children(): self._tree.delete(row)
        self._log_clear(); self._detail_clear()
        for c in (self._c_crawled, self._c_matched, self._c_emails, self._c_names):
            c.set(0)
        self._prog.set(0); self._prog_lbl.configure(text="0 / 0 pages")
        self._res_card.set_badge("Crawling…")

        self._running = True
        self._btn_start.configure(state="disabled", fg_color=P["SB3"], text_color=P["STXT2"])
        self._btn_stop.configure(state="normal")
        self._btn_export.configure(state="disabled")
        # FIX: disable toggle while crawling
        self._toggle_btn.configure(state="disabled")
        self._set_status("Running…", P["GLOW"])
        self._pulse_start()

        self._crawler = CrawlerWorker(
            start_url=url, keywords=kw,
            max_depth=self._depth.get(),
            max_workers=self._workers.get(),
            rate_limit=self._rate.get(),
            allow_subdomains=self._subdomain.get(),
            timeout=12, max_pages=self._pages.get(),
            log_cb=lambda m, t: self.after(0, self._log_append, m, t),
            result_cb=lambda r: self.after(0, self._add_result, r),
            progress_cb=lambda c, m, t: self.after(0, self._update_prog, c, m, t),
            done_cb=lambda c, m: self.after(0, self._on_done, c, m),
        )
        threading.Thread(target=self._crawler.run, daemon=True).start()

    def _stop_crawl(self):
        if self._crawler: self._crawler.stop()
        self._set_status("Stopping…", P["WRN"])
        self._btn_stop.configure(state="disabled")

    def _export(self):
        if not self._results:
            messagebox.showinfo("No Data", "No results to export."); return
        path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            initialfile=f"smartextract_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")])
        if not path: return
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["URL", "Page Title", "Keywords Hit", "Keyword Count",
                        "Names", "Emails", "Phones", "Departments",
                        "Top Snippet", "Depth", "Timestamp"])
            for r in self._results:
                w.writerow([
                    r.url, r.page_title,
                    " | ".join(getattr(r, "matched_keywords", [])),
                    r.keyword_count,
                    " | ".join(r.names), " | ".join(r.emails),
                    " | ".join(r.phones), " | ".join(r.departments),
                    r.matched_snippets[0][:300] if r.matched_snippets else "",
                    r.depth, r.timestamp])
        self._log_append(f"Exported {len(self._results)} rows → {path}", "done")
        messagebox.showinfo("Exported",
            f"Saved {len(self._results):,} rows to:\n{path}")

    def _clear(self):
        self._results.clear()
        self._crawl_stats = (0, 0)
        for row in self._tree.get_children(): self._tree.delete(row)
        self._log_clear(); self._detail_clear()
        for c in (self._c_crawled, self._c_matched, self._c_emails, self._c_names):
            c.set(0)
        self._prog.set(0); self._prog_lbl.configure(text="0 / 0 pages")
        self._res_card.set_badge("No results yet")
        self._running = False
        self._pulse_stop()
        self._set_status("Idle — ready to crawl", P["TXLT"])
        self._btn_start.configure(state="normal", fg_color=P["BRT"], text_color="#FFFFFF")
        self._btn_stop.configure(state="disabled")
        self._btn_export.configure(state="disabled", fg_color=P["SB2"], text_color=P["STXT2"])
        self._toggle_btn.configure(state="normal")

    # ── Live update handlers ──────────────────────────────────────────────────
    def _log_append(self, msg, tag):
        self._log.configure(state="normal")
        ts = datetime.now().strftime("%H:%M:%S")
        if tag == "match":
            self._log.insert("end", f"[{ts}] ", "ts")
            self._log.insert("end", "● ", "dot")
            self._log.insert("end", f"{msg}\n", "match")
        elif tag == "done":
            # Full-width divider line
            self._log.insert("end", "\n")
            self._log.insert("end", "─" * 120 + "\n", "fulldiv")
            self._log.insert("end", f"[{ts}]  ", "ts")
            self._log.insert("end", msg + "\n", "done")
            self._log.insert("end", "─" * 120 + "\n\n", "fulldiv")
        else:
            self._log.insert("end", f"[{ts}] ", "ts")
            self._log.insert("end", f"{msg}\n", tag)
        self._log.see("end")
        self._log.configure(state="disabled")

    def _add_result(self, r):
        self._results.append(r)
        mkw = getattr(r, "matched_keywords", [])
        tag = "even" if len(self._results) % 2 == 0 else "odd"
        iid = self._tree.insert("", "end", tags=(tag,), values=(
            r.page_title or r.url,
            ", ".join(mkw),
            "; ".join(r.names[:2])  or "—",
            ", ".join(r.emails[:2]) or "—",
            r.keyword_count, r.depth))
        self._tree.see(iid)
        self._res_card.set_badge(f"{len(self._results):,} result(s)")
        te = sum(len(x.emails) for x in self._results)
        tn = sum(len(x.names)  for x in self._results)
        self._c_matched.set(len(self._results))
        self._c_emails.set(te)
        self._c_names.set(tn)

    def _update_prog(self, crawled, matched, total):
        self._crawl_stats = (crawled, matched)
        self._prog.set(min(1.0, crawled / max(total, 1)))
        self._prog_lbl.configure(text=f"{crawled:,} / {total:,} pages")
        self._c_crawled.set(crawled)
        self._c_matched.set(matched)

    def _on_done(self, crawled, matched):
        self._running = False
        self._crawl_stats = (crawled, matched)
        self._pulse_stop(P["GLOW"])
        self._prog.set(1.0)
        self._prog_lbl.configure(text=f"{crawled:,} pages crawled")
        self._set_status(f"Done — {matched:,} match(es)", P["GLOW"])
        self._btn_start.configure(state="normal", fg_color=P["BRT"], text_color="#FFFFFF")
        self._btn_stop.configure(state="disabled")
        # FIX: re-enable toggle once crawl finishes
        self._toggle_btn.configure(state="normal")
        if self._results:
            self._btn_export.configure(state="normal",
                                       fg_color=P["PRI"], text_color="#FFFFFF")

    def _on_select(self, _=None):
        sel = self._tree.selection()
        if not sel: return
        idx = self._tree.index(sel[0])
        if 0 <= idx < len(self._results):
            self._render_detail(self._results[idx])
            self._show_tab("detail")

    # ── Detail renderer ───────────────────────────────────────────────────────
    def _render_detail(self, r):
        d = self._detail
        d.configure(state="normal"); d.delete("1.0", "end")
        mkw = getattr(r, "matched_keywords", [])

        def w(text, *tags): d.insert("end", text, tags)

        # Full-width top separator
        w("─" * 120 + "\n", "sep")
        w(f"  {r.page_title or 'Untitled Page'}\n", "head")
        w("─" * 120 + "\n\n", "sep")

        for lbl, val, vtag in [
            ("URL",       r.url,                              "url"),
            ("Keywords",  ", ".join(mkw) or "—",             "value"),
            ("Hits",      str(r.keyword_count),               "value"),
            ("Depth",     str(r.depth),                       "value"),
            ("Timestamp", getattr(r, "timestamp", "")[:19],  "value"),
        ]:
            w(f"  {lbl:<16}", "label")
            w(f"{val}\n", vtag)

        def sec(t):
            w("\n")
            w("─" * 120 + "\n", "sep")
            w(f"  {t}\n", "section")
            w("─" * 120 + "\n", "sep")

        if r.names:
            sec("NAMES FOUND")
            for n in r.names: w(f"  • {n}\n", "value")
        if r.emails:
            sec("EMAIL ADDRESSES")
            for e in r.emails: w(f"  • {e}\n", "url")
        if r.phones:
            sec("PHONE NUMBERS")
            for ph in r.phones: w(f"  • {ph}\n", "value")
        if r.departments:
            sec("DEPARTMENTS")
            for dep in r.departments: w(f"  • {dep}\n", "value")
        if r.matched_snippets:
            sec("MATCHED SNIPPETS")
            for i, s in enumerate(r.matched_snippets[:4], 1):
                w(f"\n  [{i}]\n", "label")
                w(f"  …{s[:500]}…\n", "snippet")

        w("\n" + "─" * 120 + "\n", "sep")
        d.configure(state="disabled"); d.yview_moveto(0)

    # ── Status & pulse ────────────────────────────────────────────────────────
    def _set_status(self, text, color):
        self._status.configure(text=text, text_color=color)

    def _pulse_start(self):
        self._pulse_step = 0; self._pulse_tick()

    def _pulse_tick(self):
        if not self._running: return
        cols = [P["GLOW"], P["VVD"], P["BRT"], P["VVD"], P["GLOW"], P["LGT"]]
        self._dot.configure(text_color=cols[self._pulse_step % len(cols)])
        self._pulse_step += 1
        self._pulse_job = self.after(280, self._pulse_tick)

    def _pulse_stop(self, color=None):
        if color is None: color = P["GLOW"]
        if self._pulse_job:
            self.after_cancel(self._pulse_job); self._pulse_job = None
        self._dot.configure(text_color=color)

    def _log_clear(self):
        self._log.configure(state="normal")
        self._log.delete("1.0", "end")
        self._log.configure(state="disabled")

    def _detail_clear(self):
        self._detail.configure(state="normal")
        self._detail.delete("1.0", "end")
        self._detail.configure(state="disabled")


# ══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    app = App()
    app.mainloop()