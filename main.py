import json
import os
import queue
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path
from tkinter import filedialog
from datetime import datetime

import customtkinter as ctk
import matplotlib.dates as mdates
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

import analytics
import bot as station_bot
import database as db
import legal
import excel_io
import payments
import plans
import security
import sender
import site_client
import telegram
from arduino import SerialReader, available_ports, evaluate_status, WATER_MAX
from config import TICK_SECONDS, OFFLINE_SECONDS, FRESH_SECONDS, APP_NAME, ANALYTICS_CACHE
from notifier import Notifier
from soil_profiles import PROFILES, PROFILE_TITLES, sample_soil

try:
    import winsound
except ImportError:
    winsound = None

# ======================= СТЕКЛЯННЫЙ СТИЛЬ (имитация Liquid Glass) =======================
# В стандартном tkinter нельзя сделать настоящее размытие и прозрачность окна.
# Поэтому эффект стекла собран из светлых полупрозрачных оттенков, мягких рамок
# и скруглённых «таблеток»-кнопок. Настоящий blur даёт только системный API Windows.
BG = "#DDE7F8"            # фон окна
GLASS = "#F6F9FF"         # стеклянная панель
GLASS_EDGE = "#FFFFFF"    # светлая кромка стекла
GLASS_SOFT = "#EAF1FD"    # вложенные элементы
SIDEBAR = "#E9F0FC"
TEXT = "#0F1B33"
MUTED = "#66758F"
BLUE = "#4F8FF7"
BLUE_HOVER = "#3F7FEA"
OK = "#34C759"
WARN = "#FF9F0A"
DANGER = "#FF3B30"
STATUS = {"ok": OK, "warn": WARN, "danger": DANGER}
STATUS_TEXT = {"ok": "Норма", "warn": "Внимание", "danger": "Засуха"}
STATUS_SUB = {"ok": "Все показатели в норме", "warn": "Есть один признак засухи",
              "danger": "Несколько признаков засухи"}
MODE_LABELS = {"off": "Выкл", "api": "Сайт (API)", "file": "JSON-файл"}
MODE_KEYS = {v: k for k, v in MODE_LABELS.items()}
RANGES = {"10 мин": 600, "1 час": 3600, "6 часов": 21600, "24 часа": 86400,
          "7 дней": 7 * 86400, "30 дней": 30 * 86400, "90 дней": 90 * 86400}
EXPORT_RANGES = {"24 часа": 1, "7 дней": 7, "30 дней": 30}
MAX_CHART_POINTS = 720     # длинные периоды усредняются, чтобы графики не тормозили
EVENT_KINDS = {"Все": None, "Засуха": "drought", "Связь": "link", "Система": "system"}
EVENT_ICON = {"drought": "💧", "link": "📡", "system": "ℹ"}
ANALYTICS_TTL = 600        # аналитику с сайта обновляем не чаще раза в 10 минут (или кнопкой)
RISK_COLOR = {"ok": "#34C759", "warning": "#FF9F0A", "critical": "#FF3B30"}
LEVEL_COLOR = {"high": "#FF3B30", "mid": "#FF9F0A", "low": "#4F8FF7", "good": "#34C759", "ok": "#FF9F0A",
               "bad": "#FF3B30", "critical": "#FF3B30", "warning": "#FF9F0A"}
EXTRA_INFO = {"salinity": ("Засолённость", "dS/m"), "ph": ("Кислотность pH", ""),
              "n": ("Азот N", "mg/kg"), "p": ("Фосфор P", "mg/kg"), "k": ("Калий K", "mg/kg")}
AVG_LABEL = "Среднее по всем датчикам"
TEST_MARK = " *"
FONT = "Segoe UI"
RADIUS = 26
ROLE_LABEL = {"admin": "Администратор", "expert": "Эксперт", "farmer": "Фермер"}


def font(size, bold=False):
    return ctk.CTkFont(family=FONT, size=size, weight="bold" if bold else "normal")


def glass(master, **kw):
    return ctk.CTkFrame(master, corner_radius=RADIUS, fg_color=GLASS, border_width=2,
                        border_color=GLASS_EDGE, **kw)


def label(master, text, size=13, bold=False, color=MUTED, **kw):
    return ctk.CTkLabel(master, text=text, font=font(size, bold), text_color=color, **kw)


def page_title(master, text):
    return ctk.CTkLabel(master, text=text, font=font(32, True), text_color=TEXT)


def pill(master, text, command, width=160, primary=True, danger=False):
    if primary:
        return ctk.CTkButton(master, text=text, command=command, width=width, height=42, corner_radius=21,
                             fg_color=BLUE, hover_color=BLUE_HOVER, text_color="#FFFFFF",
                             border_width=1, border_color="#A9CBFF", font=font(14, True))
    return ctk.CTkButton(master, text=text, command=command, width=width, height=42, corner_radius=21,
                         fg_color="#FFFFFF", hover_color="#EAF2FF", text_color=DANGER if danger else BLUE,
                         border_width=1, border_color="#D5E3F7", font=font(14))


def entry(master, width=300, **kw):
    return ctk.CTkEntry(master, width=width, height=42, corner_radius=16, border_width=1,
                        border_color="#D5E3F7", fg_color="#FFFFFF", text_color=TEXT, font=font(14), **kw)


def dropdown(master, values, command=None, width=260):
    return ctk.CTkOptionMenu(master, values=values, command=command, width=width, height=42, corner_radius=16,
                             fg_color="#FFFFFF", button_color="#D5E3F7", button_hover_color="#BCD3F4",
                             text_color=TEXT, dropdown_fg_color="#FFFFFF", dropdown_text_color=TEXT,
                             dropdown_hover_color=GLASS_SOFT, font=font(14))


def toggle(master, text, command=None):
    return ctk.CTkSwitch(master, text=text, command=command, font=font(14), text_color=TEXT,
                         progress_color=BLUE, button_color="#FFFFFF", button_hover_color="#EAF2FF",
                         fg_color="#C9D6EA")


def style_axes(ax):
    ax.set_facecolor("#FFFFFF")
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color("#D5E3F7")
    ax.tick_params(colors=MUTED, labelsize=9, length=0)
    ax.grid(axis="y", color="#E6EEF9", linewidth=0.8)
    ax.set_axisbelow(True)


def _digits_only(widget):
    text = widget.get()
    clean = "".join(ch for ch in text if ch.isdigit())
    if clean != text:
        widget.delete(0, "end")
        widget.insert(0, clean)


def segmented(master, values, command=None, variable=None, height=36):
    return ctk.CTkSegmentedButton(master, values=values, command=command, variable=variable, font=font(13),
                                  fg_color="#FFFFFF", selected_color=BLUE, selected_hover_color=BLUE_HOVER,
                                  unselected_color="#FFFFFF", unselected_hover_color="#EAF2FF",
                                  text_color=TEXT, corner_radius=18, height=height)


def clear(widget):
    for child in widget.winfo_children():
        child.destroy()


def safe_destroy(widget):
    try:
        widget.destroy()
    except Exception:
        pass


def lock_card(master, title, text, button_text=None, command=None):
    """Карточка «функция недоступна» с необязательной кнопкой."""
    box = glass(master)
    label(box, "🔒  " + title, 17, True, TEXT).pack(anchor="w", padx=24, pady=(18, 4))
    label(box, text, 13, False, MUTED, wraplength=820, justify="left").pack(anchor="w", padx=24, pady=(0, 14))
    if button_text and command:
        pill(box, button_text, command, width=240).pack(anchor="w", padx=24, pady=(0, 18))
    return box


def fmt_ts(value):
    """ISO-время с сайта → «23.11 14:05»."""
    if not value:
        return "—"
    try:
        return datetime.fromisoformat(str(value)).strftime("%d.%m %H:%M")
    except ValueError:
        return str(value)


def fmt_num(value, digits=1, unit=""):
    if value is None:
        return "—"
    return f"{value:.{digits}f}{(' ' + unit) if unit else ''}".replace(".", ",")


# ======================= ВСПЛЫВАЮЩИЕ УВЕДОМЛЕНИЯ =======================
class ToastStack:
    """Всплывающие уведомления в правом нижнем углу окна.

    Раньше одно уведомление висело поверх окна и не закрывалось (ошибка в _safe_destroy), из-за этого
    мешало работать. Теперь: до 3 уведомлений стопкой, каждое исчезает само через 6 секунд,
    при наведении мыши сразу скрывается (не перекрывает элементы под ним), есть крестик.
    Все уведомления сохраняются в истории (раздел «Уведомления»)."""

    LIFETIME_MS = 6000
    MAX = 3
    GAP = 10

    def __init__(self, root):
        self.root = root
        self.items = []

    def show(self, text, ok=True, on_open=None):
        while len(self.items) >= self.MAX:
            self._close(self.items[0])
        frame = ctk.CTkFrame(self.root, corner_radius=18, fg_color="#FFFFFF", border_width=2,
                             border_color=OK if ok else DANGER)
        row = ctk.CTkFrame(frame, fg_color="transparent")
        row.pack(fill="x", padx=(16, 8), pady=10)
        dot = label(row, "●", 14, True, OK if ok else DANGER)
        dot.pack(side="left", anchor="n", padx=(0, 8))
        text_label = label(row, text, 13, True, TEXT, wraplength=330, justify="left")
        text_label.pack(side="left", fill="x", expand=True)
        close = ctk.CTkButton(row, text="✕", width=26, height=26, corner_radius=13, fg_color="transparent",
                              hover_color="#EAF2FF", text_color=MUTED, font=font(12),
                              command=lambda: self._close(frame))
        close.pack(side="right", anchor="n", padx=(6, 0))
        for widget in (frame, row, dot, text_label):
            widget.bind("<Enter>", lambda e, f=frame: self._close(f))
            if on_open:
                widget.bind("<Button-1>", lambda e, f=frame: (self._close(f), on_open()))
        self.items.append(frame)
        self._layout()
        self.root.after(self.LIFETIME_MS, lambda f=frame: self._close(f))

    def _close(self, frame):
        if frame in self.items:
            self.items.remove(frame)
            safe_destroy(frame)
            self._layout()

    def _layout(self):
        y = -20
        for frame in reversed(self.items):
            try:
                frame.place(relx=1.0, rely=1.0, x=-24, y=y, anchor="se")
                frame.update_idletasks()
                y -= frame.winfo_reqheight() + self.GAP
            except Exception:
                pass

    def clear(self):
        for frame in list(self.items):
            self._close(frame)


# ======================= ВХОД / РЕГИСТРАЦИЯ =======================
class AuthScreen(ctk.CTkFrame):
    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.card = glass(self)
        self.card.place(relx=0.5, rely=0.5, anchor="center")
        label(self.card, "●", 40, True, BLUE).pack(pady=(40, 0), padx=70)
        label(self.card, app.brand_name(), 26, True, TEXT).pack(pady=(4, 0))
        self.caption = label(self.card, "")
        self.caption.pack(pady=(4, 20))
        self.body = ctk.CTkFrame(self.card, fg_color="transparent")
        self.body.pack(padx=40)
        self.error = label(self.card, "", 13, False, DANGER, wraplength=340, justify="center")
        self.error.pack(pady=(8, 0))
        self.footer = ctk.CTkFrame(self.card, fg_color="transparent")
        self.footer.pack(pady=(10, 36))
        self.show_login()

    def _reset(self, caption):
        for w in self.body.winfo_children() + self.footer.winfo_children():
            w.destroy()
        self.error.configure(text="")
        self.caption.configure(text=caption)

    def show_login(self, admin=False):
        self._reset("Служебный вход" if admin else "Вход в систему")
        self.login_user = entry(self.body, 300, placeholder_text="Логин")
        self.login_user.pack(pady=6)
        self.login_pass = entry(self.body, 300, placeholder_text="Пароль", show="•")
        self.login_pass.pack(pady=6)
        action = (lambda: self.submit_admin()) if admin else (lambda: self.submit_login())
        self.login_pass.bind("<Return>", lambda e: action())
        pill(self.body, "Войти", action, width=300).pack(pady=(14, 0))
        if admin:
            if not db.has_role("admin"):
                self.caption.configure(text="Первый служебный вход: создайте администратора")
                pill(self.body, "Создать администратора", self.create_admin, width=300).pack(pady=(10, 0))
            pill(self.footer, "← назад", lambda: self.show_login(False), width=160, primary=False).pack()
        else:
            pill(self.footer, "Регистрация", self.show_register, width=160, primary=False).pack()

    def _fail(self, text):
        self.error.configure(text=text, text_color=DANGER)

    def _ok(self, text):
        self.error.configure(text=text, text_color=OK)

    def submit_login(self):
        login = self.login_user.get().strip()
        password = self.login_pass.get()
        if not login or not password:
            return self._fail("Введите логин и пароль")
        user = db.get_user(login)
        if not user or not security.check_password(password, user["salt"], user["password_hash"]):
            return self._fail("Неверный логин или пароль")
        self.app.on_login(dict(user))

    def submit_admin(self):
        login = self.login_user.get().strip()
        password = self.login_pass.get()
        if not login or not password:
            return self._fail("Введите логин и пароль")
        user = db.get_user(login)
        if not user or user["role"] != "admin" or \
                not security.check_password(password, user["salt"], user["password_hash"]):
            return self._fail("Неверные данные служебного входа")
        self.app.on_login(dict(user))

    def create_admin(self):
        name = self.login_user.get().strip()
        pwd = self.login_pass.get()
        if len(name) < 3:
            return self._fail("Логин администратора не короче 3 символов")
        if len(pwd) < 6:
            return self._fail("Пароль администратора не короче 6 символов")
        try:
            salt, pwd_hash = security.make_hash(pwd)
            db.create_user(name, salt, pwd_hash, "admin", {})
        except Exception as err:
            return self._fail(f"Не удалось создать администратора: {err}")
        self.app.on_login(dict(db.get_user(name)))

    def show_register(self):
        self._reset("Регистрация")
        self.role = ctk.CTkSegmentedButton(self.body, values=["Эксперт", "Фермер"], height=40, corner_radius=20,
                                           font=font(14), fg_color="#FFFFFF", selected_color=BLUE,
                                           selected_hover_color=BLUE_HOVER, unselected_color="#FFFFFF",
                                           unselected_hover_color="#EAF2FF", text_color=TEXT,
                                           command=lambda _: self._register_fields())
        self.role.set("Эксперт")
        self.role.pack(pady=(0, 10))
        self.fields_box = ctk.CTkFrame(self.body, fg_color="transparent")
        self.fields_box.pack()
        self._register_fields()
        pill(self.footer, "← назад", self.show_login, width=160, primary=False).pack()

    def _register_fields(self):
        for w in self.fields_box.winfo_children():
            w.destroy()
        expert = self.role.get() == "Эксперт"
        spec = [("first_name", "Имя", ""), ("last_name", "Фамилия", "")]
        if expert:
            spec += [("company", "Название фирмы", ""), ("position", "Должность (необязательно)", ""),
                     ("phone", "Телефон (только цифры)", "digits")]
        else:
            spec += [("region", "Область / район", ""), ("phone", "Телефон (только цифры)", "digits")]
        spec += [("username", "Логин (от 3 символов)", ""), ("password", "Пароль (от 6 символов)", "pwd"),
                 ("repeat", "Повторите пароль", "pwd")]
        self.reg = {}
        for key, text, kind in spec:
            widget = entry(self.fields_box, 300, placeholder_text=text, show="•" if kind == "pwd" else "")
            widget.pack(pady=4)
            if kind == "digits":
                widget.bind("<KeyRelease>", lambda e, w=widget: _digits_only(w))
            self.reg[key] = widget
        hint = ("Эксперт создаёт фирму. Фермер сможет войти, но увидит датчики только после приглашения эксперта."
                if not expert else "Название фирмы укажите так, как его будут видеть фермеры.")
        label(self.fields_box, hint, 12, False, MUTED, wraplength=300, justify="center").pack(pady=(6, 0))
        self.agree = toggle(self.fields_box, "Я принимаю условия и политику конфиденциальности")
        self.agree.pack(pady=(10, 0))
        pill(self.fields_box, "Читать документы", self.read_legal, width=300, primary=False).pack(pady=(6, 0))
        pill(self.fields_box, "Создать аккаунт", self.register, width=300).pack(pady=(10, 0))

    def read_legal(self):
        win = ctk.CTkToplevel(self)
        win.title("Документы")
        win.geometry("640x560")
        win.configure(fg_color=BG)
        box = ctk.CTkTextbox(win, fg_color="#FFFFFF", text_color=TEXT, font=font(13), corner_radius=16)
        box.pack(fill="both", expand=True, padx=20, pady=20)
        box.insert("end", legal.text("terms") + "\n\n" + legal.text("privacy") + "\n\n"
                   + legal.text("sources") + "\n\n" + legal.text("disclaimer"))
        box.configure(state="disabled")

    def register(self):
        expert = self.role.get() == "Эксперт"
        data = {k: w.get().strip() for k, w in self.reg.items()}
        required = [("first_name", "Имя"), ("last_name", "Фамилия"), ("phone", "Телефон"),
                    ("username", "Логин"), ("password", "Пароль"), ("repeat", "Повтор пароля")]
        required.append(("company", "Название фирмы") if expert else ("region", "Область / район"))
        for key, title in required:
            if not data.get(key):
                return self._fail(f"Не заполнено поле «{title}»")
        if not data["phone"].isdigit():
            return self._fail("Телефон должен содержать только цифры")
        if len(data["username"]) < 3:
            return self._fail("Логин должен быть не короче 3 символов")
        if len(data["password"]) < 6:
            return self._fail("Пароль должен быть не короче 6 символов")
        if data["password"] != data["repeat"]:
            return self._fail("Пароли не совпадают")
        if db.get_user(data["username"]):
            return self._fail(f"Логин «{data['username']}» уже занят, выберите другой")
        if not self.agree.get():
            return self._fail("Чтобы зарегистрироваться, примите условия и политику конфиденциальности")
        profile = {k: data.get(k, "") for k in ("first_name", "last_name", "company", "position", "phone", "region")}
        salt, pwd_hash = security.make_hash(data["password"])
        try:
            user_id = db.create_user(data["username"], salt, pwd_hash, "expert" if expert else "farmer", profile)
            db.record_consent(user_id, legal.LEGAL_VERSION)
        except Exception as err:
            return self._fail(f"Не удалось сохранить аккаунт: {err}")
        self.show_login()
        if expert:
            self._ok(f"Регистрация прошла успешно! Фирма «{profile['company']}» создана. "
                     "Теперь войдите в систему.")
        else:
            self._ok("Регистрация прошла успешно! Теперь войдите в систему. "
                     "Датчики появятся после приглашения эксперта.")


# ======================= ДАТЧИКИ =======================
class SensorsPage(ctk.CTkFrame):
    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.pack(fill="x")
        page_title(head, "Датчики").pack(side="left")
        self.picker = dropdown(head, [AVG_LABEL], command=self.app.on_pick_sensor, width=300)
        self.picker.pack(side="right")

        self.basis = label(self, "", 12, True, WARN)
        self.banner = glass(self)
        self.banner.pack(fill="x", pady=(18, 10))
        self.dot = ctk.CTkLabel(self.banner, text="●", font=font(26, True), text_color=MUTED)
        self.dot.pack(side="left", padx=(24, 14), pady=20)
        texts = ctk.CTkFrame(self.banner, fg_color="transparent")
        texts.pack(side="left", pady=18)
        self.banner_title = label(texts, "Ожидание данных", 17, True, TEXT)
        self.banner_title.pack(anchor="w")
        self.banner_sub = label(texts, "Подключите датчик или выберите другой")
        self.banner_sub.pack(anchor="w")

        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x")
        for i in range(3):
            row.grid_columnconfigure(i, weight=1)
        self.temp = self._card(row, 0, "Температура", "из 50 °C")
        self.hum = self._card(row, 1, "Влажность воздуха", "от 0 до 100 %")
        self.water = self._card(row, 2, "Влажность почвы (датчик воды)", "из 1000 (0 — сухо)")

        self.extras = ctk.CTkFrame(self, fg_color="transparent")
        self.extra_cards = {}
        for i, key in enumerate(EXTRA_INFO):
            self.extras.grid_columnconfigure(i, weight=1)
            box = glass(self.extras)
            box.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 10, 0))
            title = label(box, EXTRA_INFO[key][0], 12)
            title.pack(anchor="w", padx=18, pady=(14, 0))
            value = label(box, "--", 22, True, TEXT)
            value.pack(anchor="w", padx=18, pady=(2, 14))
            self.extra_cards[key] = (title, value)

        self.footer = ctk.CTkFrame(self, fg_color="transparent")
        self.footer.pack(fill="x", pady=(18, 0))
        self.proof = pill(self, "Открыть источники данных", self.app.open_sources, width=260, primary=False)
        self.coords = label(self.footer, "")
        self.coords.pack(side="left")
        self.updated = label(self.footer, "")
        self.updated.pack(side="left", padx=18)
        self.send_label = label(self.footer, "")
        self.send_label.pack(side="right")

    def _card(self, parent, col, name, scale):
        box = glass(parent)
        box.grid(row=0, column=col, sticky="nsew", padx=(0 if col == 0 else 12, 0))
        label(box, name, 15).pack(anchor="w", padx=24, pady=(20, 0))
        value = ctk.CTkLabel(box, text="--", font=font(44, True), text_color=TEXT)
        value.pack(anchor="w", padx=24, pady=(6, 0))
        label(box, scale, 12).pack(anchor="w", padx=24)
        bar = ctk.CTkProgressBar(box, height=8, corner_radius=4, progress_color=BLUE, fg_color="#D5E3F7")
        bar.set(0)
        bar.pack(fill="x", padx=24, pady=(16, 0))
        state = label(box, "ожидание", 13, True)
        state.pack(anchor="w", padx=24, pady=(10, 20))
        return value, bar, state

    @staticmethod
    def _put(widgets, text, progress, risk, risk_text, ok_text, muted=False):
        value, bar, state = widgets
        value.configure(text=text, text_color=MUTED if muted else TEXT)
        bar.set(max(0.0, min(1.0, progress)))
        if muted:
            state.configure(text="нет связи", text_color=DANGER)
        else:
            state.configure(text=risk_text if risk else ok_text, text_color=WARN if risk else OK)

    def _fill(self, cur, s, muted):
        self._put(self.temp, f"{cur['temperature']:.1f}", cur["temperature"] / 50,
                  cur["temperature"] >= float(s["temp_warn"]), "жарко", "норма", muted)
        self._put(self.hum, f"{cur['humidity']:.0f}", cur["humidity"] / 100,
                  cur["humidity"] <= float(s["hum_warn"]), "сухой воздух", "норма", muted)
        self._put(self.water, f"{cur['water']:.0f}", cur["water"] / WATER_MAX,
                  cur["water"] <= float(s["water_warn"]), "сухо", "влажно", muted)

    def render(self, view, send_info):
        options = view["options"]
        self.picker.configure(values=[t for t, _ in options])
        self.picker.set(next(t for t, sid in options if sid == view["selected"]))
        if view["bases"]:
            self.basis.configure(text=" · ".join(view["bases"]) + TEST_MARK)
            self.basis.pack(anchor="w", before=self.banner)
        else:
            self.basis.pack_forget()

        cur = view["current"]
        star = TEST_MARK if view["is_test"] else ""
        s = view["s"]
        if cur is None:
            self.dot.configure(text_color=MUTED)
            self.banner_title.configure(text="Нет данных")
            self.banner_sub.configure(text="Датчик ещё не передавал данные")
        elif cur["stale"]:
            self.dot.configure(text_color=DANGER)
            self.banner_title.configure(text=f"Нет связи с датчиком{star}")
            self.banner_sub.configure(text=f"Данные устарели: последние от {cur['last_at']}. "
                                           "Проверьте подключение Arduino.")
            self._fill(cur, s, muted=True)
        else:
            st = cur["status"]
            self.dot.configure(text_color=STATUS[st])
            self.banner_title.configure(text=f"Состояние: {STATUS_TEXT[st]}{star}")
            self.banner_sub.configure(text=STATUS_SUB[st] + (f" · {view['company']}" if view["company"] else ""))
            self._fill(cur, s, muted=False)

        show_extras = bool(view["bases"]) and cur is not None and any(
            cur.get(k) is not None for k in EXTRA_INFO)
        self.extras.pack_forget()
        self.proof.pack_forget()
        if show_extras:
            self.extras.pack(fill="x", pady=(14, 0), before=self.footer)
            for key, (title, value) in self.extra_cards.items():
                v = cur.get(key)
                title.configure(text=EXTRA_INFO[key][0] + star)
                if v is None:
                    value.configure(text="—", text_color=MUTED)
                else:
                    digits = 2 if key in ("salinity", "ph") else 0
                    value.configure(text=f"{v:.{digits}f} {EXTRA_INFO[key][1]}".strip(),
                                    text_color=MUTED if cur["stale"] else TEXT)
        if view["is_admin"] and view["bases"]:
            self.proof.pack(anchor="w", pady=(14, 0), before=self.footer)
        self.coords.configure(text=f"Координаты{star}: {view['coords']}" if view["coords"] else "")
        self.updated.configure(text=f"Обновлено {datetime.now():%H:%M:%S}")
        self.send_label.configure(text=send_info or "")


# ======================= ИСТОРИЯ =======================
class TimeView:
    """Общее окно по времени для всех графиков: одно движение мышью двигает все графики сразу."""

    def __init__(self):
        self.lo = None
        self.hi = None
        self.full = None

    def reset(self):
        self.lo = None
        self.hi = None


class TimeChart(ctk.CTkFrame):
    """График с временем внизу. Мышью двигаем влево-вправо, колесом меняем масштаб."""

    def __init__(self, master, title, color, view, on_move):
        super().__init__(master, fg_color="transparent")
        self.title_text = title
        self.color = color
        self.view = view
        self.on_move = on_move
        self.data = ([], [], None, None)
        self.fig = Figure(figsize=(9, 2.1), dpi=100, facecolor=GLASS)
        self.canvas = FigureCanvasTkAgg(self.fig, master=self)
        self.widget = self.canvas.get_tk_widget()
        self.widget.configure(height=210, cursor="fleur")
        self.widget.pack(fill="x", expand=True)
        self.widget.bind("<ButtonPress-1>", self._press)
        self.widget.bind("<B1-Motion>", self._drag)
        self.widget.bind("<MouseWheel>", self._wheel)
        self.widget.bind("<Button-4>", lambda e: self._zoom(0.8))
        self.widget.bind("<Button-5>", lambda e: self._zoom(1.25))
        self._x0 = None

    def draw(self, xs, ys, threshold=None, ylim=None):
        self.data = (xs, ys, threshold, ylim)
        self._paint()

    def _paint(self):
        xs, ys, threshold, ylim = self.data
        self.fig.clear()
        ax = self.fig.add_subplot(111)
        self.fig.subplots_adjust(left=0.08, right=0.98, top=0.84, bottom=0.24)
        ax.set_title(self.title_text, loc="left", fontsize=11, color=TEXT, fontweight="bold")
        pairs = [(x, y) for x, y in zip(xs, ys) if y is not None]
        if pairs:
            px, py = zip(*pairs)
            ax.plot(px, py, color=self.color, linewidth=2.2)
            ax.fill_between(px, py, color=self.color, alpha=0.10)
        if self.view.full:
            lo, hi = self._current()
            ax.set_xlim(lo, hi)
        if ylim:
            ax.set_ylim(*ylim)
        if threshold is not None:
            ax.axhline(threshold, color=DANGER, linestyle="--", linewidth=1)
        style_axes(ax)
        lo, hi = ax.get_xlim()
        fmt = "%H:%M" if hi - lo <= 1.5 else ("%d.%m %H:%M" if hi - lo <= 4 else "%d.%m")
        ax.xaxis.set_major_formatter(mdates.DateFormatter(fmt))
        self.canvas.draw_idle()

    def _current(self):
        if self.view.lo is None:
            return self.view.full
        return self.view.lo, self.view.hi

    def _press(self, event):
        self._x0 = event.x

    def _drag(self, event):
        if self._x0 is None or not self.view.full:
            return
        lo, hi = self._current()
        width = max(1, self.widget.winfo_width())
        shift = -(event.x - self._x0) / width * (hi - lo)
        self._x0 = event.x
        self.on_move(lo + shift, hi + shift)

    def _zoom(self, factor):
        if not self.view.full:
            return
        lo, hi = self._current()
        center = (lo + hi) / 2
        half = (hi - lo) / 2 * factor
        self.on_move(center - half, center + half)

    def _wheel(self, event):
        self._zoom(0.8 if event.delta > 0 else 1.25)


class HistoryPage(ctk.CTkFrame):
    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.view = TimeView()
        self.series = []
        self.charts = {}
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.pack(fill="x")
        page_title(head, "История").pack(side="left")
        self.picker = dropdown(head, [AVG_LABEL], command=self.app.on_pick_sensor, width=300)
        self.picker.pack(side="right", padx=(12, 0))
        self.range_var = ctk.StringVar(value="1 час")
        self._last_range = "1 час"
        segmented(head, list(RANGES), command=self.on_range, variable=self.range_var).pack(side="right")

        self.lock_hint = label(self, "", 12, True, WARN, wraplength=900, justify="left")
        tools = ctk.CTkFrame(self, fg_color="transparent")
        tools.pack(fill="x", pady=(12, 0))
        self.tools = tools
        pill(tools, "Сбросить вид", self.reset_view, width=140, primary=False).pack(side="left")
        pill(tools, "Экспорт в Excel", self.app.export_dialog, width=170, primary=False).pack(side="left", padx=10)
        if app.user["role"] in ("expert", "admin"):
            pill(tools, "Импорт из Excel", self.app.import_history, width=170, primary=False).pack(side="left")
        label(tools, "Мышью двигайте графики влево-вправо, колесом — масштаб", 12).pack(side="right")

        self.summary = ctk.CTkFrame(self, fg_color="transparent")
        self.summary.pack(fill="x", pady=(14, 10))
        self.empty = label(self, "", 13)
        self.charts_box = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.charts_box.pack(fill="both", expand=True)

    def _stat(self, col, text, value):
        box = glass(self.summary)
        box.grid(row=0, column=col, sticky="nsew", padx=(0 if col == 0 else 12, 0))
        label(box, text, 12).pack(anchor="w", padx=20, pady=(16, 0))
        label(box, value, 22, True, TEXT).pack(anchor="w", padx=20, pady=(2, 16))

    def _clamp(self, lo, hi):
        a, b = self.view.full
        span = min(max(hi - lo, 2 / 1440), b - a)
        lo = max(a, min(lo, b - span))
        return lo, lo + span

    def _moved(self, lo, hi):
        if not self.view.full:
            return
        a, b = self.view.full
        lo, hi = self._clamp(lo, hi)
        if lo <= a + 1e-9 and hi >= b - 1e-9:
            self.view.reset()
        else:
            self.view.lo, self.view.hi = lo, hi
        self._repaint()

    def reset_view(self):
        self.view.reset()
        self._repaint()

    def on_range(self, value):
        """Период истории. На бесплатном тарифе — не больше FREE_HISTORY_DAYS дней."""
        days = self.app.limits()["history_days"]
        if days is not None and RANGES[value] > days * 86400:
            self.range_var.set(self._last_range)
            self.lock_hint.configure(text="🔒 " + plans.locked_text(self.app.plan(), f"История больше {days} дней"))
            self.lock_hint.pack(anchor="w", pady=(8, 0), before=self.tools)
            return
        self.lock_hint.pack_forget()
        self._last_range = value
        self.view.reset()
        self.app.refresh_history()

    def refresh(self):
        days = self.app.limits()["history_days"]
        if days is not None and RANGES[self.range_var.get()] > days * 86400:
            self.range_var.set("24 часа")
            self._last_range = "24 часа"

    def _repaint(self):
        for chart, xs, ys, threshold, ylim in self.series:
            chart.draw(xs, ys, threshold, ylim)

    def render(self, points, show_extras, threshold_pct, suffix):
        for widget in self.summary.winfo_children():
            widget.destroy()
        for i in range(3):
            self.summary.grid_columnconfigure(i, weight=1)
        for chart in self.charts.values():
            chart.pack_forget()
        if not points:
            self.series = []
            self.empty.configure(text="Нет данных за выбранный период")
            self.empty.pack(anchor="w")
            return
        self.empty.pack_forget()

        temps = [p["temperature"] for p in points]
        hums = [p["humidity"] for p in points]
        waters = [p["water"] / WATER_MAX * 100 for p in points]
        self._stat(0, "Средняя температура" + suffix, f"{analytics.mean(temps):.1f} °C")
        self._stat(1, "Средняя влажность воздуха" + suffix, f"{analytics.mean(hums):.0f} %")
        self._stat(2, "Минимум влажности почвы" + suffix, f"{min(waters):.0f} %")

        xs = [mdates.date2num(datetime.fromtimestamp(p["ts"])) for p in points]
        lo, hi = min(xs), max(xs)
        if hi - lo < 1 / 1440:
            hi = lo + 1 / 1440
        self.view.full = (lo, hi)
        if self.view.lo is not None:
            self.view.lo, self.view.hi = self._clamp(self.view.lo, self.view.hi)

        specs = [("temperature", "Температура, °C", "#FF9F0A", None, None),
                 ("humidity", "Влажность воздуха, %", "#4F8FF7", None, (0, 100)),
                 ("water", "Влажность почвы, %", "#34C759", threshold_pct, (0, 100))]
        if show_extras:
            specs += [("salinity", "Засолённость, dS/m" + suffix, "#AF52DE", None, None),
                      ("ph", "Кислотность pH" + suffix, "#5AC8FA", None, None)]
        self.series = []
        for key, title, color, threshold, ylim in specs:
            chart = self.charts.get(key)
            if chart is None:
                chart = TimeChart(self.charts_box, title, color, self.view, self._moved)
                self.charts[key] = chart
            chart.title_text = title
            chart.pack(fill="x", pady=8)
            if key == "water":
                ys = [p["water"] / WATER_MAX * 100 for p in points]
            else:
                ys = [p.get(key) for p in points]
            self.series.append((chart, xs, ys, threshold, ylim))
        self._repaint()


# ======================= МОИ ДАТЧИКИ (эксперт и админ) =======================
class ManagePage(ctk.CTkFrame):
    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app
        page_title(self, "Мои датчики").pack(anchor="w")
        self.list = ctk.CTkScrollableFrame(self, fg_color="transparent", height=300)
        self.list.pack(fill="both", expand=True, pady=(12, 0))
        add = glass(self)
        add.pack(fill="x", pady=(14, 0))
        label(add, "Добавить датчик Arduino", 16, True, TEXT).pack(anchor="w", padx=24, pady=(14, 8))
        row = ctk.CTkFrame(add, fg_color="transparent")
        row.pack(anchor="w", padx=24, pady=(0, 16))
        self.new_name = entry(row, 220, placeholder_text="Название")
        self.new_name.grid(row=0, column=0, padx=(0, 10))
        self.new_port = dropdown(row, [""], width=150)
        self.new_port.grid(row=0, column=1, padx=(0, 10))
        self.new_lat = entry(row, 120, placeholder_text="широта")
        self.new_lat.grid(row=0, column=2, padx=(0, 10))
        self.new_lon = entry(row, 120, placeholder_text="долгота")
        self.new_lon.grid(row=0, column=3, padx=(0, 10))
        pill(row, "Добавить", self.add_sensor, width=130).grid(row=0, column=4)
        self.msg = label(self, "", 13)
        self.msg.pack(anchor="w", pady=(10, 0))

    def refresh(self):
        ports = available_ports() or [""]
        self.new_port.configure(values=ports)
        if not self.new_port.get() or self.new_port.get() not in ports:
            self.new_port.set(ports[0])
        for widget in self.list.winfo_children():
            widget.destroy()
        sensors = db.manageable_sensors(self.app.user)
        if not sensors:
            label(self.list, "Датчиков пока нет. Добавьте Arduino ниже.").pack(anchor="w", padx=12, pady=12)
        for sensor in sensors:
            self._row(sensor, ports)

    def _row(self, sensor, ports):
        box = glass(self.list)
        box.pack(fill="x", padx=4, pady=6)
        top = ctk.CTkFrame(box, fg_color="transparent")
        top.pack(fill="x", padx=20, pady=(14, 6))
        name = entry(top, 240)
        name.insert(0, sensor["name"])
        name.pack(side="left")
        port = dropdown(top, list(dict.fromkeys(ports + [sensor["port"] or ""])), width=150)
        port.set(sensor["port"] or "")
        port.pack(side="left", padx=10)
        enabled = toggle(top, "включён")
        enabled.select() if sensor["enabled"] else enabled.deselect()
        enabled.pack(side="left", padx=10)
        bottom = ctk.CTkFrame(box, fg_color="transparent")
        bottom.pack(fill="x", padx=20, pady=(0, 14))
        lat = entry(bottom, 130, placeholder_text="широта")
        lon = entry(bottom, 130, placeholder_text="долгота")
        if sensor["lat"] is not None:
            lat.insert(0, str(sensor["lat"]))
        if sensor["lon"] is not None:
            lon.insert(0, str(sensor["lon"]))
        lat.pack(side="left", padx=(0, 8))
        lon.pack(side="left", padx=(0, 16))
        sid = sensor["id"]

        def save():
            try:
                lat_v = float(lat.get().replace(",", ".")) if lat.get().strip() else None
                lon_v = float(lon.get().replace(",", ".")) if lon.get().strip() else None
            except ValueError:
                self.msg.configure(text="Координаты должны быть числами", text_color=DANGER)
                return
            db.update_sensor(sid, name.get().strip() or sensor["name"], port.get().strip(), lat_v, lon_v,
                             enabled.get() == 1, "")
            self.msg.configure(text=f"Сохранено: {name.get().strip()}", text_color=OK)
            self.app.restart_readers()

        def remove():
            db.delete_sensor(sid)
            self.app.restart_readers()
            self.refresh()

        pill(bottom, "Сохранить", save, width=120).pack(side="left", padx=(0, 8))
        pill(bottom, "Удалить", remove, width=110, primary=False, danger=True).pack(side="left")

    def add_sensor(self):
        name = self.new_name.get().strip()
        port = self.new_port.get().strip()
        if not name or not port:
            self.msg.configure(text="Укажите название и COM-порт", text_color=DANGER)
            return
        try:
            lat = float(self.new_lat.get().replace(",", ".")) if self.new_lat.get().strip() else None
            lon = float(self.new_lon.get().replace(",", ".")) if self.new_lon.get().strip() else None
        except ValueError:
            self.msg.configure(text="Координаты должны быть числами", text_color=DANGER)
            return
        db.add_real_sensor(self.app.user["id"], name, port, lat, lon)
        self.new_name.delete(0, "end")
        self.msg.configure(text=f"Датчик «{name}» добавлен на {port}", text_color=OK)
        self.app.restart_readers()
        self.refresh()


# ======================= ФЕРМЕРЫ (эксперт) =======================
class FarmersPage(ctk.CTkFrame):
    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app
        page_title(self, "Фермеры").pack(anchor="w")
        invite = glass(self)
        invite.pack(fill="x", pady=(14, 0))
        label(invite, "Пригласить фермера", 16, True, TEXT).pack(anchor="w", padx=24, pady=(14, 6))
        label(invite, "Введите логин, под которым фермер зарегистрировался в программе. "
                      "Название вашей фирмы он получит автоматически.", 12, False, MUTED,
              wraplength=800, justify="left").pack(anchor="w", padx=24)
        row = ctk.CTkFrame(invite, fg_color="transparent")
        row.pack(anchor="w", padx=24, pady=(10, 16))
        self.login = entry(row, 260, placeholder_text="Логин фермера")
        self.login.pack(side="left")
        pill(row, "Пригласить", self.invite, width=140).pack(side="left", padx=10)
        self.limit_label = label(invite, "", 13, True, MUTED)
        self.limit_label.pack(anchor="w", padx=24, pady=(0, 4))
        self.msg = label(invite, "", 13)
        self.msg.pack(anchor="w", padx=24, pady=(0, 14))
        self.list = ctk.CTkScrollableFrame(self, fg_color="transparent", height=320)
        self.list.pack(fill="both", expand=True, pady=(14, 0))

    def refresh(self):
        for widget in self.list.winfo_children():
            widget.destroy()
        farmers = db.farmers_of(self.app.user["id"])
        limit = self.app.limits()["farmers"]
        info = self.app.plan()
        if limit is None:
            self.limit_label.configure(text=f"Фермеров: {len(farmers)} · {info['title']}: без ограничений. "
                                            "Фермеры автоматически работают по вашей подписке.", text_color=OK)
        else:
            self.limit_label.configure(text=f"Фермеров: {len(farmers)} из {limit} на бесплатном тарифе. "
                                            "Больше — в подписке.",
                                       text_color=WARN if len(farmers) >= limit else MUTED)
        if not farmers:
            label(self.list, "Приглашённых фермеров пока нет.").pack(anchor="w", padx=12, pady=12)
        own = db.manageable_sensors(self.app.user)
        for farmer in farmers:
            box = glass(self.list)
            box.pack(fill="x", padx=4, pady=6)
            top = ctk.CTkFrame(box, fg_color="transparent")
            top.pack(fill="x", padx=20, pady=(14, 6))
            label(top, f"{farmer['first_name']} {farmer['last_name']}", 16, True, TEXT).pack(side="left")
            label(top, f"  ·  логин {farmer['username']}  ·  {farmer['company']}", 13).pack(side="left")
            pill(top, "Убрать", lambda fid=farmer["id"]: self.remove(fid), width=110,
                 primary=False, danger=True).pack(side="right")
            granted = db.granted_sensor_ids(farmer["id"])
            access = ctk.CTkFrame(box, fg_color="transparent")
            access.pack(anchor="w", padx=20, pady=(0, 14))
            if not own:
                label(access, "Сначала добавьте свои датчики.").pack(anchor="w")
            for sensor in own:
                switch_widget = toggle(access, sensor["name"])
                if sensor["id"] in granted:
                    switch_widget.select()
                switch_widget.configure(command=lambda fid=farmer["id"], sid=sensor["id"], w=switch_widget:
                                        db.set_access(sid, fid, w.get() == 1))
                switch_widget.pack(anchor="w", pady=4)

    def invite(self):
        ok, text = db.invite_farmer(self.login.get().strip(), self.app.user, self.app.limits()["farmers"])
        self.msg.configure(text=text, text_color=OK if ok else DANGER)
        if ok:
            self.login.delete(0, "end")
            self.refresh()

    def remove(self, farmer_id):
        db.remove_farmer(farmer_id, self.app.user["id"])
        self.refresh()


# ======================= TELEGRAM-БОТ (по токену) =======================
class NotifyPage(ctk.CTkFrame):
    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app
        page_title(self, "Telegram-бот").pack(anchor="w")
        scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        scroll.pack(fill="both", expand=True, pady=(12, 0))

        tg = glass(scroll)
        tg.pack(fill="x", pady=(0, 14))
        label(tg, "Telegram-бот", 17, True, TEXT).pack(anchor="w", padx=24, pady=(18, 4))
        label(tg, "1. Создайте бота у @BotFather и вставьте токен ниже.\n"
                  "2. Нажмите «Проверить токен» и «Включить».\n"
                  "3. Откройте бота в Telegram и напишите /start — после этого он будет присылать уведомления.\n"
                  "Команды бота: /turn_notifications (вкл/выкл, можно добавить on или off), /status, /help.",
              12, False, MUTED, wraplength=860, justify="left").pack(anchor="w", padx=24, pady=(0, 12))
        self.token = entry(tg, 540, placeholder_text="Токен бота", show="•")
        self.token.pack(anchor="w", padx=24, pady=(0, 10))
        row = ctk.CTkFrame(tg, fg_color="transparent")
        row.pack(anchor="w", padx=24, pady=(0, 10))
        pill(row, "Проверить токен", self.check, width=170, primary=False).pack(side="left")
        pill(row, "Включить", self.enable, width=130).pack(side="left", padx=10)
        pill(row, "Выключить", self.disable, width=130, primary=False).pack(side="left")
        self.msg = label(tg, "", 13)
        self.msg.pack(anchor="w", padx=24, pady=(0, 16))

        rules = glass(scroll)
        rules.pack(fill="x", pady=(0, 14))
        label(rules, "Какие события присылать", 17, True, TEXT).pack(anchor="w", padx=24, pady=(18, 10))
        self.rules = {}
        for key, text in [
            ("notify_drought", "Засуха и признаки засухи, возврат в норму"),
            ("notify_temp", "Очень высокая температура (на 5 °C выше порога)"),
            ("notify_offline", "Датчик не передаёт данные дольше 5 минут"),
            ("notify_test", "Тестовые показатели: засолённость и pH (с пометкой *)"),
        ]:
            sw = toggle(rules, text)
            sw.pack(anchor="w", padx=24, pady=6)
            self.rules[key] = sw
        cool = ctk.CTkFrame(rules, fg_color="transparent")
        cool.pack(anchor="w", padx=24, pady=(10, 18))
        label(cool, "Не повторять одно и то же чаще, чем раз в (сек):").pack(side="left")
        self.cooldown = entry(cool, 100)
        self.cooldown.pack(side="left", padx=10)
        pill(cool, "Сохранить правила", self.save_rules, width=180, primary=False).pack(side="left")

        log = glass(scroll)
        log.pack(fill="x", pady=(0, 14))
        label(log, "Отправленные в Telegram", 17, True, TEXT).pack(anchor="w", padx=24, pady=(18, 8))
        self.subs = label(log, "")
        self.subs.pack(anchor="w", padx=24)
        self.log_box = ctk.CTkTextbox(log, height=220, fg_color="#FFFFFF", text_color=TEXT, font=font(13),
                                      corner_radius=16)
        self.log_box.pack(fill="x", padx=24, pady=(8, 18))

    def refresh(self):
        s = db.get_settings()
        self.token.delete(0, "end")
        self.token.insert(0, s["tg_token"])
        for key, sw in self.rules.items():
            sw.select() if s[key] == "1" else sw.deselect()
        self.cooldown.delete(0, "end")
        self.cooldown.insert(0, s["notify_cooldown"])
        self.subs.configure(text=f"Подписчиков на уведомления: {len(db.list_subscribers())}")
        lines = [f"{'✓' if r['ok'] else '✗'} {datetime.fromtimestamp(r['ts']):%d.%m %H:%M:%S}  {r['text']}"
                 for r in db.list_notifications()]
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.insert("end", "\n".join(lines) if lines else "Уведомлений пока не было.")
        self.log_box.configure(state="disabled")

    def check(self):
        ok, text = telegram.check_token(self.token.get().strip())
        self.msg.configure(text=f"Бот {text} найден" if ok else text, text_color=OK if ok else DANGER)

    def enable(self):
        token = self.token.get().strip()
        ok, text = telegram.check_token(token)
        if not ok:
            self.msg.configure(text=text, text_color=DANGER)
            return
        db.set_settings({"tg_token": token, "tg_enabled": "1"})
        self.app.sync_bot()
        self.msg.configure(text=f"Включено. Напишите боту {text} команду /start", text_color=OK)

    def disable(self):
        db.set_settings({"tg_enabled": "0"})
        self.app.sync_bot()
        self.msg.configure(text="Уведомления выключены", text_color=MUTED)

    def save_rules(self):
        values = {key: "1" if sw.get() == 1 else "0" for key, sw in self.rules.items()}
        try:
            values["notify_cooldown"] = str(max(0, int(self.cooldown.get())))
        except ValueError:
            self.msg.configure(text="Пауза должна быть целым числом секунд", text_color=DANGER)
            return
        db.set_settings(values)
        self.msg.configure(text="Правила сохранены", text_color=OK)


# ======================= НАСТРОЙКИ (эксперт и админ) =======================
class SettingsPage(ctk.CTkFrame):
    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app
        page_title(self, "Настройки").pack(anchor="w")
        scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        scroll.pack(fill="both", expand=True, pady=(12, 0))
        self.e = {}

        sec = self._section(scroll, "Пороги засухи")
        self._field(sec, "Температура, выше которой риск (°C)", "temp_warn")
        self._field(sec, "Влажность воздуха, ниже которой риск (%)", "hum_warn")
        self._field(sec, "Влажность почвы, ниже которой сухо (0–1000)", "water_warn")

        self.integ_lock = lock_card(
            scroll, "Подключение к сайту и отправка данных",
            "Подключение к сайту «ПочваМонитор» по коду, отправка показаний по API и в JSON-файл, а также "
            "аналитика сайта в программе доступны в подписке. Попробуйте бесплатно 7 дней.",
            "Подробнее о подписке", lambda: self.app.show_page("plan"))
        self.integ_box = ctk.CTkFrame(scroll, fg_color="transparent")
        self.integ_box.pack(fill="x")
        sec = self._section(self.integ_box, "Подключение к сайту")
        self._field(sec, "Адрес сайта", "site_url")
        label(sec, "Одноразовый код с сайта (Ключи API → Создать код подключения)").pack(anchor="w", padx=24)
        row = ctk.CTkFrame(sec, fg_color="transparent")
        row.pack(anchor="w", padx=24, pady=(6, 10))
        self.code = entry(row, 240, placeholder_text="XXXX-XXXX")
        self.code.pack(side="left")
        pill(row, "Подключить", self.pair, width=150).pack(side="left", padx=10)
        self.key_info = label(sec, "")
        self.key_info.pack(anchor="w", padx=24)
        self.pair_msg = label(sec, "")
        self.pair_msg.pack(anchor="w", padx=24, pady=(4, 16))

        sec = self._section(self.integ_box, "Отправка данных на сайт")
        self.mode = ctk.CTkSegmentedButton(sec, values=list(MODE_LABELS.values()), font=font(13), height=36,
                                           corner_radius=18, fg_color="#FFFFFF", selected_color=BLUE,
                                           selected_hover_color=BLUE_HOVER, unselected_color="#FFFFFF",
                                           unselected_hover_color="#EAF2FF", text_color=TEXT)
        self.mode.pack(anchor="w", padx=24, pady=(4, 14))
        self._field(sec, "Путь к JSON-файлу (если выбран JSON-файл)", "file_path")
        self._field(sec, "Интервал отправки, сек (от 1 до 3600)", "send_interval")

        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.pack(fill="x", pady=(14, 0))
        pill(bottom, "Сохранить", self.save, width=150).pack(side="left")
        self.msg = label(bottom, "")
        self.msg.pack(side="left", padx=12)

    def _section(self, parent, text):
        wrap = glass(parent)
        wrap.pack(fill="x", pady=(0, 14))
        label(wrap, text, 17, True, TEXT).pack(anchor="w", padx=24, pady=(18, 10))
        return wrap

    def _field(self, parent, text, key):
        label(parent, text).pack(anchor="w", padx=24)
        widget = entry(parent, 420)
        widget.pack(anchor="w", padx=24, pady=(6, 14))
        self.e[key] = widget

    def refresh(self):
        s = db.get_settings()
        for key, widget in self.e.items():
            widget.delete(0, "end")
            widget.insert(0, s[key])
        self.mode.set(MODE_LABELS.get(s["send_mode"], "Выкл"))
        self._show_key(s["api_key"])
        self.integ_lock.pack_forget()
        self.integ_box.pack_forget()
        if self.app.limits()["integrations"]:
            self.integ_box.pack(fill="x")
        else:
            self.integ_lock.pack(fill="x", pady=(0, 14))

    @staticmethod
    def _mask(key):
        return (f"Подключено к сайту: {key[:7]}…{key[-4:]}. Отключить можно в разделе «Устройства»."
                if key else "Программа ещё не подключена к сайту")

    def _show_key(self, key):
        self.key_info.configure(text=self._mask(key), text_color=OK if key else MUTED)

    def pair(self):
        code = self.code.get().strip()
        site = self.e["site_url"].get().strip()
        if not code or not site:
            self.pair_msg.configure(text="Введите адрес сайта и код", text_color=DANGER)
            return
        if not self.app.limits()["integrations"]:
            self.pair_msg.configure(text=plans.locked_text(self.app.plan(), "Подключение к сайту"), text_color=DANGER)
            return
        name = f"Программа ПК ({socket.gethostname()})"
        self.pair_msg.configure(text="Подключаю...", text_color=MUTED)
        threading.Thread(target=lambda: self.app.queue.put(("pair", site, *site_client.pair_full(site, code, name))),
                         daemon=True).start()

    def on_pair_result(self, site, key, brand, error):
        if key:
            db.set_settings({"api_key": key, "site_url": site, "site_brand": brand or "ПочваМонитор X Drought",
                             "site_owner": str(self.app.user["id"]),
                             "send_url": site.rstrip("/") + "/api/sensors/readings", "send_mode": "api"})
            self.code.delete(0, "end")
            self.pair_msg.configure(text="Готово: программа подключена к сайту, отправка включена. "
                                         "Аналитика сайта — в разделе «Аналитика».", text_color=OK)
            self.mode.set(MODE_LABELS["api"])
            self._show_key(key)
            self.app.on_site_connected()
        else:
            self.pair_msg.configure(text=f"Не удалось подключиться: {error}", text_color=DANGER)

    def save(self):
        try:
            values = {
                "temp_warn": str(float(self.e["temp_warn"].get().replace(",", "."))),
                "hum_warn": str(float(self.e["hum_warn"].get().replace(",", "."))),
                "water_warn": str(float(self.e["water_warn"].get().replace(",", "."))),
                "site_url": self.e["site_url"].get().strip(),
                "file_path": self.e["file_path"].get().strip(),
                "send_interval": str(max(1, min(3600, int(self.e["send_interval"].get())))),
                "send_mode": MODE_KEYS.get(self.mode.get(), "off"),
            }
        except ValueError:
            self.msg.configure(text="Проверьте числа в порогах и интервале", text_color=DANGER)
            return
        if not self.app.limits()["integrations"]:
            values["send_mode"] = "off"
        values["send_url"] = values["site_url"].rstrip("/") + "/api/sensors/readings"
        db.set_settings(values)
        self.msg.configure(text="Сохранено", text_color=OK)


# ======================= ИСТОРИЯ УВЕДОМЛЕНИЙ (все) =======================
class EventsPage(ctk.CTkFrame):
    """Центр уведомлений: все всплывающие уведомления программы с фильтром и отметкой «прочитано».
    Фермер видит только уведомления по своим датчикам и общие."""

    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.pack(fill="x")
        page_title(head, "Уведомления").pack(side="left")
        pill(head, "Очистить", self.clear, width=120, primary=False, danger=True).pack(side="right")
        pill(head, "Прочитать все", self.read_all, width=150, primary=False).pack(side="right", padx=10)

        tools = ctk.CTkFrame(self, fg_color="transparent")
        tools.pack(fill="x", pady=(14, 0))
        self.kind_var = ctk.StringVar(value="Все")
        segmented(tools, list(EVENT_KINDS), command=lambda _: self.refresh(), variable=self.kind_var).pack(side="left")
        self.sound = toggle(tools, "Звук", self.save_prefs)
        self.sound.pack(side="right")
        self.popups = toggle(tools, "Всплывающие уведомления", self.save_prefs)
        self.popups.pack(side="right", padx=16)
        self.count = label(self, "", 12)
        self.count.pack(anchor="w", pady=(10, 0))
        self.list = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.list.pack(fill="both", expand=True, pady=(8, 0))

    def refresh(self):
        s = db.get_settings()
        self.popups.select() if s.get("popups", "1") == "1" else self.popups.deselect()
        self.sound.select() if s.get("popup_sound", "1") == "1" else self.sound.deselect()
        clear(self.list)
        ids = self.app.event_scope()
        rows = db.list_events(300, ids, EVENT_KINDS[self.kind_var.get()])
        unread = db.unread_events(ids)
        self.count.configure(text=f"Показано: {len(rows)} · непрочитанных: {unread}")
        if not rows:
            label(self.list, "Уведомлений пока нет. Здесь сохраняются все уведомления программы: засуха, "
                             "связь с датчиками, сайт и подписка.", wraplength=800, justify="left"
                  ).pack(anchor="w", padx=12, pady=12)
        for row in rows:
            box = glass(self.list)
            box.pack(fill="x", padx=4, pady=4)
            color = OK if row["level"] == "ok" else DANGER
            icon = EVENT_ICON.get(row["kind"], "ℹ")
            new = "" if row["is_read"] else "  •  новое"
            label(box, f"{icon}  {datetime.fromtimestamp(row['ts']):%d.%m %H:%M:%S}{new}", 12, True, color
                  ).pack(side="left", padx=18, pady=12)
            label(box, row["text"], 14, False, TEXT, wraplength=700, justify="left").pack(side="left", padx=8)
        # открыли список — значит, увидели
        if unread:
            db.mark_events_read(ids)
            self.app.update_badge()

    def save_prefs(self):
        db.set_settings({"popups": "1" if self.popups.get() == 1 else "0",
                         "popup_sound": "1" if self.sound.get() == 1 else "0"})

    def read_all(self):
        db.mark_events_read(self.app.event_scope())
        self.app.update_badge()
        self.refresh()

    def clear(self):
        db.clear_events(self.app.event_scope())
        self.app.update_badge()
        self.refresh()


# ======================= АККАУНТ (все) =======================
class AccountPage(ctk.CTkFrame):
    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app
        page_title(self, "Аккаунт").pack(anchor="w")
        self.info = label(self, "", 15, False, TEXT, wraplength=900, justify="left")
        self.info.pack(anchor="w", pady=(12, 16))
        box = glass(self)
        box.pack(fill="x")
        label(box, "Смена пароля", 17, True, TEXT).pack(anchor="w", padx=24, pady=(18, 10))
        self.current = entry(box, 320, placeholder_text="Текущий пароль", show="•")
        self.current.pack(anchor="w", padx=24, pady=6)
        self.new = entry(box, 320, placeholder_text="Новый пароль (от 6 символов)", show="•")
        self.new.pack(anchor="w", padx=24, pady=6)
        self.repeat = entry(box, 320, placeholder_text="Повторите новый пароль", show="•")
        self.repeat.pack(anchor="w", padx=24, pady=6)
        row = ctk.CTkFrame(box, fg_color="transparent")
        row.pack(anchor="w", padx=24, pady=(10, 18))
        pill(row, "Сменить пароль", self.change, width=180).pack(side="left")
        self.msg = label(row, "")
        self.msg.pack(side="left", padx=12)

    def refresh(self):
        u = db.get_user_by_id(self.app.user["id"])
        lines = [f"{u['first_name']} {u['last_name']}".strip() + f"   ·   {ROLE_LABEL[u['role']]}",
                 f"Логин: {u['username']}"]
        if u["role"] == "expert":
            lines.append(f"Фирма: {u['company']}" + (f"   ·   Должность: {u['position']}" if u["position"] else ""))
        if u["role"] == "farmer":
            if u["invited_by"]:
                expert = db.get_user_by_id(u["invited_by"])
                lines.append(f"Фирма: {u['company']}   ·   Эксперт: {expert['first_name']} {expert['last_name']}")
            else:
                lines.append("Вы ещё не приглашены экспертом. Датчики появятся после приглашения.")
            if u["region"]:
                lines.append(f"Регион: {u['region']}")
        if u["phone"]:
            lines.append(f"Телефон: {u['phone']}")
        lines.append("Тариф: " + plans.status_line(self.app.plan()))
        self.info.configure(text="\n".join(lines))

    def change(self):
        user = db.get_user_by_id(self.app.user["id"])
        if not security.check_password(self.current.get(), user["salt"], user["password_hash"]):
            self.msg.configure(text="Текущий пароль неверный", text_color=DANGER)
            return
        if len(self.new.get()) < 6 or self.new.get() != self.repeat.get():
            self.msg.configure(text="Новый пароль от 6 символов, повтор должен совпадать", text_color=DANGER)
            return
        salt, pwd_hash = security.make_hash(self.new.get())
        db.update_password(user["id"], salt, pwd_hash)
        for widget in (self.current, self.new, self.repeat):
            widget.delete(0, "end")
        self.msg.configure(text="Пароль изменён", text_color=OK)


# ======================= ПОДПИСКА (эксперт; админ — для просмотра) =======================
class SubscriptionPage(ctk.CTkFrame):
    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app
        page_title(self, "Подписка").pack(anchor="w")
        scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        scroll.pack(fill="both", expand=True, pady=(12, 0))

        self.card = glass(scroll)
        self.card.pack(fill="x", pady=(0, 14))
        self.plan_title = label(self.card, "", 22, True, TEXT)
        self.plan_title.pack(anchor="w", padx=24, pady=(20, 2))
        self.plan_sub = label(self.card, "", 13, False, MUTED, wraplength=860, justify="left")
        self.plan_sub.pack(anchor="w", padx=24)
        self.buttons = ctk.CTkFrame(self.card, fg_color="transparent")
        self.buttons.pack(anchor="w", padx=24, pady=(14, 6))
        self.msg = label(self.card, "", 13, False, MUTED, wraplength=860, justify="left")
        self.msg.pack(anchor="w", padx=24, pady=(0, 18))

        table = glass(scroll)
        table.pack(fill="x", pady=(0, 14))
        label(table, "Чем подписка отличается от бесплатного тарифа", 17, True, TEXT).pack(
            anchor="w", padx=24, pady=(18, 10))
        grid = ctk.CTkFrame(table, fg_color="transparent")
        grid.pack(fill="x", padx=24, pady=(0, 18))
        grid.grid_columnconfigure(0, weight=3)
        grid.grid_columnconfigure(1, weight=2)
        grid.grid_columnconfigure(2, weight=2)
        for col, text in enumerate(("Возможность", "Бесплатно", "Подписка Pro")):
            label(grid, text, 13, True, BLUE if col == 2 else TEXT).grid(row=0, column=col, sticky="w", pady=(0, 6))
        for i, (what, free, pro) in enumerate(plans.COMPARISON, start=1):
            bg = GLASS_SOFT if i % 2 else "transparent"
            cells = [what, free, pro]
            for col, text in enumerate(cells):
                cell = ctk.CTkFrame(grid, fg_color=bg, corner_radius=0)
                cell.grid(row=i, column=col, sticky="nsew")
                color = OK if text.startswith("✓") else (MUTED if text == "—" else TEXT)
                label(cell, text, 13, col == 2, color, wraplength=380 if col == 0 else 240,
                      justify="left").pack(anchor="w", padx=10, pady=7)

        why = glass(scroll)
        why.pack(fill="x")
        label(why, "Как это работает", 17, True, TEXT).pack(anchor="w", padx=24, pady=(18, 6))
        label(why, f"• Пробный период — {plans.TRIAL_DAYS} дней со всеми функциями подписки, один раз на аккаунт "
                   "эксперта. После окончания программа сама вернётся на бесплатный тариф, данные не удаляются.\n"
                   "• Фермеры, которых вы пригласили, автоматически получают ваш тариф. Им ничего покупать не нужно.\n"
                   "• История хранится 90 дней на любом тарифе: после оформления подписки старые данные станут доступны.\n"
                   "• Оплата пока не подключена: кнопка «Оформить подписку» ничего не списывает.",
              13, False, MUTED, wraplength=860, justify="left").pack(anchor="w", padx=24, pady=(0, 18))

    def refresh(self):
        info = self.app.plan()
        lim = plans.limits(info)
        self.plan_title.configure(text=f"Ваш тариф: {info['title']}",
                                  text_color=OK if info["paid"] else TEXT)
        sub = [plans.status_line(info)]
        if self.app.user["role"] == "expert":
            count = len(db.farmers_of(self.app.user["id"]))
            sub.append(f"Фермеров: {count}" + (f" из {lim['farmers']}" if lim["farmers"] else " (без ограничений)"))
        sub.append(f"История: {'за любой период' if lim['history_days'] is None else 'за ' + str(lim['history_days']) + ' дней'}"
                   f" · экспорт в Excel: за {lim['export_days']} дней"
                   f" · сайт и API: {'доступно' if lim['integrations'] else 'недоступно'}")
        self.plan_sub.configure(text="\n".join(sub))
        clear(self.buttons)
        if self.app.user["role"] == "expert":
            if info["trial_available"]:
                pill(self.buttons, f"Попробовать {plans.TRIAL_DAYS} дней бесплатно", self.start_trial,
                     width=280).pack(side="left", padx=(0, 10))
            if info["code"] != "pro":
                pill(self.buttons, "Оформить подписку", self.checkout, width=220,
                     primary=not info["trial_available"]).pack(side="left")
        self.msg.configure(text=f"Стоимость: {plans.PRICE_TEXT}." if info["code"] != "pro" else "")

    def start_trial(self):
        if db.start_trial(self.app.user["id"], plans.TRIAL_DAYS):
            self.app.reload_user()
            self.app.notify(f"Пробный период включён на {plans.TRIAL_DAYS} дней: доступны все функции подписки.",
                            True, kind="system")
        self.refresh()

    def checkout(self):
        ok, text = payments.checkout(self.app.user)
        self.msg.configure(text=text, text_color=OK if ok else WARN)


# ======================= УСТРОЙСТВА (все) =======================
class DevicesPage(ctk.CTkFrame):
    """Что подключено к аккаунту: этот компьютер, другие программы на сайте, датчики Arduino."""

    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.pack(fill="x")
        page_title(head, "Устройства").pack(side="left")
        pill(head, "Обновить", self.reload_site, width=130, primary=False).pack(side="right")
        self.scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.scroll.pack(fill="both", expand=True, pady=(12, 0))
        self.site_devices = None
        self.site_error = ""
        self.msg = label(self, "", 13)
        self.msg.pack(anchor="w", pady=(8, 0))

    def can_manage(self):
        return self.app.user["role"] in ("expert", "admin")

    def refresh(self):
        clear(self.scroll)
        s = db.get_settings()
        connected = bool(s["api_key"])
        # --- этот компьютер
        box = glass(self.scroll)
        box.pack(fill="x", pady=(0, 14))
        label(box, f"Этот компьютер · {socket.gethostname()}", 17, True, TEXT).pack(anchor="w", padx=24, pady=(18, 4))
        if connected:
            owner = f" · аккаунт сайта: {s['site_owner_name']}" if s.get("site_owner_name") else ""
            label(box, f"Подключён к сайту {s['site_url']}{owner}", 13, False, OK).pack(anchor="w", padx=24)
            if self.can_manage():
                pill(box, "Отключить от сайта", self.disconnect_self, width=200, primary=False,
                     danger=True).pack(anchor="w", padx=24, pady=(10, 18))
            else:
                label(box, "").pack(pady=4)
        else:
            label(box, "Не подключён к сайту. Подключение — в «Настройках» (функция подписки).", 13
                  ).pack(anchor="w", padx=24, pady=(0, 18))
        # --- программы на аккаунте сайта
        if connected:
            box = glass(self.scroll)
            box.pack(fill="x", pady=(0, 14))
            label(box, "Программы, подключённые к аккаунту на сайте", 17, True, TEXT).pack(
                anchor="w", padx=24, pady=(18, 4))
            label(box, "Отключённое устройство больше не отправляет данные и не получает аналитику. "
                       "Чтобы подключить его снова, нужен новый код с сайта.", 12, False, MUTED,
                  wraplength=860, justify="left").pack(anchor="w", padx=24, pady=(0, 8))
            if self.site_devices is None:
                label(box, self.site_error or "Загружаю список с сайта…", 13, False,
                      DANGER if self.site_error else MUTED).pack(anchor="w", padx=24, pady=(0, 18))
                if not self.site_error:
                    self.reload_site()
            else:
                for d in self.site_devices:
                    self._site_row(box, d)
                label(box, "").pack(pady=4)
        # --- датчики
        box = glass(self.scroll)
        box.pack(fill="x", pady=(0, 14))
        label(box, "Датчики Arduino", 17, True, TEXT).pack(anchor="w", padx=24, pady=(18, 4))
        sensors = (db.manageable_sensors(self.app.user) if self.can_manage()
                   else [x for x in self.app._visible() if x["kind"] == "real"])
        if not sensors:
            label(box, "Датчиков нет." if self.can_manage() else "Эксперт ещё не открыл вам доступ к датчикам.",
                  13).pack(anchor="w", padx=24, pady=(0, 18))
        now = time.time()
        for sensor in sensors:
            row = ctk.CTkFrame(box, fg_color=GLASS_SOFT, corner_radius=16)
            row.pack(fill="x", padx=20, pady=4)
            fresh = self.app.latest_real.get(sensor["id"])
            if not sensor["enabled"]:
                state, color = "деактивирован", MUTED
            elif fresh and now - fresh[1] <= FRESH_SECONDS:
                state, color = "на связи", OK
            else:
                state, color = "нет связи", DANGER
            seen = (datetime.fromtimestamp(sensor["last_seen"]).strftime("%d.%m %H:%M")
                    if sensor["last_seen"] else "никогда")
            label(row, f"● {sensor['name']}", 14, True, color).pack(side="left", padx=14, pady=10)
            label(row, f"{sensor['port'] or 'порт не задан'} · {state} · последние данные: {seen}", 12
                  ).pack(side="left", padx=6)
            if self.can_manage():
                text = "Активировать" if not sensor["enabled"] else "Деактивировать"
                pill(row, text, lambda x=sensor: self.toggle_sensor(x), width=150, primary=not sensor["enabled"],
                     danger=bool(sensor["enabled"])).pack(side="right", padx=10, pady=6)
        label(box, "").pack(pady=4)

    def _site_row(self, box, d):
        row = ctk.CTkFrame(box, fg_color=GLASS_SOFT, corner_radius=16)
        row.pack(fill="x", padx=20, pady=4)
        color = OK if d["active"] else MUTED
        name = d["name"] + ("  (этот компьютер)" if d["current"] else "")
        label(row, f"● {name}", 14, True, color).pack(side="left", padx=14, pady=10)
        info = (f"подключено {fmt_ts(d['created_at'])} · активность {fmt_ts(d['last_used'])} · "
                f"датчиков: {d['sensors']} · {'активно' if d['active'] else 'отключено ' + fmt_ts(d['revoked_at'])}")
        label(row, info, 12).pack(side="left", padx=6)
        if d["active"] and self.can_manage():
            pill(row, "Деактивировать", lambda: self.revoke(d), width=150, primary=False,
                 danger=True).pack(side="right", padx=10, pady=6)

    def reload_site(self):
        s = db.get_settings()
        if not s["api_key"]:
            self.refresh()
            return
        self.site_devices, self.site_error = None, ""

        def work():
            try:
                result = ("ok", site_client.devices(s["site_url"], s["api_key"]))
            except site_client.Revoked as err:
                result = ("revoked", str(err))
            except ConnectionError as err:
                result = ("error", str(err))
            self.app.queue.put(("devices", *result))

        threading.Thread(target=work, daemon=True).start()

    def on_devices(self, status, payload):
        if status == "ok":
            self.site_devices, self.site_error = payload, ""
        elif status == "revoked":
            self.app.on_revoked()
            return
        else:
            self.site_devices, self.site_error = None, payload
        if self.app.current == "devices":
            self.refresh()

    def revoke(self, device):
        s = db.get_settings()
        self.msg.configure(text=f"Отключаю «{device['name']}»…", text_color=MUTED)

        def work():
            try:
                site_client.revoke(s["site_url"], s["api_key"], device["id"])
                result = ("revoked_ok", device)
            except site_client.Revoked as err:
                result = ("revoked", str(err))
            except ConnectionError as err:
                result = ("error", str(err))
            self.app.queue.put(("device_revoke", *result))

        threading.Thread(target=work, daemon=True).start()

    def on_revoke(self, status, payload):
        if status == "revoked_ok":
            if payload["current"]:
                self.app.disconnect_site("Этот компьютер отключён от аккаунта на сайте.")
            else:
                self.app.notify(f"Устройство «{payload['name']}» отключено от аккаунта на сайте.", True)
            self.reload_site()
        elif status == "revoked":
            self.app.on_revoked()
        else:
            self.msg.configure(text=payload, text_color=DANGER)

    def disconnect_self(self):
        s = db.get_settings()
        me = next((d for d in (self.site_devices or []) if d["current"]), None)
        if me:
            self.revoke(me)
        else:
            # сайт недоступен — хотя бы забываем ключ на этом компьютере
            self.app.disconnect_site("Этот компьютер отключён от сайта (ключ удалён на компьютере). "
                                     "Если сайт был недоступен, отключите устройство и на сайте.")
            self.refresh()

    def toggle_sensor(self, sensor):
        db.update_sensor(sensor["id"], sensor["name"], sensor["port"], sensor["lat"], sensor["lon"],
                         not sensor["enabled"], sensor["profile"])
        self.app.restart_readers()
        self.app.notify(f"Датчик «{sensor['name']}» {'активирован' if not sensor['enabled'] else 'деактивирован'}.",
                        True, kind="link", sensor_id=sensor["id"])
        self.refresh()


# ======================= АНАЛИТИКА С САЙТА (все) =======================
class AnalyticsPage(ctk.CTkFrame):
    """Аналитика по ВАШИМ датчикам. Программа отправляет показания на сайт «ПочваМонитор», сайт считает
    по ним прогноз и возвращает результат сюда: погода по координатам датчика, прогноз влажности почвы
    и засоления, что посадить и советы по его pH/засолённости/влажности, обеспеченность N, P, K.
    Если какого-то датчика нет, прогноз строится по остальным, а программа сообщает, чего не хватает.
    Без сети показываются последние полученные данные."""

    QUALITY = {"full": ("Полные данные", OK), "partial": ("Неполные данные", WARN),
               "insufficient": ("Недостаточно данных", DANGER)}

    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.pack(fill="x")
        page_title(head, "Аналитика").pack(side="left")
        self.brand = label(head, "", 14, True, BLUE)
        self.brand.pack(side="left", padx=14, pady=(10, 0))
        self.refresh_btn = pill(head, "Обновить", lambda: self.app.load_analytics(force=True), width=130,
                                primary=False)
        self.refresh_btn.pack(side="right")
        self.picker = dropdown(head, ["—"], command=self._pick, width=300)
        self.picker.pack(side="right", padx=10)
        self.status = label(self, "", 12)
        self.status.pack(anchor="w", pady=(6, 0))
        self.scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.scroll.pack(fill="both", expand=True, pady=(8, 0))
        self.chart = None
        self._choices = {}

    # ----- состояние: заблокировано / не подключено / данные
    def refresh(self):
        clear(self.scroll)
        self.chart = None
        state = self.app.analytics_state()
        self.brand.configure(text=self.app.brand_name() if state == "ready" else "")
        role = self.app.user["role"]
        if state == "locked":
            self.picker.pack_forget()
            lock_card(self.scroll, "Аналитика по датчикам",
                      plans.locked_text(self.app.plan(), "Прогноз по вашим датчикам (через сайт)"),
                      "Подробнее о подписке" if role == "expert" else None,
                      (lambda: self.app.show_page("plan")) if role == "expert" else None).pack(fill="x")
            self.status.configure(text="")
            return
        if state == "offline":
            self.picker.pack_forget()
            text = ("Подключите программу к сайту «ПочваМонитор»: «Настройки → Подключение к сайту». Программа "
                    "будет отправлять показания ваших датчиков на сайт, сайт рассчитает по ним прогноз погоды "
                    "в месте датчика, прогноз влажности почвы, что посадить и советы — и вернёт сюда."
                    ) if role != "farmer" else (
                    "Программа на этом компьютере ещё не подключена к сайту. Попросите эксперта подключить её.")
            lock_card(self.scroll, "Нет подключения к сайту", text,
                      "Открыть настройки" if role != "farmer" else None,
                      (lambda: self.app.show_page("settings")) if role != "farmer" else None).pack(fill="x")
            self.status.configure(text="")
            return
        self.picker.pack(side="right", padx=10, before=self.refresh_btn)
        data = self.app.analytics
        if data.get("sensors") is None:
            self.status.configure(text=self.app.analytics_error or "Загружаю аналитику с сайта…",
                                  text_color=DANGER if self.app.analytics_error else MUTED)
            if not self.app.analytics_loading:
                self.app.load_analytics()
            return
        when = data.get("fetched_at")
        err = f" · {self.app.analytics_error}" if self.app.analytics_error else ""
        self.status.configure(text=f"Рассчитано сайтом по показаниям ваших датчиков, {datetime.fromtimestamp(when):%d.%m %H:%M}"
                                   + err, text_color=WARN if err else MUTED)
        self._fill_picker(data)
        if not self._choices:
            self._no_sensors()
        elif data.get("detail"):
            self._detail(data["detail"])
        elif data.get("detail_error"):
            lock_card(self.scroll, "Нет данных по датчику", data["detail_error"]).pack(fill="x")
        elif self.app.analytics_loading:
            label(self.scroll, "Загружаю аналитику датчика…").pack(anchor="w", pady=10)
        self.app.load_analytics()   # обновит сам, если данные старше 10 минут

    def _fill_picker(self, data):
        """В списке — датчики, которые видит пользователь. Если сайт по датчику данных не получал, это видно."""
        on_site = {s["id"]: s for s in data["sensors"]}
        self._choices = {}
        for v in self.app.analytics_sensors():
            site = on_site.get(v["id"])
            mark = TEST_MARK if v["kind"] == "test" else ""
            title = f"{v['name']}{mark}" + ("" if site else " — нет данных на сайте")
            self._choices[title] = v["id"]
        values = list(self._choices) or ["Нет датчиков"]
        self.picker.configure(values=values)
        current = data.get("sensor_id")
        title = next((t for t, sid in self._choices.items() if sid == current), values[0])
        self.picker.set(title)

    def _pick(self, title):
        sid = self._choices.get(title)
        if sid is None or sid == self.app.analytics.get("sensor_id"):
            return
        self.app.analytics.update(sensor_id=sid, detail=None, detail_error="")
        self.app.load_analytics(force=True)
        self.refresh()

    def _no_sensors(self):
        text = ("У вас пока нет датчиков. Добавьте Arduino в разделе «Мои датчики» — после того как показания "
                "поступят на сайт, здесь появится прогноз.") if self.app.user["role"] != "farmer" else (
               "Эксперт ещё не открыл вам доступ к датчикам.")
        lock_card(self.scroll, "Нет датчиков для анализа", text).pack(fill="x")

    def _section(self, title, sub=""):
        box = glass(self.scroll)
        box.pack(fill="x", pady=(0, 14))
        label(box, title, 17, True, TEXT).pack(anchor="w", padx=24, pady=(18, 2 if sub else 10))
        if sub:
            label(box, sub, 12, False, MUTED, wraplength=860, justify="left").pack(anchor="w", padx=24, pady=(0, 10))
        return box

    @staticmethod
    def _end(box):
        label(box, "").pack(pady=3)

    # ----- аналитика выбранного датчика
    def _detail(self, d):
        self._completeness(d)
        self._values(d)
        self._weather(d)
        self._moisture(d)
        self._crops(d)
        self._tips(d)
        foot = self._section("Как считается прогноз")
        for text in d.get("assumptions", []):
            label(foot, "• " + text, 12, False, MUTED, wraplength=860, justify="left").pack(anchor="w", padx=24, pady=1)
        label(foot, "Рекомендации ориентировочные и не заменяют агрономическую экспертизу.", 12, True, MUTED
              ).pack(anchor="w", padx=24, pady=(6, 0))
        self._end(foot)

    def _completeness(self, d):
        comp = d["completeness"]
        title, color = self.QUALITY.get(comp["quality"], ("", MUTED))
        sensor = d["sensor"]
        box = glass(self.scroll)
        box.configure(border_width=2, border_color=color)
        box.pack(fill="x", pady=(0, 14))
        top = ctk.CTkFrame(box, fg_color="transparent")
        top.pack(fill="x", padx=24, pady=(18, 4))
        label(top, f"● {title}", 17, True, color).pack(side="left")
        state = "на связи" if sensor["online"] else f"нет связи, последние данные {fmt_ts(sensor['last'])}"
        label(top, f"{sensor['name']}{TEST_MARK if sensor['is_test'] else ''} · {state} · "
                   f"показаний за неделю: {sensor['count']}", 12).pack(side="right")
        label(box, comp["message"], 14, True, TEXT, wraplength=860, justify="left").pack(anchor="w", padx=24, pady=(0, 6))
        if comp.get("measured"):
            label(box, "Измеряется: " + ", ".join(comp["measured"]) + ".", 12, False, MUTED, wraplength=860,
                  justify="left").pack(anchor="w", padx=24, pady=(0, 4))
        for item in comp.get("missing", []):
            row = ctk.CTkFrame(box, fg_color=GLASS_SOFT, corner_radius=12)
            row.pack(fill="x", padx=24, pady=3)
            label(row, f"Нет: {item['name']} ({item['reason']})", 13, True,
                  DANGER if item["key"] == "water" else WARN).pack(anchor="w", padx=14, pady=(8, 0))
            label(row, "→ " + item["effect"], 12, False, TEXT, wraplength=820, justify="left").pack(
                anchor="w", padx=14, pady=(0, 8))
        for text in comp.get("warnings", []):
            label(box, "⚠ " + text, 12, True, WARN, wraplength=860, justify="left").pack(anchor="w", padx=24, pady=2)
        self._end(box)

    def _values(self, d):
        box = self._section("Последние показания датчика")
        grid = ctk.CTkFrame(box, fg_color="transparent")
        grid.pack(fill="x", padx=24, pady=(0, 18))
        for i, v in enumerate(d["values"]):
            col, row_i = i % 4, i // 4
            grid.grid_columnconfigure(col, weight=1)
            c = ctk.CTkFrame(grid, fg_color=GLASS_SOFT, corner_radius=16)
            c.grid(row=row_i, column=col, sticky="nsew", padx=(0 if col == 0 else 8, 0), pady=4)
            label(c, v["name"], 12).pack(anchor="w", padx=14, pady=(10, 0))
            if v["value"] is None:
                label(c, "нет датчика", 15, True, MUTED).pack(anchor="w", padx=14)
                label(c, v.get("missing") or "", 11, False, MUTED, wraplength=180, justify="left").pack(
                    anchor="w", padx=14, pady=(0, 10))
            else:
                digits = 0 if v["key"] in ("n", "p", "k", "humidity", "water") else 1
                label(c, fmt_num(v["value"], digits, v["unit"]), 18, True, TEXT).pack(anchor="w", padx=14, pady=(0, 10))

    def _weather(self, d):
        wx = d["weather"]
        src = "Open-Meteo" if wx["source"] != "climate" else "средние многолетние"
        box = self._section("Погода на 7 дней", f"Место: {wx.get('place', '')}. Источник: {src}.")
        cur = wx.get("current")
        if cur:
            label(box, f"Сейчас: {cur.get('icon', '')} {cur.get('text', '')}, {fmt_num(cur.get('temp'), 1, '°C')}, "
                       f"ветер {fmt_num(cur.get('wind'), 1, 'м/с')}", 14, True, TEXT).pack(anchor="w", padx=24, pady=(0, 8))
        days = ctk.CTkFrame(box, fg_color="transparent")
        days.pack(fill="x", padx=24, pady=(0, 18))
        for i, day in enumerate(wx["days"][:7]):
            days.grid_columnconfigure(i, weight=1)
            c = ctk.CTkFrame(days, fg_color=GLASS_SOFT, corner_radius=16)
            c.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 8, 0))
            try:
                title = datetime.fromisoformat(day["date"]).strftime("%d.%m")
            except ValueError:
                title = day["date"]
            label(c, title, 12, True, TEXT).pack(pady=(10, 0))
            label(c, day.get("icon", ""), 20).pack()
            tmin = day.get("tmin")
            label(c, f"{fmt_num(day.get('tmax'), 0)}°" + (f" / {fmt_num(tmin, 0)}°" if tmin is not None else ""),
                  13, True, TEXT).pack()
            prob = f" · {day['precip_prob']:.0f}%" if day.get("precip_prob") is not None else ""
            label(c, f"💧 {fmt_num(day.get('precip'), 1)} мм{prob}", 11).pack()
            if day.get("wind") is not None:
                label(c, f"ветер {fmt_num(day['wind'], 0)} м/с", 11).pack()
            label(c, "").pack(pady=2)

    def _moisture(self, d):
        m = d.get("moisture")
        box = self._section("Прогноз влажности почвы на 7 дней",
                            "Водный баланс: последнее показание датчика + осадки и испарение по прогнозу погоды.")
        if not m:
            label(box, "Прогноз невозможен: нет показаний датчика влажности почвы. Подключите датчик влажности "
                       "почвы к Arduino — без него программа не может оценить риск засухи.", 13, True, DANGER,
                  wraplength=860, justify="left").pack(anchor="w", padx=24)
            self._end(box)
            return
        label(box, f"Риск: {m['risk_text']}   ·   сейчас {fmt_num(m['theta_now'], 1, '%')} → через 7 дней "
                   f"{fmt_num(m['theta_7d'], 1, '%')}   ·   порог увядания {fmt_num(m['wilting_point'], 0, '%')}",
              14, True, RISK_COLOR.get(m["risk"], TEXT)).pack(anchor="w", padx=24)
        if m.get("ec_now") is not None:
            label(box, f"Засоление: сейчас {fmt_num(m['ec_now'], 1, 'дСм/м')} → через 7 дней "
                       f"{fmt_num(m['ec_7d'], 1, 'дСм/м')}", 13, False, TEXT).pack(anchor="w", padx=24, pady=(2, 0))
        else:
            label(box, "Прогноз засоления не строится: датчик засолённости не подключён.", 12, False, MUTED
                  ).pack(anchor="w", padx=24, pady=(2, 0))
        series = m.get("series") or []
        if series:
            fig = Figure(figsize=(9, 1.9), dpi=100, facecolor=GLASS)
            ax = fig.add_subplot(111)
            fig.subplots_adjust(left=0.07, right=0.98, top=0.92, bottom=0.2)
            xs = list(range(len(series)))
            ys = [p["theta"] for p in series]
            ax.plot(xs, ys, color=BLUE, linewidth=2.2)
            ax.fill_between(xs, ys, color=BLUE, alpha=0.1)
            ax.axhline(m["wilting_point"], color=DANGER, linestyle="--", linewidth=1)
            ax.set_xticks(xs)
            ax.set_xticklabels([p["date"][8:10] + "." + p["date"][5:7] for p in series])
            style_axes(ax)
            canvas = FigureCanvasTkAgg(fig, master=box)
            canvas.get_tk_widget().configure(height=190)
            canvas.get_tk_widget().pack(fill="x", padx=24, pady=(8, 4))
            canvas.draw_idle()
            self.chart = canvas
        for a in m.get("alerts") or []:
            label(box, "⚠ " + a["text"], 13, False, LEVEL_COLOR.get(a["level"], TEXT), wraplength=860,
                  justify="left").pack(anchor="w", padx=24, pady=2)
        t = d.get("trend")
        if t:
            trend = f"{fmt_num(t['trend'], 1)} %/ч" if t.get("trend") is not None else "мало данных"
            hours = f" · до «сухо» ≈ {fmt_num(t['forecast_hours'], 1)} ч" if t.get("forecast_hours") is not None else ""
            label(box, f"По показаниям за сутки: тренд {trend}, в риске {t['risk_share']} % времени{hours}. "
                       f"{t['verdict']}", 12, False, TEXT, wraplength=860, justify="left").pack(anchor="w", padx=24, pady=(6, 0))
        self._end(box)

    def _crops(self, d):
        crops = d["crops"]
        box = self._section("Что лучше посадить", crops.get("note", ""))
        if not crops["available"]:
            self._end(box)
            return
        grid = ctk.CTkFrame(box, fg_color="transparent")
        grid.pack(fill="x", padx=24, pady=(0, 18))
        for i, c in enumerate(crops["items"]):
            col, row_i = i % 4, i // 4
            grid.grid_columnconfigure(col, weight=1)
            card = ctk.CTkFrame(grid, fg_color=GLASS_SOFT, corner_radius=16)
            card.grid(row=row_i, column=col, sticky="nsew", padx=(0 if col == 0 else 8, 0), pady=4)
            top = ctk.CTkFrame(card, fg_color="transparent")
            top.pack(fill="x", padx=12, pady=(10, 0))
            label(top, c["name"], 14, True, TEXT).pack(side="left")
            label(top, str(c["score"]), 16, True, LEVEL_COLOR.get(c["level"], TEXT)).pack(side="right")
            label(card, c["group"], 11).pack(anchor="w", padx=12)
            meter = ctk.CTkProgressBar(card, height=6, corner_radius=3, fg_color="#D5E3F7",
                                       progress_color=LEVEL_COLOR.get(c["level"], BLUE))
            meter.set(c["score"] / 100)
            meter.pack(fill="x", padx=12, pady=(6, 4))
            for f in [f"✓ {p}" for p in c.get("pluses", [])[:2]] + [f"− {r}" for r in c.get("reasons", [])[:2]]:
                label(card, f, 11, False, TEXT if f.startswith("✓") else MUTED, wraplength=200,
                      justify="left").pack(anchor="w", padx=12)
            label(card, "🌱 " + c["sow"], 11, True, BLUE, wraplength=200, justify="left").pack(
                anchor="w", padx=12, pady=(4, 10))

    def _tips(self, d):
        nutrients = d.get("nutrients") or []
        box = self._section("Питание почвы (N, P, K)",
                            "Фосфор и калий — по шкале Мачигина для карбонатных почв, азот — ориентировочно.")
        if not nutrients:
            label(box, "Датчики азота, фосфора и калия не подключены — оценки питания нет.", 13, False, MUTED
                  ).pack(anchor="w", padx=24)
        for n in nutrients:
            label(box, f"{n['name']}: {fmt_num(n['value'], 0, 'мг/кг')} — {n['level']}", 13, True, TEXT
                  ).pack(anchor="w", padx=24, pady=1)
        self._tip_rows(box, d.get("nutrient_tips") or [])
        self._end(box)
        for title, items in (("Как улучшить почву", d.get("soil_tips") or []),
                             ("Агросоветы по погоде", d.get("weather_tips") or [])):
            if items:
                box = self._section(title)
                self._tip_rows(box, items)
                self._end(box)

    @staticmethod
    def _tip_rows(box, items):
        for x in items:
            row = ctk.CTkFrame(box, fg_color="transparent")
            row.pack(fill="x", padx=24, pady=3)
            label(row, x["level_text"], 11, True, LEVEL_COLOR.get(x["level"], BLUE), width=90,
                  anchor="w").pack(side="left", anchor="n")
            label(row, x["text"], 13, False, TEXT, wraplength=740, justify="left").pack(side="left", padx=8)


# ======================= ТЕСТОВЫЙ РЕЖИМ (только админ, скрыто) =======================
class TestModePage(ctk.CTkFrame):
    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app
        page_title(self, "Тестовый режим").pack(anchor="w")
        self.warn = label(self, "", 13, False, WARN, wraplength=900, justify="left")
        self.warn.pack(anchor="w", pady=(8, 12))
        top = glass(self)
        top.pack(fill="x", pady=(0, 14))
        self.switch = toggle(top, "Тестовый режим включён", self.toggle_mode)
        self.switch.pack(anchor="w", padx=24, pady=(18, 6))
        label(top, "Физических датчиков почвы пока нет. Тестовый режим добавляет виртуальные датчики с "
                   "показателями на основе открытых данных и их GPS-координаты.",
              12, False, MUTED, wraplength=900, justify="left").pack(anchor="w", padx=24, pady=(0, 18))
        self.list = ctk.CTkScrollableFrame(self, fg_color="transparent", height=260)
        self.list.pack(fill="both", expand=True)
        add = glass(self)
        add.pack(fill="x", pady=(14, 0))
        label(add, "Добавить тестовый датчик", 15, True, TEXT).pack(anchor="w", padx=24, pady=(14, 8))
        row = ctk.CTkFrame(add, fg_color="transparent")
        row.pack(anchor="w", padx=24, pady=(0, 16))
        self.new_name = entry(row, 220, placeholder_text="Название")
        self.new_name.grid(row=0, column=0, padx=(0, 10))
        self.new_profile = dropdown(row, list(PROFILE_TITLES.values()), width=280)
        self.new_profile.grid(row=0, column=1, padx=(0, 10))
        self.new_lat = entry(row, 120, placeholder_text="широта")
        self.new_lat.grid(row=0, column=2, padx=(0, 10))
        self.new_lon = entry(row, 120, placeholder_text="долгота")
        self.new_lon.grid(row=0, column=3, padx=(0, 10))
        pill(row, "Добавить", self.add_sensor, width=120).grid(row=0, column=4)
        self.msg = label(self, "")
        self.msg.pack(anchor="w", pady=(10, 0))

    def refresh(self):
        on = db.get_settings()["test_mode"] == "1"
        self.switch.select() if on else self.switch.deselect()
        self.warn.configure(text=("Тестовый режим активен. Показания тестовых датчиков помечены *, это "
                                  "ориентировочные значения, а не измерения.") if on else
                            "Тестовый режим выключен: тестовые датчики скрыты и не сохраняются.")
        for widget in self.list.winfo_children():
            widget.destroy()
        for sensor in db.test_sensors():
            self._row(sensor)

    def _row(self, sensor):
        box = glass(self.list)
        box.pack(fill="x", padx=4, pady=6)
        top = ctk.CTkFrame(box, fg_color="transparent")
        top.pack(fill="x", padx=20, pady=(14, 6))
        name = entry(top, 240)
        name.insert(0, sensor["name"])
        name.pack(side="left")
        profile = dropdown(top, list(PROFILE_TITLES.values()), width=300)
        profile.set(PROFILE_TITLES.get(sensor["profile"], list(PROFILE_TITLES.values())[0]))
        profile.pack(side="left", padx=10)
        enabled = toggle(top, "включён")
        enabled.select() if sensor["enabled"] else enabled.deselect()
        enabled.pack(side="left", padx=10)
        bottom = ctk.CTkFrame(box, fg_color="transparent")
        bottom.pack(fill="x", padx=20, pady=(0, 14))
        label(bottom, "GPS:", 13, True, TEXT).pack(side="left", padx=(0, 8))
        lat = entry(bottom, 130, placeholder_text="широта")
        lon = entry(bottom, 130, placeholder_text="долгота")
        if sensor["lat"] is not None:
            lat.insert(0, str(sensor["lat"]))
        if sensor["lon"] is not None:
            lon.insert(0, str(sensor["lon"]))
        lat.pack(side="left", padx=(0, 8))
        lon.pack(side="left", padx=(0, 16))
        sid = sensor["id"]

        def save():
            try:
                lat_v = float(lat.get().replace(",", ".")) if lat.get().strip() else None
                lon_v = float(lon.get().replace(",", ".")) if lon.get().strip() else None
            except ValueError:
                self.msg.configure(text="Координаты должны быть числами", text_color=DANGER)
                return
            key = next(k for k, v in PROFILE_TITLES.items() if v == profile.get())
            db.update_sensor(sid, name.get().strip() or sensor["name"], "", lat_v, lon_v, enabled.get() == 1, key)
            self.msg.configure(text=f"Сохранено: {name.get().strip()}", text_color=OK)

        def remove():
            db.delete_sensor(sid)
            self.refresh()

        pill(bottom, "Сохранить", save, width=120).pack(side="left", padx=(0, 8))
        pill(bottom, "Удалить", remove, width=110, primary=False, danger=True).pack(side="left")

    def toggle_mode(self):
        on = self.switch.get() == 1
        db.set_settings({"test_mode": "1" if on else "0"})
        self.app.on_test_mode_changed(on)
        self.refresh()

    def add_sensor(self):
        name = self.new_name.get().strip()
        if not name:
            self.msg.configure(text="Введите название датчика", text_color=DANGER)
            return
        try:
            lat = float(self.new_lat.get().replace(",", ".")) if self.new_lat.get().strip() else None
            lon = float(self.new_lon.get().replace(",", ".")) if self.new_lon.get().strip() else None
        except ValueError:
            self.msg.configure(text="Координаты должны быть числами", text_color=DANGER)
            return
        key = next(k for k, v in PROFILE_TITLES.items() if v == self.new_profile.get())
        db.add_test_sensor(name, key, lat, lon)
        self.new_name.delete(0, "end")
        self.msg.configure(text=f"Добавлен тестовый датчик «{name}»", text_color=OK)
        self.refresh()


# ======================= ПОЛЬЗОВАТЕЛИ (только админ, скрыто) =======================
class UsersPage(ctk.CTkFrame):
    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app
        page_title(self, "Все пользователи").pack(anchor="w")
        self.list = ctk.CTkScrollableFrame(self, fg_color="transparent", height=420)
        self.list.pack(fill="both", expand=True, pady=(14, 0))
        self.msg = label(self, "")
        self.msg.pack(anchor="w", pady=(10, 0))

    def refresh(self):
        for widget in self.list.winfo_children():
            widget.destroy()
        for u in db.list_users():
            row = glass(self.list)
            row.pack(fill="x", padx=4, pady=5)
            info = f"{u['first_name']} {u['last_name']}".strip() or u["username"]
            label(row, info, 15, True, TEXT).pack(side="left", padx=20, pady=14)
            extra = f"{ROLE_LABEL[u['role']]} · логин {u['username']}"
            if u["company"]:
                extra += f" · {u['company']}"
            if u["role"] == "expert":
                extra += " · " + plans.status_line(plans.effective(u, db.get_user_by_id))
            label(row, extra, 13).pack(side="left", padx=6)
            if u["role"] == "expert":
                plan = dropdown(row, ["Бесплатный", "Pro (выдать)"], width=160,
                                command=lambda v, uid=u["id"]: self.set_plan(uid, v))
                plan.set("Pro (выдать)" if plans.own_plan(u)[0] == "pro" else "Бесплатный")
                plan.pack(side="right", padx=(0, 16))
            if u["id"] != self.app.user["id"]:
                pill(row, "Удалить", lambda uid=u["id"]: self.delete(uid), width=110,
                     primary=False, danger=True).pack(side="right", padx=16)

    def set_plan(self, user_id, value):
        """Оплаты пока нет — подписку Pro выдаёт администратор вручную (для проверки и для клиентов)."""
        db.set_plan(user_id, "pro" if value.startswith("Pro") else "free", 0)
        self.msg.configure(text="Тариф эксперта изменён. Его фермеры получают тот же тариф.", text_color=OK)
        self.refresh()

    def delete(self, user_id):
        db.delete_user(user_id)
        self.msg.configure(text="Пользователь удалён", text_color=OK)
        self.refresh()


# ======================= ПРИЛОЖЕНИЕ =======================
class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("light")
        ctk.set_default_color_theme("blue")
        self.configure(fg_color=BG)
        self.title(APP_NAME)
        self._set_icon()
        self.geometry("1240x820")
        self.minsize(1000, 680)
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        db.init_db()

        self.queue = queue.Queue()
        self.user = None
        self.admin_revealed = False
        self.pages = {}
        self.nav = {}
        self.current = None
        self.selected = "avg"
        self.readers = {}
        self.latest_real = {}
        self.test_state = {}
        self.notifier = Notifier()
        self.bot = None
        self.last_send = 0.0
        self.last_prune = 0.0
        self.send_info = ""
        self.online = {}
        self.prev_status = {}
        self.toasts = ToastStack(self)
        self.analytics = self._load_analytics_cache()
        self.analytics_loading = False
        self.analytics_error = ""
        self.analytics_attempt = 0.0
        self.bind_all("<Control-Shift-KeyPress>", self.on_secret_key)

        self.title(self.brand_name())
        self.show_auth()
        self.start_readers()
        self.sync_bot()
        self.after(200, self.poll)
        self.after(TICK_SECONDS * 1000, self.tick)

    # ---------- общие ----------
    def _clear(self):
        for w in self.winfo_children():
            w.destroy()

    def show_auth(self, admin=False):
        self._clear()
        self.toasts.items = []
        self.pages, self.nav = {}, {}
        self.user = None
        frame = AuthScreen(self, self)
        frame.pack(fill="both", expand=True)
        if admin:
            frame.show_login(admin=True)
        self.auth_frame = frame

    def on_secret_key(self, event=None):
        if event is not None and event.keysym.lower() != "a" and getattr(event, "keycode", 0) != 65:
            return
        if self.user is None:
            self.show_auth(admin=True)
            return
        if self.user["role"] != "admin":
            return
        self.admin_revealed = not self.admin_revealed
        if not self.admin_revealed and self.current in ("testmode", "users"):
            self.current = "sensors"
        self._build_nav()
        self.show_page(self.current or "sensors")

    def on_login(self, user):
        self.user = user
        self.admin_revealed = user["role"] == "admin"
        s = db.get_settings()
        # программы, подключённые к сайту до версии 1.5, «присваивает» первый вошедший эксперт или админ
        if s["api_key"] and not s.get("site_owner") and user["role"] in ("expert", "admin"):
            db.set_settings({"site_owner": str(user["id"])})
        self._clear()
        self.toasts.items = []
        self._build_main()
        self.show_page("sensors")
        self._plan_reminder()
        if s["api_key"] and self.site_allowed():
            self.load_analytics()

    def _set_icon(self):
        """Иконка окна Drought (icon.ico рядом с программой или внутри собранного exe)."""
        base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
        try:
            self.iconbitmap(str(base / "icon.ico"))
        except Exception:
            pass   # не Windows или файла нет — остаётся стандартная иконка

    # ---------- тариф ----------
    def reload_user(self):
        row = db.get_user_by_id(self.user["id"])
        if row:
            self.user = dict(row)
        self.apply_brand()
        self._build_nav()

    def plan(self):
        """Действующий тариф вошедшего пользователя (фермер — по тарифу своего эксперта)."""
        row = db.get_user_by_id(self.user["id"]) or self.user
        return plans.effective(row, db.get_user_by_id)

    def limits(self):
        return plans.limits(self.plan())

    def install_plan(self):
        """Тариф того, кто подключил программу к сайту: от него зависят отправка по API/JSON и аналитика."""
        owner = db.get_settings().get("site_owner")
        user = db.get_user_by_id(int(owner)) if owner and owner.isdigit() else None
        if user is None:
            return None
        return plans.effective(user, db.get_user_by_id)

    def site_allowed(self):
        info = self.install_plan()
        return info is None or plans.limits(info)["integrations"]

    def _plan_reminder(self):
        info = self.plan()
        if self.user["role"] == "expert" and info["code"] == "trial" and info["days_left"] is not None \
                and info["days_left"] <= 2:
            self.notify(f"Пробный период заканчивается через {info['days_left']} дн. Потом включится "
                        "бесплатный тариф, данные сохранятся.", False, kind="system")

    # ---------- название: «ПочваМонитор X Drought» после подключения к сайту ----------
    def brand_name(self):
        s = db.get_settings()
        if s.get("api_key") and s.get("site_brand") and self.site_allowed():
            return s["site_brand"]
        return APP_NAME

    def apply_brand(self):
        name = self.brand_name()
        self.title(name)
        if hasattr(self, "brand_label"):
            try:
                self.brand_label.configure(text=name)
            except Exception:
                pass

    def on_site_connected(self):
        self.apply_brand()
        self.notify(f"Программа подключена к сайту. Теперь это «{self.brand_name()}»: аналитика сайта — "
                    "в разделе «Аналитика».", True, kind="system")
        self.analytics = {}
        self.load_analytics(force=True)
        s = db.get_settings()
        threading.Thread(target=self._fetch_info, args=(s["site_url"], s["api_key"]), daemon=True).start()
        if "devices" in self.pages:
            self.pages["devices"].site_devices = None

    def _fetch_info(self, site, key):
        try:
            info = site_client.info(site, key)
            self.queue.put(("site_info", info))
        except (ConnectionError, site_client.Revoked):
            pass

    def disconnect_site(self, text):
        db.set_settings({"api_key": "", "send_mode": "off", "site_brand": "", "site_owner": "",
                         "site_owner_name": ""})
        self.analytics = {}
        try:
            ANALYTICS_CACHE.unlink()
        except OSError:
            pass
        self.apply_brand()
        self.notify(text, True, kind="system")
        if self.user:
            self._build_nav()
            if self.current in ("analytics", "devices", "settings"):
                self.show_page(self.current)

    def on_revoked(self):
        if db.get_settings()["api_key"]:
            self.disconnect_site("Это устройство отключено от аккаунта на сайте. Отправка данных и аналитика "
                                 "остановлены. Чтобы подключиться снова, введите новый код в «Настройках».")

    # ---------- аналитика с сайта ----------
    def analytics_state(self):
        s = db.get_settings()
        if not self.limits()["analytics"]:
            return "locked"
        if not s["api_key"] or not self.site_allowed():
            return "offline"
        return "ready"

    @staticmethod
    def _load_analytics_cache():
        try:
            data = json.loads(ANALYTICS_CACHE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if data.get("version") == 2 else {}   # старый формат (по участкам сайта) не используем

    def _save_analytics_cache(self):
        try:
            ANALYTICS_CACHE.write_text(json.dumps(self.analytics, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass

    def analytics_sensors(self):
        """Датчики для аналитики: те, что видит пользователь (тестовые — только в тестовом режиме)."""
        test_mode = db.get_settings()["test_mode"] == "1"
        return [v for v in self._visible() if v["kind"] != "test" or test_mode]

    def load_analytics(self, force=False):
        """Список датчиков аккаунта на сайте + аналитика выбранного датчика (по его показаниям)."""
        s = db.get_settings()
        if not s["api_key"] or not self.site_allowed() or self.analytics_loading or self.user is None:
            return
        fresh = time.time() - self.analytics.get("fetched_at", 0) < ANALYTICS_TTL
        if not force and ((fresh and self.analytics.get("detail")) or time.time() - self.analytics_attempt < 60):
            return
        self.analytics_attempt = time.time()
        mine = [v["id"] for v in self.analytics_sensors()]
        wanted = self.analytics.get("sensor_id")
        if wanted not in mine:
            wanted = self.selected if self.selected in mine else (mine[0] if mine else None)
        include_test = s["test_mode"] == "1"
        self.analytics_loading = True

        def work():
            try:
                listed = site_client.sensors(s["site_url"], s["api_key"], include_test)
                detail, detail_error = None, ""
                if wanted is not None:
                    if wanted in {x["id"] for x in listed}:
                        detail = site_client.sensor(s["site_url"], s["api_key"], wanted)
                    else:
                        detail_error = ("Сайт не получал показаний этого датчика за последние 7 дней. Проверьте, что "
                                        "датчик на связи и отправка на сайт включена (Настройки → Отправка данных: "
                                        "«На сайт»). Прогноз появится после первых показаний.")
                result = ("ok", {"version": 2, "sensors": listed, "sensor_id": wanted, "detail": detail,
                                 "detail_error": detail_error, "fetched_at": time.time()})
            except site_client.Revoked as err:
                result = ("revoked", str(err))
            except (ConnectionError, KeyError, TypeError) as err:
                result = ("error", str(err))
            self.queue.put(("analytics", *result))

        threading.Thread(target=work, daemon=True).start()

    def on_analytics(self, status, payload):
        self.analytics_loading = False
        if status == "ok":
            self.analytics = payload
            self.analytics_error = ""
            self._save_analytics_cache()
        elif status == "revoked":
            self.on_revoked()
        else:
            self.analytics_error = ("Сайт недоступен, показаны последние полученные данные"
                                    if self.analytics.get("sensors") is not None else payload)
        if self.user and self.current == "analytics":
            self.pages["analytics"].refresh()

    def logout(self):
        self.show_auth()

    def on_close(self):
        self.stop_readers()
        if self.bot:
            self.bot.stop()
        self.destroy()

    # ---------- меню и страницы по ролям ----------
    def _allowed(self, revealed=None):
        role = self.user["role"]
        shown = self.admin_revealed if revealed is None else revealed
        items = [("sensors", "Датчики"), ("history", "История"), ("analytics", "Аналитика"),
                 ("events", "Уведомления")]
        if role in ("expert", "admin"):
            items += [("manage", "Мои датчики")]
        if role == "expert":
            items += [("farmers", "Фермеры")]
        items += [("devices", "Устройства")]
        if role in ("expert", "admin"):
            items += [("notify", "Telegram-бот"), ("settings", "Настройки")]
        if role == "expert":
            items += [("plan", "Подписка")]   # фермеру подписку не предлагаем: он работает по тарифу эксперта
        items += [("account", "Аккаунт")]
        if role == "admin" and shown:
            items += [("testmode", "Тестовый режим"), ("users", "Все пользователи")]
        return items

    def _build_main(self):
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.side = glass(self, width=240)
        self.side.grid(row=0, column=0, sticky="ns", padx=(14, 0), pady=14)
        self.side.grid_propagate(False)
        self.brand_label = label(self.side, self.brand_name(), 18, True, TEXT, wraplength=200, justify="left")
        self.brand_label.pack(anchor="w", padx=22, pady=(22, 2))
        self.who = label(self.side, "", 13, False, MUTED, wraplength=200, justify="left")
        self.who.pack(anchor="w", padx=22, pady=(0, 10))
        self.nav_box = ctk.CTkFrame(self.side, fg_color="transparent")
        self.nav_box.pack(fill="x")
        pill(self.side, "Выйти", self.logout, width=190, primary=False).pack(side="bottom", padx=24, pady=(8, 24))
        self.connection_label = label(self.side, "Датчики не подключены", 13, False, MUTED, wraplength=200)
        self.connection_label.pack(side="bottom", padx=22, pady=6)

        self.content = glass(self)
        self.content.grid(row=0, column=1, sticky="nsew", padx=24, pady=14)
        self.content.grid_rowconfigure(0, weight=1)
        self.content.grid_columnconfigure(0, weight=1)
        inner = ctk.CTkFrame(self.content, fg_color="transparent")
        inner.place(relx=0, rely=0, relwidth=1, relheight=1)
        inner.grid_rowconfigure(0, weight=1)
        inner.grid_columnconfigure(0, weight=1)
        self.body = inner
        builders = {"sensors": SensorsPage, "history": HistoryPage, "manage": ManagePage, "farmers": FarmersPage,
                    "notify": NotifyPage, "settings": SettingsPage, "account": AccountPage, "events": EventsPage,
                    "testmode": TestModePage, "users": UsersPage, "analytics": AnalyticsPage,
                    "devices": DevicesPage, "plan": SubscriptionPage}
        for key, _ in self._allowed(revealed=True):
            self.pages[key] = builders[key](inner, self)
            self.pages[key].grid(row=0, column=0, sticky="nsew", padx=22, pady=22)
        self._build_nav()

    def _build_nav(self):
        for w in self.nav_box.winfo_children():
            w.destroy()
        self.nav = {}
        role = ROLE_LABEL[self.user["role"]]
        company = self.user["company"] if self.user["role"] == "expert" else ""
        info = self.plan()
        plan_text = "" if info["code"] == "admin" or self.user["role"] == "farmer" else f"\nТариф: {info['title']}"
        self.who.configure(text=f"{self.user['first_name']} {self.user['last_name']}\n{role}"
                                + (f" · {company}" if company else "") + plan_text)
        allowed = self._allowed()
        if self.user["role"] == "admin" and self.admin_revealed:
            label(self.nav_box, "Администрирование", 12, True, WARN).pack(anchor="w", padx=22, pady=(10, 2))
        for key, text in allowed:
            if key not in self.pages:
                continue
            btn = ctk.CTkButton(self.nav_box, text=text, anchor="w", height=34, corner_radius=17,
                                fg_color="transparent", hover_color="#FFFFFF", text_color=TEXT, font=font(14),
                                command=lambda k=key: self.show_page(k))
            btn.pack(fill="x", padx=14, pady=2)
            btn.base_text = text
            self.nav[key] = btn
        self._mark_nav()
        self.update_badge()

    def event_scope(self):
        """Какие датчики учитывать в истории уведомлений: админ и эксперт — все, фермер — свои."""
        if self.user is None or self.user["role"] in ("admin", "expert"):
            return None
        return [v["id"] for v in self._visible()]

    def update_badge(self):
        btn = self.nav.get("events") if self.user else None
        if btn is None:
            return
        n = db.unread_events(self.event_scope())
        try:
            btn.configure(text=f"{btn.base_text}  ·  {n}" if n else btn.base_text)
        except Exception:
            pass

    def _mark_nav(self):
        for k, btn in self.nav.items():
            active = k == self.current
            btn.configure(fg_color="#FFFFFF" if active else "transparent",
                          text_color=BLUE if active else TEXT)

    def show_page(self, key):
        if key not in self.pages:
            key = "sensors"
        self.current = key
        self._mark_nav()
        page = self.pages[key]
        page.tkraise()
        if hasattr(page, "refresh"):
            page.refresh()
        self.refresh_current()

    # ---------- датчики для текущего пользователя ----------
    def on_pick_sensor(self, text):
        for t, sid in self._sensor_options():
            if t == text:
                self.selected = sid
        self.refresh_current()

    def _visible(self):
        return db.visible_sensors(self.user, db.get_settings()["test_mode"] == "1")

    def _sensor_options(self):
        options = [(AVG_LABEL, "avg")]
        for v in self._visible():
            options.append((v["name"] + (TEST_MARK if v["kind"] == "test" else ""), v["id"]))
        return options

    def sensor_view(self):
        s = db.get_settings()
        visible = self._visible()
        ids = {v["id"] for v in visible}
        if self.selected != "avg" and self.selected not in ids:
            self.selected = "avg"
        now = time.time()
        latest = {}
        for r in db.latest_by_sensor():
            row = dict(r)
            row["age"] = now - row["ts"]
            latest[row["sensor_id"]] = row
        bases = []
        for v in visible:
            if v["kind"] == "test" and v["profile"] in PROFILES:
                basis = PROFILES[v["profile"]]["basis"]
                if basis not in bases:
                    bases.append(basis)
        if self.selected == "avg":
            rows = [latest[v["id"]] for v in visible if v["id"] in latest]
            fresh = [r for r in rows if r["age"] <= FRESH_SECONDS]
            use = fresh or rows
            current = analytics.average_latest(use) if use else None
            if current:
                current["stale"] = not fresh
                current["last_at"] = datetime.fromtimestamp(max(r["ts"] for r in use)).strftime("%H:%M:%S")
            is_test = any(v["kind"] == "test" for v in visible)
            coords, company = None, ""
        else:
            v = next(x for x in visible if x["id"] == self.selected)
            current = latest.get(self.selected)
            if current:
                current["stale"] = current["age"] > FRESH_SECONDS
                current["last_at"] = datetime.fromtimestamp(current["ts"]).strftime("%H:%M:%S")
            is_test = v["kind"] == "test"
            coords = (f"{v['lat']:.4f}, {v['lon']:.4f}" if v["lat"] is not None else "не задано") if is_test else None
            bases = [PROFILES[v["profile"]]["basis"]] if is_test and v["profile"] in PROFILES else []
            company = self.user["company"] if self.user["role"] == "farmer" else ""
        return {"s": s, "options": self._sensor_options(), "selected": self.selected, "current": current,
                "is_test": is_test, "coords": coords, "bases": bases, "company": company,
                "is_admin": self.user["role"] == "admin"}

    def history_data(self):
        s = db.get_settings()
        visible = self._visible()
        ids = {v["id"] for v in visible}
        if self.selected != "avg" and self.selected not in ids:
            self.selected = "avg"
        span = RANGES[self.pages["history"].range_var.get()]
        days = self.limits()["history_days"]
        if days is not None:
            span = min(span, days * 86400)
        since = time.time() - span
        threshold = float(s["water_warn"]) / WATER_MAX * 100
        bucket = max(10 if self.selected == "avg" else 0, int(span / MAX_CHART_POINTS))
        points = db.get_series(since, self._series_ids(), bucket if bucket >= TICK_SECONDS * 2 else 0)
        if self.selected == "avg":
            is_test = any(v["kind"] == "test" for v in visible)
        else:
            is_test = next(v for v in visible if v["id"] == self.selected)["kind"] == "test"
        show_extras = s["test_mode"] == "1" and any(p.get(k) is not None for p in points
                                                    for k in analytics.EXTRA)
        return points, show_extras, threshold, (TEST_MARK if is_test else "")

    def refresh_history(self):
        if "history" in self.pages:
            points, show_extras, threshold, suffix = self.history_data()
            self.pages["history"].render(points, show_extras, threshold, suffix)

    def refresh_current(self):
        if self.user is None:
            return
        if self.current == "sensors":
            self.pages["sensors"].render(self.sensor_view(), self.send_info)
        elif self.current == "history":
            self.refresh_history()
        for page in ("sensors", "history"):
            if page in self.pages and page != self.current:
                self.pages[page].picker.configure(values=[t for t, _ in self._sensor_options()])

    def open_sources(self):
        for text, url in [src for key in PROFILES for src in PROFILES[key]["sources"] if src[1]]:
            webbrowser.open(url)
        self.send_info = "Открыты источники данных"

    def on_test_mode_changed(self, enabled):
        self.notifier.test_mode_changed(db.get_settings(), enabled)
        if not enabled and self.selected not in ("avg",):
            self.selected = "avg"
        self.refresh_current()

    # ---------- датчики: чтение с COM-портов ----------
    def start_readers(self):
        self.stop_readers()
        for sensor in db.collect_sensors(include_test=False):
            if sensor["kind"] != "real" or not sensor["port"]:
                continue
            sid = sensor["id"]
            reader = SerialReader(
                sensor["port"],
                on_data=lambda d, sid=sid: self.queue.put(("data", sid, d)),
                on_status=lambda msg, ok, sid=sid: self.queue.put(("status", sid, msg, ok)))
            reader.start()
            self.readers[sid] = reader
        self.set_connection()

    def stop_readers(self):
        for reader in self.readers.values():
            reader.stop()
        self.readers = {}

    def restart_readers(self):
        self.start_readers()
        self.refresh_current()

    def set_connection(self):
        if not hasattr(self, "connection_label"):
            return
        now = time.time()
        sensors = [s for s in db.collect_sensors(include_test=False) if s["kind"] == "real" and s["port"]]
        fresh = 0
        for sensor in sensors:
            entry_data = self.latest_real.get(sensor["id"])
            if entry_data and now - entry_data[1] <= FRESH_SECONDS:
                fresh += 1
        color = OK if fresh else (DANGER if sensors else MUTED)
        self.connection_label.configure(text=f"● На связи: {fresh} из {len(sensors)}", text_color=color)

    def notify(self, text, ok=True, kind="system", sensor_id=None, popup=True):
        """Уведомление: всегда в историю, во всплывающее окно — если пользователь видит этот датчик
        и всплывающие уведомления включены."""
        db.log_event(time.time(), "ok" if ok else "warn", text, kind, sensor_id)
        if self.user is None:
            return
        if sensor_id is not None and sensor_id not in {v["id"] for v in self._visible()}:
            return   # фермер не получает уведомления по чужим датчикам
        self.update_badge()
        if self.current == "events":
            self.pages["events"].refresh()
        elif popup and db.get_settings().get("popups", "1") == "1":
            self.toasts.show(text, ok, on_open=lambda: self.show_page("events"))

    def toast(self, text, ok=True):
        self.notify(text, ok, kind="system")

    def play_alert(self, kind):
        patterns = {"offline": [(880, 280), (880, 280), (660, 450)],
                    "danger": [(1200, 220), (1200, 220), (1200, 380)],
                    "warn": [(660, 350)],
                    "online": [(1000, 180), (1300, 220)]}

        def work():
            try:
                if winsound is None:
                    raise RuntimeError("no winsound")
                for freq, dur in patterns.get(kind, []):
                    winsound.Beep(freq, dur)
            except Exception:
                self.after(0, self.bell)

        threading.Thread(target=work, daemon=True).start()

    def announce(self, kind, text, ok, sensor_id=None):
        event_kind = "link" if kind in ("offline", "online") else "drought"
        self.notify(text, ok, kind=event_kind, sensor_id=sensor_id)
        visible = self.user is not None and (sensor_id is None or sensor_id in {v["id"] for v in self._visible()})
        if visible and db.get_settings().get("popup_sound", "1") == "1":
            self.play_alert(kind)

    def _check_links(self, now):
        for sensor in db.collect_sensors(include_test=False):
            if sensor["kind"] != "real" or not sensor["port"]:
                continue
            sid = sensor["id"]
            entry_data = self.latest_real.get(sid)
            online = bool(entry_data) and now - entry_data[1] <= FRESH_SECONDS
            prev = self.online.get(sid)
            self.online[sid] = online
            if prev is None:
                continue
            if prev and not online:
                self.announce("offline", f"Датчик «{sensor['name']}» отключился: данные больше не поступают.", False,
                              sid)
            elif online and not prev:
                self.announce("online", f"Датчик «{sensor['name']}» снова на связи.", True, sid)

    def _status_alert(self, sensor, status):
        prev = self.prev_status.get(sensor["id"])
        self.prev_status[sensor["id"]] = status
        if prev is None or prev == status:
            return
        sid = sensor["id"]
        if status == "danger":
            self.announce("danger", f"Датчик «{sensor['name']}» находится в засухе.", False, sid)
        elif status == "warn":
            self.announce("warn", f"Датчик «{sensor['name']}»: признаки засухи.", False, sid)
        elif status == "ok":
            self.notify(f"Датчик «{sensor['name']}» снова в норме.", True, kind="drought", sensor_id=sid)

    def export_dialog(self):
        """Выбор периода экспорта. Бесплатно — до 7 дней, в подписке — до 30 дней."""
        allowed = self.limits()["export_days"]
        win = ctk.CTkToplevel(self)
        win.title("Экспорт в Excel")
        win.geometry("520x300")
        win.configure(fg_color=BG)
        win.transient(self)
        win.after(150, win.lift)
        box = glass(win)
        box.pack(fill="both", expand=True, padx=16, pady=16)
        label(box, "Экспорт показаний в Excel", 17, True, TEXT).pack(anchor="w", padx=22, pady=(18, 4))
        name = AVG_LABEL if self.selected == "avg" else next(
            (v["name"] for v in self._visible() if v["id"] == self.selected), self.selected)
        label(box, f"Датчик: {name}. Больше суток — средние по минутам.", 12).pack(anchor="w", padx=22)
        var = ctk.StringVar(value="24 часа")
        segmented(box, list(EXPORT_RANGES), variable=var).pack(anchor="w", padx=22, pady=(12, 6))
        info = label(box, "", 12, True, WARN, wraplength=460, justify="left")
        info.pack(anchor="w", padx=22)
        if allowed < max(EXPORT_RANGES.values()):
            info.configure(text="🔒 " + plans.locked_text(self.plan(), f"Экспорт больше чем за {allowed} дней"))

        def go():
            days = EXPORT_RANGES[var.get()]
            if days > allowed:
                info.configure(text="🔒 " + plans.locked_text(self.plan(), f"Экспорт за {days} дней"))
                return
            win.destroy()
            self.export_history(days)

        pill(box, "Сохранить в Excel", go, width=200).pack(anchor="w", padx=22, pady=(12, 18))

    def export_history(self, days=1):
        days = min(days, self.limits()["export_days"])
        ids = self._series_ids()
        points = db.get_series(time.time() - days * 86400, ids, 0 if days <= 1 else 60)
        if self.selected == "avg" and days <= 1:
            points = analytics.average_series(points)
        if not points:
            return self.toast("Нет данных за выбранный период для экспорта", False)
        path = filedialog.asksaveasfilename(defaultextension=".xlsx", filetypes=[("Excel", "*.xlsx")],
                                            initialfile=f"datchiki_{days}d_{datetime.now():%Y%m%d_%H%M}.xlsx")
        if not path:
            return
        if self.selected == "avg":
            name = AVG_LABEL
        else:
            name = next((v["name"] for v in self._visible() if v["id"] == self.selected), self.selected)
        try:
            excel_io.export_points(path, points, name)
        except ImportError:
            return self.toast("Для Excel установите библиотеку: pip install openpyxl", False)
        self.toast(f"Файл сохранён: {os.path.basename(path)} ({len(points)} строк)", True)

    def _series_ids(self):
        ids = [v["id"] for v in self._visible()]
        if self.selected != "avg":
            ids = [i for i in ids if i == self.selected]
        return ids

    def import_history(self):
        if self.selected == "avg":
            return self.toast("Сначала выберите датчик в списке, в который импортировать данные", False)
        target = next((v for v in self._visible() if v["id"] == self.selected), None)
        if target is None:
            return self.toast("Датчик не найден", False)
        path = filedialog.askopenfilename(filetypes=[("Excel", "*.xlsx")])
        if not path:
            return
        settings = db.get_settings()
        try:
            rows, skipped = excel_io.read_import(path)
        except ImportError:
            return self.toast("Для Excel установите библиотеку: pip install openpyxl", False)
        except ValueError as err:
            return self.toast(str(err), False)
        for ts, values in rows:
            status = evaluate_status(values["temperature"], values["humidity"], values["water"], settings)
            db.add_reading(ts, target["id"], 1 if target["kind"] == "test" else 0, values, status)
        self.toast(f"Импортировано строк: {len(rows)}. Пропущено: {skipped}", True)
        self.refresh_history()

    def sync_bot(self):
        s = db.get_settings()
        want = s["tg_enabled"] == "1" and s["tg_token"]
        if self.bot and (not want or self.bot.token != s["tg_token"]):
            self.bot.stop()
            self.bot = None
        if want and self.bot is None:
            self.bot = station_bot.StationBot(s["tg_token"])
            self.bot.start()

    # ---------- цикл ----------
    def poll(self):
        while True:
            try:
                item = self.queue.get_nowait()
            except queue.Empty:
                break
            kind = item[0]
            if kind == "data":
                d = item[2]
                d["status"] = evaluate_status(d["temperature"], d["humidity"], d["water"], db.get_settings())
                self.latest_real[item[1]] = (d, time.time())
            elif kind == "status":
                self.set_connection()
            elif kind == "send":
                self.send_info = item[1]
            elif kind == "pair" and self.user and self.user["role"] in ("expert", "admin") \
                    and "settings" in self.pages:
                self.pages["settings"].on_pair_result(item[1], item[2], item[3], item[4])
            elif kind == "analytics":
                self.on_analytics(item[1], item[2])
            elif kind == "devices" and self.user and "devices" in self.pages:
                self.pages["devices"].on_devices(item[1], item[2])
            elif kind == "device_revoke" and self.user and "devices" in self.pages:
                self.pages["devices"].on_revoke(item[1], item[2])
            elif kind == "site_info":
                owner = item[1].get("owner") or {}
                db.set_settings({"site_brand": item[1].get("brand") or db.get_settings()["site_brand"],
                                 "site_owner_name": owner.get("username", "")})
                self.apply_brand()
            elif kind == "send_revoked":
                self.on_revoked()
        self.after(200, self.poll)

    def tick(self):
        try:
            self._tick()
        finally:
            self.after(TICK_SECONDS * 1000, self.tick)

    def _tick(self):
        s = db.get_settings()
        now = time.time()
        test_on = s["test_mode"] == "1"
        saved = []
        for sensor in db.collect_sensors(include_test=test_on):
            sid = sensor["id"]
            if sensor["kind"] == "real":
                entry_data = self.latest_real.get(sid)
                if not entry_data or now - entry_data[1] > FRESH_SECONDS:
                    continue
                values = dict(entry_data[0])
                status = values["status"]
            else:
                state = self.test_state.setdefault(sid, {})
                values = sample_soil(state, sensor["profile"])
                status = evaluate_status(values["temperature"], values["humidity"], values["water"], s)
                values["status"] = status
            db.add_reading(now, sid, 1 if sensor["kind"] == "test" else 0, values, status)
            db.touch_sensor(sid, status, now)
            self.notifier.on_reading(s, sensor, values, status)
            self._status_alert(sensor, status)
            saved.append((sensor, values))
        self._check_links(now)
        self.notifier.check_offline(s, db.collect_sensors(include_test=test_on), now)
        if s["send_mode"] != "off" and saved and now - self.last_send >= float(s["send_interval"]):
            self.last_send = now
            if not self.site_allowed():
                # отправка по API и в JSON-файл — функция подписки того, кто подключил программу
                self.send_info = "Отправка на сайт приостановлена: нужна подписка (раздел «Подписка»)"
            else:
                for sensor, values in saved:
                    self._send_async(s, sensor, values)
        if now - self.last_prune > 3600:
            db.prune_readings()
            self.last_prune = now
        self.set_connection()
        self.refresh_current()

    def _send_async(self, s, sensor, values):
        payload = sender.build_payload(values, sensor)

        def work():
            try:
                if s["send_mode"] == "api":
                    if not s["api_key"]:
                        msg = "Нет ключа: подключите программу к сайту в Настройках"
                    else:
                        code, text = sender.send_api(s["send_url"], s["api_key"], payload)
                        msg = f"Сайт: ответ {code}" + ("" if code < 300 else f", {text}")
                        if code == 401:
                            self.queue.put(("send_revoked",))
                elif s["send_mode"] == "file":
                    sender.write_json_file(s["file_path"], payload)
                    msg = f"JSON записан: {payload['sensor_id']}"
                else:
                    msg = "Отправка выключена"
            except Exception as err:
                msg = f"Ошибка отправки: {err}"
            self.queue.put(("send", msg))

        threading.Thread(target=work, daemon=True).start()


if __name__ == "__main__":
    App().mainloop()
