from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any, Callable

import requests
import minecraft_launcher_lib
from PySide6.QtCore import Qt, QThread, Signal, QUrl
from PySide6.QtGui import QIcon, QDesktopServices, QPixmap
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QPushButton, QComboBox, QLineEdit, QListWidget, QListWidgetItem,
    QStackedWidget, QMessageBox, QProgressBar, QFrame, QDialog, QFileDialog,
    QSpinBox, QCheckBox, QSplitter
)

APP_NAME = "NovaCraft Launcher"
APP_VERSION = "0.2.0"
APP_USER_AGENT = "NovaCraftLauncher/0.2.0 (desktop launcher)"
ROOT = Path(os.getenv("APPDATA") or Path.home()) / "NovaCraft"
RUNTIME_DIR = ROOT / "runtime"
INSTANCES_DIR = ROOT / "instances"
SKINS_DIR = ROOT / "skins"
CONFIG = ROOT / "config.json"
VERSION_CACHE = ROOT / "version-cache.json"
ELY_CLIENT_TOKEN = "novacraft-" + str(uuid.uuid5(uuid.NAMESPACE_DNS, "novacraft-launcher"))
CURSEFORGE_GAME_ID = 432


def ensure_dirs() -> None:
    for folder in (ROOT, RUNTIME_DIR, INSTANCES_DIR, SKINS_DIR):
        folder.mkdir(parents=True, exist_ok=True)


def load_config() -> dict[str, Any]:
    ensure_dirs()
    try:
        cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
        if not isinstance(cfg, dict):
            raise ValueError("Invalid config")
    except Exception:
        cfg = {}
    cfg.setdefault("account", None)
    cfg.setdefault("curseforge_key", "")
    cfg.setdefault("ram_gb", 4)
    cfg.setdefault("performance_args", True)
    cfg.setdefault("java_path", "")
    cfg.setdefault("skins", {})
    cfg.setdefault("instances", [])
    cfg.setdefault("selected_instance", "")
    if not cfg["instances"]:
        default_id = str(uuid.uuid4())
        cfg["instances"] = [{
            "id": default_id,
            "name": "Моя сборка",
            "version": "1.21.11",
            "loader": "Vanilla",
            "loader_version": "latest",
        }]
        cfg["selected_instance"] = default_id
        save_config(cfg)
    elif not cfg.get("selected_instance") or not any(i.get("id") == cfg["selected_instance"] for i in cfg["instances"]):
        cfg["selected_instance"] = cfg["instances"][0].get("id", "")
    return cfg


def save_config(cfg: dict[str, Any]) -> None:
    ensure_dirs()
    CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def protect_token(token: str) -> str:
    if not token:
        return ""
    try:
        import win32crypt  # type: ignore
        blob = win32crypt.CryptProtectData(token.encode("utf-8"), "NovaCraft Ely.by token", None, None, None, 0)[1]
        return "dpapi:" + base64.b64encode(blob).decode("ascii")
    except Exception:
        # Windows builds include pywin32 and use DPAPI; this is a development fallback.
        return "plain:" + base64.b64encode(token.encode("utf-8")).decode("ascii")


def reveal_token(value: str) -> str:
    if not value:
        return ""
    if value.startswith("dpapi:"):
        try:
            import win32crypt  # type: ignore
            return win32crypt.CryptUnprotectData(base64.b64decode(value[6:]), None, None, None, 0)[1].decode("utf-8")
        except Exception as exc:
            raise RuntimeError("Не удалось расшифровать токен Ely.by в Windows. Войди в аккаунт повторно.") from exc
    if value.startswith("plain:"):
        return base64.b64decode(value[6:]).decode("utf-8")
    return value


def offline_uuid(username: str) -> str:
    digest = bytearray(hashlib.md5(("OfflinePlayer:" + username).encode("utf-8")).digest())
    digest[6] = (digest[6] & 0x0F) | 0x30
    digest[8] = (digest[8] & 0x3F) | 0x80
    return str(uuid.UUID(bytes=bytes(digest)))


def ely_auth(username: str, password: str) -> dict[str, str]:
    response = requests.post(
        "https://authserver.ely.by/auth/authenticate",
        json={"username": username, "password": password, "clientToken": ELY_CLIENT_TOKEN, "requestUser": True},
        timeout=25,
    )
    try:
        data = response.json()
    except ValueError as exc:
        raise RuntimeError(f"Ely.by вернул непонятный ответ (HTTP {response.status_code}).") from exc
    if not response.ok:
        raise RuntimeError(data.get("errorMessage") or data.get("error") or f"Ошибка авторизации Ely.by ({response.status_code})")
    profile = data.get("selectedProfile") or {}
    if not profile.get("name") or not profile.get("id") or not data.get("accessToken"):
        raise RuntimeError("Ely.by не вернул игровой профиль. Проверь аккаунт и выбранный профиль.")
    return {
        "type": "ely",
        "username": profile["name"],
        "uuid": profile["id"],
        "token": protect_token(data["accessToken"]),
    }


def profile_dir(instance: dict[str, Any]) -> Path:
    folder = INSTANCES_DIR / re.sub(r"[^a-zA-Z0-9_-]", "", str(instance.get("id", "")))
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def instance_label(instance: dict[str, Any]) -> str:
    loader = instance.get("loader", "Vanilla")
    return f"{instance.get('name', 'Сборка')}  ·  {instance.get('version', '?')}  ·  {loader}"


class Worker(QThread):
    progress = Signal(int, str)
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, fn: Callable[[Callable[[int, str], None]], Any]):
        super().__init__()
        self.fn = fn

    def run(self) -> None:
        try:
            result = self.fn(lambda value=0, message="": self.progress.emit(int(value), str(message)))
            self.done.emit(result)
        except Exception as exc:
            self.failed.emit(str(exc))


class SkinPreview(QWidget):
    """Lazy embedded 3D viewer; skinview3d supplies the draggable model."""
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._view = None
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self.placeholder = QLabel("3D-ПРОСМОТР СКИНА\n\nЗагрузи PNG-скин, чтобы посмотреть модель.\nМодель можно вращать мышью.")
        self.placeholder.setObjectName("skinPlaceholder")
        self.placeholder.setAlignment(Qt.AlignCenter)
        self.placeholder.setMinimumHeight(360)
        self._layout.addWidget(self.placeholder)

    def show_skin(self, path: str | None) -> None:
        if not path or not Path(path).is_file():
            if self._view is not None:
                self._view.setHtml(self._html(None), QUrl("https://novacraft.local/"))
            else:
                self.placeholder.setText("3D-ПРОСМОТР СКИНА\n\nЗагрузи PNG-скин, чтобы посмотреть модель.\nМодель можно вращать мышью.")
            return
        try:
            raw = Path(path).read_bytes()
            source = "data:image/png;base64," + base64.b64encode(raw).decode("ascii")
        except OSError as exc:
            self.placeholder.setText(f"Не удалось открыть скин:\n{exc}")
            return
        if self._view is None:
            try:
                from PySide6.QtWebEngineWidgets import QWebEngineView
                self._view = QWebEngineView(self)
                self._view.setMinimumHeight(360)
                self._layout.removeWidget(self.placeholder)
                self.placeholder.hide()
                self._layout.addWidget(self._view)
            except Exception as exc:
                self.placeholder.setText(f"Не удалось запустить 3D-просмотр:\n{exc}")
                return
        self._view.setHtml(self._html(source), QUrl("https://novacraft.local/"))

    @staticmethod
    def _html(source: str | None) -> str:
        skin_json = json.dumps(source or "")
        html = r'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<style>html,body{margin:0;width:100%;height:100%;overflow:hidden;background:#0d0f18;color:#b7b8c7;font:14px 'Segoe UI',sans-serif}#wrap{height:100%;display:flex;align-items:center;justify-content:center}canvas{max-width:100%;max-height:100%;outline:none}#msg{position:absolute;bottom:16px;left:0;right:0;text-align:center;color:#777d91;font-size:12px;pointer-events:none}</style>
<script src="https://cdn.jsdelivr.net/npm/skinview3d@3.4.2/bundles/skinview3d.bundle.js"></script></head>
<body><div id="wrap"><canvas id="skin" width="360" height="420"></canvas><div id="msg"></div></div><script>
const skinSource=__SKIN_JSON__;
function waitForSkin(){
 if(!window.skinview3d){document.getElementById('msg').textContent='Для 3D-просмотра требуется подключение к интернету';return;}
 if(!skinSource){document.getElementById('msg').textContent='Скин ещё не загружен';return;}
 try{const canvas=document.getElementById('skin');const viewer=new skinview3d.SkinViewer({canvas:canvas,width:360,height:420,skin:skinSource,background:0x0d0f18});viewer.controls.enableRotate=true;viewer.controls.enableZoom=false;viewer.controls.autoRotate=true;viewer.controls.autoRotateSpeed=0.7;viewer.camera.position.set(28,18,38);if(skinview3d.IdleAnimation)viewer.animation=new skinview3d.IdleAnimation();document.getElementById('msg').textContent='Перетаскивай мышью, чтобы вращать модель';}
 catch(e){document.getElementById('msg').textContent='Не удалось отобразить скин';}
}
window.addEventListener('load',waitForSkin);
</script></body></html>'''
        return html.replace("__SKIN_JSON__", skin_json)


class LoginDialog(QDialog):
    logged_in = Signal(object)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Вход через Ely.by")
        self.setMinimumWidth(430)
        layout = QVBoxLayout(self)
        title = QLabel("Аккаунт Ely.by")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)
        note = QLabel("Твои данные отправляются на сервер авторизации Ely.by. Пароль лаунчер не сохраняет.")
        note.setObjectName("muted")
        note.setWordWrap(True)
        layout.addWidget(note)
        form = QFormLayout()
        self.login_input = QLineEdit()
        self.login_input.setPlaceholderText("E-mail или логин Ely.by")
        self.password_input = QLineEdit()
        self.password_input.setEchoMode(QLineEdit.Password)
        self.password_input.setPlaceholderText("Пароль")
        form.addRow("Логин", self.login_input)
        form.addRow("Пароль", self.password_input)
        layout.addLayout(form)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        row = QHBoxLayout()
        row.addStretch()
        cancel = QPushButton("Отмена")
        cancel.clicked.connect(self.reject)
        self.login_button = QPushButton("Войти")
        self.login_button.setObjectName("primary")
        self.login_button.clicked.connect(self.login)
        row.addWidget(cancel)
        row.addWidget(self.login_button)
        layout.addLayout(row)

    def login(self) -> None:
        username = self.login_input.text().strip()
        password = self.password_input.text()
        if not username or not password:
            self.status.setText("Заполни логин и пароль.")
            return
        self.login_button.setEnabled(False)
        self.status.setText("Подключаюсь к Ely.by…")

        def task(_report):
            return ely_auth(username, password)

        self.worker = Worker(task)
        self.worker.done.connect(self._success)
        self.worker.failed.connect(self._failed)
        self.worker.start()

    def _success(self, account: dict[str, str]) -> None:
        self.logged_in.emit(account)
        self.accept()

    def _failed(self, message: str) -> None:
        self.login_button.setEnabled(True)
        self.status.setText(message)


class OfflineDialog(QDialog):
    saved = Signal(object)

    def __init__(self, existing: dict[str, Any] | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Офлайн-профиль")
        self.setMinimumWidth(420)
        layout = QVBoxLayout(self)
        title = QLabel("Играть без авторизации")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)
        desc = QLabel("Офлайн-ник подходит для одиночной игры и серверов, разрешающих офлайн-вход. Он не подтверждает владение Minecraft.")
        desc.setWordWrap(True)
        desc.setObjectName("muted")
        layout.addWidget(desc)
        form = QFormLayout()
        self.nick = QLineEdit((existing or {}).get("username", ""))
        self.nick.setPlaceholderText("Например, Alex_123")
        self.nick.setMaxLength(16)
        self.nick.setToolTip("От 3 до 16 символов: латинские буквы, цифры и знак подчёркивания.")
        form.addRow("Ник", self.nick)
        layout.addLayout(form)
        self.skin_note = QLabel("Скин можно загрузить во вкладке «Аккаунт и скин». 3D-просмотр работает в лаунчере; для отображения скина внутри игры нужен подходящий серверный/клиентский механизм.")
        self.skin_note.setWordWrap(True)
        self.skin_note.setObjectName("muted")
        layout.addWidget(self.skin_note)
        self.error = QLabel("")
        self.error.setObjectName("error")
        layout.addWidget(self.error)
        row = QHBoxLayout()
        row.addStretch()
        cancel = QPushButton("Отмена")
        cancel.clicked.connect(self.reject)
        save = QPushButton("Сохранить профиль")
        save.setObjectName("primary")
        save.clicked.connect(self.save_profile)
        row.addWidget(cancel)
        row.addWidget(save)
        layout.addLayout(row)

    def save_profile(self) -> None:
        name = self.nick.text().strip()
        if not re.fullmatch(r"[A-Za-z0-9_]{3,16}", name):
            self.error.setText("Ник должен содержать 3–16 латинских букв, цифр или _.")
            return
        self.saved.emit({"type": "offline", "username": name, "uuid": offline_uuid(name), "token": ""})
        self.accept()


class InstanceEditorDialog(QDialog):
    saved = Signal(object)

    def __init__(self, instance: dict[str, Any] | None, versions: list[dict[str, Any]], parent: QWidget | None = None):
        super().__init__(parent)
        self.instance = dict(instance or {})
        self.versions = versions
        self.setWindowTitle("Настроить сборку" if instance else "Новая сборка")
        self.resize(690, 560)
        root = QVBoxLayout(self)
        title = QLabel("Параметры сборки")
        title.setObjectName("dialogTitle")
        root.addWidget(title)
        split = QSplitter(Qt.Horizontal)
        left = QWidget()
        left_l = QVBoxLayout(left)
        left_l.setContentsMargins(0, 0, 8, 0)
        left_l.addWidget(QLabel("1. Название сборки"))
        self.name = QLineEdit(self.instance.get("name", ""))
        self.name.setPlaceholderText("Например, Survival + Sodium")
        left_l.addWidget(self.name)
        left_l.addSpacing(8)
        left_l.addWidget(QLabel("2. Версия Minecraft"))
        self.version_search = QLineEdit()
        self.version_search.setPlaceholderText("Фильтр: 1.21, snapshot…")
        left_l.addWidget(self.version_search)
        self.version_list = QListWidget()
        self.version_list.setMinimumWidth(250)
        left_l.addWidget(self.version_list, 1)
        self.selected_version = QLabel("Выбрана версия: —")
        self.selected_version.setObjectName("muted")
        left_l.addWidget(self.selected_version)
        split.addWidget(left)
        right = QWidget()
        right_l = QVBoxLayout(right)
        right_l.setContentsMargins(8, 0, 0, 0)
        right_l.addWidget(QLabel("3. Загрузчик модов"))
        self.loader = QComboBox()
        self.loader.addItems(["Vanilla", "Fabric", "Forge", "NeoForge", "Quilt"])
        self.loader.setCurrentText(self.instance.get("loader", "Vanilla"))
        right_l.addWidget(self.loader)
        right_l.addSpacing(14)
        right_l.addWidget(QLabel("Сборка — отдельный профиль"))
        tips = QLabel("У каждой сборки своя папка с модами, настройками, мирами и ресурс-паками. Общие игровые библиотеки используются совместно, чтобы экономить место на диске.")
        tips.setWordWrap(True)
        tips.setObjectName("muted")
        right_l.addWidget(tips)
        right_l.addStretch()
        split.addWidget(right)
        split.setSizes([390, 240])
        root.addWidget(split, 1)
        self.error = QLabel("")
        self.error.setObjectName("error")
        root.addWidget(self.error)
        row = QHBoxLayout()
        row.addStretch()
        cancel = QPushButton("Отмена")
        cancel.clicked.connect(self.reject)
        save = QPushButton("Сохранить сборку")
        save.setObjectName("primary")
        save.clicked.connect(self.save_instance)
        row.addWidget(cancel)
        row.addWidget(save)
        root.addLayout(row)
        self.version_search.textChanged.connect(self.filter_versions)
        self.version_list.currentItemChanged.connect(self.version_selected)
        self.fill_versions()

    def fill_versions(self) -> None:
        current = self.instance.get("version")
        if not self.versions and current:
            item = QListWidgetItem(current + "  ·  сохранённая версия")
            item.setData(Qt.UserRole, current)
            self.version_list.addItem(item)
        else:
            for v in self.versions:
                version_id = v.get("id", "")
                typ = v.get("type", "release")
                label = {"release": "Релиз", "snapshot": "Снапшот", "old_beta": "Бета", "old_alpha": "Альфа"}.get(typ, typ)
                item = QListWidgetItem(f"{version_id}   ·   {label}")
                item.setData(Qt.UserRole, version_id)
                item.setData(Qt.UserRole + 1, typ)
                self.version_list.addItem(item)
        self.filter_versions(self.version_search.text())
        if current:
            for i in range(self.version_list.count()):
                item = self.version_list.item(i)
                if item.data(Qt.UserRole) == current:
                    self.version_list.setCurrentItem(item)
                    break
        if self.version_list.currentItem() is None and self.version_list.count():
            first_release = next((self.version_list.item(i) for i in range(self.version_list.count()) if self.version_list.item(i).data(Qt.UserRole + 1) == "release"), self.version_list.item(0))
            self.version_list.setCurrentItem(first_release)

    def filter_versions(self, query: str) -> None:
        q = query.strip().lower()
        for i in range(self.version_list.count()):
            item = self.version_list.item(i)
            item.setHidden(bool(q) and q not in item.text().lower())

    def version_selected(self, current, _previous=None) -> None:
        if current:
            self.selected_version.setText("Выбрана версия: " + str(current.data(Qt.UserRole)))

    def save_instance(self) -> None:
        name = self.name.text().strip()
        selected = self.version_list.currentItem()
        version = selected.data(Qt.UserRole) if selected else self.instance.get("version", "")
        if not name:
            self.error.setText("Дай сборке понятное название.")
            return
        if not version or not re.fullmatch(r"[A-Za-z0-9._-]+", str(version)):
            self.error.setText("Выбери версию Minecraft из списка.")
            return
        result = {
            "id": self.instance.get("id") or str(uuid.uuid4()),
            "name": name,
            "version": str(version),
            "loader": self.loader.currentText(),
            "loader_version": "latest",
        }
        self.saved.emit(result)
        self.accept()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        ensure_dirs()
        self.cfg = load_config()
        self.versions: list[dict[str, Any]] = self._load_cached_versions()
        self.setWindowTitle(f"{APP_NAME}  ·  {APP_VERSION}")
        self.setMinimumSize(1020, 680)
        self.resize(1240, 790)
        icon_path = Path(__file__).with_name("icon.ico")
        if icon_path.is_file(): self.setWindowIcon(QIcon(str(icon_path)))
        self._mod_results: list[dict[str, Any]] = []
        self._build_ui()
        self.refresh_instance_combo()
        self.update_account_summary()
        self.start_version_refresh()

    def _load_cached_versions(self) -> list[dict[str, Any]]:
        try:
            data = json.loads(VERSION_CACHE.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except Exception:
            return []

    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        nav = QFrame()
        nav.setObjectName("nav")
        nav.setFixedWidth(230)
        nav_l = QVBoxLayout(nav)
        nav_l.setContentsMargins(18, 22, 18, 18)
        nav_l.setSpacing(8)
        logo_row = QHBoxLayout()
        logo = QLabel()
        icon_path = Path(__file__).with_name("icon.png")
        if icon_path.exists(): logo.setPixmap(QPixmap(str(icon_path)).scaled(48, 48, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        logo_text = QLabel("NOVA\nCRAFT")
        logo_text.setObjectName("logo")
        logo_row.addWidget(logo)
        logo_row.addWidget(logo_text, 1)
        nav_l.addLayout(logo_row)
        subtitle = QLabel("MINECRAFT JAVA LAUNCHER")
        subtitle.setObjectName("sub")
        nav_l.addWidget(subtitle)
        nav_l.addSpacing(28)
        self.nav_buttons: list[QPushButton] = []
        nav_items = [("⌂", "Главная"), ("▦", "Сборки"), ("✦", "Моды"), ("◉", "Аккаунт и скин"), ("⚙", "Настройки")]
        for icon_text, name in nav_items:
            button = QPushButton(f"{icon_text}    {name}")
            button.setObjectName("navButton")
            button.setCheckable(True)
            button.setMinimumHeight(44)
            button.clicked.connect(lambda _checked=False, n=name: self.switch_page(n))
            nav_l.addWidget(button)
            self.nav_buttons.append(button)
        nav_l.addStretch(1)
        self.account_nav = QPushButton("Войти / профиль")
        self.account_nav.clicked.connect(lambda: self.switch_page("Аккаунт и скин"))
        nav_l.addWidget(self.account_nav)
        self.version_state = QLabel("Проверяю версии Minecraft…")
        self.version_state.setWordWrap(True)
        self.version_state.setObjectName("navFootnote")
        nav_l.addWidget(self.version_state)
        outer.addWidget(nav)
        right = QWidget()
        right_l = QVBoxLayout(right)
        right_l.setContentsMargins(0, 0, 0, 0)
        right_l.setSpacing(0)
        topbar = QFrame()
        topbar.setObjectName("topbar")
        top_l = QHBoxLayout(topbar)
        top_l.setContentsMargins(28, 12, 28, 12)
        self.top_profile = QLabel("Офлайн-профиль не выбран")
        self.top_profile.setObjectName("topProfile")
        top_l.addWidget(self.top_profile)
        top_l.addStretch()
        self.top_build_combo = QComboBox()
        self.top_build_combo.setMinimumWidth(240)
        self.top_build_combo.currentIndexChanged.connect(self.select_instance_from_combo)
        top_l.addWidget(QLabel("Активная сборка"))
        top_l.addWidget(self.top_build_combo)
        right_l.addWidget(topbar)
        self.pages = QStackedWidget()
        right_l.addWidget(self.pages, 1)
        outer.addWidget(right, 1)
        self.home_page = self._make_home_page()
        self.builds_page = self._make_builds_page()
        self.mods_page = self._make_mods_page()
        self.account_page = self._make_account_page()
        self.settings_page = self._make_settings_page()
        for page in (self.home_page, self.builds_page, self.mods_page, self.account_page, self.settings_page): self.pages.addWidget(page)
        self.switch_page("Главная")

    def _header(self, title: str, desc: str) -> QWidget:
        w = QWidget()
        l = QVBoxLayout(w)
        l.setContentsMargins(30, 28, 30, 15)
        l.setSpacing(5)
        a = QLabel(title)
        a.setObjectName("pageTitle")
        b = QLabel(desc)
        b.setObjectName("pageDesc")
        b.setWordWrap(True)
        l.addWidget(a)
        l.addWidget(b)
        return w

    def _card(self, name: str = "card") -> QFrame:
        card = QFrame()
        card.setObjectName(name)
        return card

    def _make_home_page(self) -> QWidget:
        w = QWidget()
        l = QVBoxLayout(w)
        l.setContentsMargins(0, 0, 0, 22)
        l.addWidget(self._header("Твоя Minecraft", "Быстрый запуск игры, отдельные профили и чистое управление модами."))
        hero = self._card("heroCard")
        hl = QVBoxLayout(hero)
        hl.setContentsMargins(28, 26, 28, 26)
        hl.setSpacing(14)
        self.hero_title = QLabel("ГОТОВ К ИГРЕ")
        self.hero_title.setObjectName("heroTitle")
        hl.addWidget(self.hero_title)
        self.selected_build_text = QLabel("Моя сборка · Minecraft")
        self.selected_build_text.setObjectName("heroSubtitle")
        hl.addWidget(self.selected_build_text)
        controls = QHBoxLayout()
        self.home_version = QComboBox()
        self.home_version.setMinimumHeight(44)
        self.home_version.setMinimumWidth(200)
        self.home_version.currentTextChanged.connect(self.version_quick_changed)
        controls.addWidget(self.home_version, 1)
        self.home_loader = QComboBox()
        self.home_loader.addItems(["Vanilla", "Fabric", "Forge", "NeoForge", "Quilt"])
        self.home_loader.setMinimumHeight(44)
        self.home_loader.currentTextChanged.connect(self.loader_quick_changed)
        controls.addWidget(self.home_loader)
        self.play_button = QPushButton("▶   ИГРАТЬ")
        self.play_button.setObjectName("primary")
        self.play_button.setMinimumHeight(48)
        self.play_button.setMinimumWidth(160)
        self.play_button.clicked.connect(self.launch_selected)
        controls.addWidget(self.play_button)
        hl.addLayout(controls)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.hide()
        hl.addWidget(self.progress)
        self.status = QLabel("Выбери профиль и нажми «Играть».")
        self.status.setObjectName("statusLine")
        self.status.setWordWrap(True)
        hl.addWidget(self.status)
        l.addWidget(hero)
        cards = QHBoxLayout()
        cards.setContentsMargins(30, 16, 30, 0)
        cards.setSpacing(14)
        for title, desc, btn_text, page in [
            ("СБОРКИ", "Версии, загрузчики и раздельные папки миров.", "Управлять сборками", "Сборки"),
            ("МОДЫ", "Поиск в Modrinth и CurseForge.", "Открыть каталог", "Моды"),
            ("ПРОФИЛЬ", "Офлайн-ник, Ely.by и 3D-просмотр скина.", "Аккаунт и скин", "Аккаунт и скин"),
        ]:
            c = self._card()
            cl = QVBoxLayout(c)
            cl.setContentsMargins(18, 18, 18, 18)
            t = QLabel(title)
            t.setObjectName("cardTitle")
            d = QLabel(desc)
            d.setWordWrap(True)
            d.setObjectName("muted")
            b = QPushButton(btn_text)
            b.clicked.connect(lambda _=False, p=page: self.switch_page(p))
            cl.addWidget(t)
            cl.addWidget(d, 1)
            cl.addWidget(b)
            cards.addWidget(c, 1)
        l.addLayout(cards)
        l.addStretch(1)
        return w

    def _make_builds_page(self) -> QWidget:
        w = QWidget()
        l = QVBoxLayout(w)
        l.addWidget(self._header("Менеджер сборок", "Сделай отдельную конфигурацию для каждого сервера, модпака или версии Minecraft."))
        row = QHBoxLayout()
        self.build_list = QListWidget()
        self.build_list.setMinimumWidth(300)
        self.build_list.itemDoubleClicked.connect(lambda _item: self.edit_selected_instance())
        row.addWidget(self.build_list, 1)
        detail = self._card()
        detail_l = QVBoxLayout(detail)
        detail_l.setContentsMargins(22, 22, 22, 22)
        self.build_detail_title = QLabel("Выбери сборку")
        self.build_detail_title.setObjectName("cardTitle")
        self.build_detail = QLabel("Каждая сборка получает собственные моды, настройки, ресурс-паки и миры. Игровые библиотеки общие для экономии места.")
        self.build_detail.setWordWrap(True)
        self.build_detail.setObjectName("muted")
        detail_l.addWidget(self.build_detail_title)
        detail_l.addWidget(self.build_detail)
        detail_l.addStretch()
        self.edit_build_button = QPushButton("Изменить параметры")
        self.edit_build_button.clicked.connect(self.edit_selected_instance)
        self.choose_build_button = QPushButton("Сделать активной")
        self.choose_build_button.setObjectName("primary")
        self.choose_build_button.clicked.connect(self.activate_selected_instance)
        detail_l.addWidget(self.edit_build_button)
        detail_l.addWidget(self.choose_build_button)
        row.addWidget(detail, 1)
        row_widget = QWidget(); row_widget.setLayout(row)
        l.addWidget(row_widget, 1)
        buttons = QHBoxLayout()
        add = QPushButton("＋ Создать сборку")
        add.setObjectName("primary")
        add.clicked.connect(self.create_instance)
        delete = QPushButton("Удалить сборку")
        delete.clicked.connect(self.delete_selected_instance)
        buttons.addWidget(add)
        buttons.addWidget(delete)
        buttons.addStretch()
        l.addLayout(buttons)
        self.build_list.currentItemChanged.connect(self.show_build_detail)
        return w

    def _make_mods_page(self) -> QWidget:
        w = QWidget()
        l = QVBoxLayout(w)
        l.addWidget(self._header("Каталог модов", "Дважды кликни по моду, чтобы установить подходящую версию в активную сборку."))
        row = QHBoxLayout()
        self.mod_source = QComboBox()
        self.mod_source.addItems(["Modrinth", "CurseForge"])
        self.mod_source.setMinimumWidth(150)
        self.mod_query = QLineEdit()
        self.mod_query.setPlaceholderText("Ищи Sodium, Iris, JEI, Create…")
        self.mod_query.returnPressed.connect(self.search_mods)
        search = QPushButton("Найти моды")
        search.setObjectName("primary")
        search.clicked.connect(self.search_mods)
        row.addWidget(self.mod_source)
        row.addWidget(self.mod_query, 1)
        row.addWidget(search)
        row_widget = QWidget(); row_widget.setLayout(row)
        row_widget.setContentsMargins(30, 0, 30, 12)
        l.addWidget(row_widget)
        self.mods_list = QListWidget()
        self.mods_list.itemDoubleClicked.connect(self.install_selected_mod)
        margins = QWidget(); ml = QVBoxLayout(margins); ml.setContentsMargins(30, 0, 30, 24); ml.addWidget(self.mods_list)
        l.addWidget(margins, 1)
        hint = QLabel("CurseForge требует личный API key — добавь его в настройках. Скачивай только моды, которым доверяешь.")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        hint.setContentsMargins(30, 0, 30, 10)
        l.addWidget(hint)
        return w

    def _make_account_page(self) -> QWidget:
        w = QWidget()
        l = QVBoxLayout(w)
        l.addWidget(self._header("Аккаунт и скин", "Управляй игровым ником и смотри свой скин в 3D прямо в лаунчере."))
        split = QSplitter(Qt.Horizontal)
        info = self._card()
        il = QVBoxLayout(info)
        il.setContentsMargins(22, 22, 22, 22)
        il.setSpacing(12)
        head = QLabel("ТВОЙ ПРОФИЛЬ")
        head.setObjectName("cardTitle")
        il.addWidget(head)
        self.account_type_label = QLabel("Офлайн-профиль")
        self.account_type_label.setObjectName("pill")
        il.addWidget(self.account_type_label)
        self.account_nick_label = QLabel("Не выбран")
        self.account_nick_label.setObjectName("profileNick")
        il.addWidget(self.account_nick_label)
        self.profile_skin_path = QLabel("Скин не загружен")
        self.profile_skin_path.setObjectName("muted")
        self.profile_skin_path.setWordWrap(True)
        il.addWidget(self.profile_skin_path)
        offline = QPushButton("Войти с офлайн-ником")
        offline.clicked.connect(self.open_offline_login)
        ely = QPushButton("Подключить Ely.by")
        ely.clicked.connect(self.open_ely_login)
        logout = QPushButton("Выйти из аккаунта")
        logout.clicked.connect(self.logout_account)
        il.addWidget(offline)
        il.addWidget(ely)
        il.addWidget(logout)
        il.addSpacing(10)
        skin_group = QLabel("ЛОКАЛЬНЫЙ СКИН")
        skin_group.setObjectName("cardTitle")
        il.addWidget(skin_group)
        upload = QPushButton("Загрузить PNG-скин")
        upload.setObjectName("primary")
        upload.clicked.connect(self.upload_skin)
        remove = QPushButton("Убрать скин")
        remove.clicked.connect(self.remove_skin)
        il.addWidget(upload)
        il.addWidget(remove)
        self.skin_loader_button = QPushButton("Установить поддержку скина в игре")
        self.skin_loader_button.clicked.connect(self.install_custom_skin_loader)
        il.addWidget(self.skin_loader_button)
        notice = QLabel("Скин сохранится локально. Для его показа внутри Minecraft можно установить CustomSkinLoader в сборку с мод-загрузчиком. Такой локальный скин виден тебе; другим игрокам он автоматически не передаётся.")
        notice.setObjectName("muted")
        notice.setWordWrap(True)
        il.addWidget(notice)
        il.addStretch()
        preview = self._card()
        pl = QVBoxLayout(preview)
        pl.setContentsMargins(14, 14, 14, 14)
        lbl = QLabel("ПРЕДПРОСМОТР 3D")
        lbl.setObjectName("cardTitle")
        pl.addWidget(lbl)
        self.skin_preview = SkinPreview()
        pl.addWidget(self.skin_preview, 1)
        hint = QLabel("Мышь — вращение модели · текстура хранится локально")
        hint.setObjectName("muted")
        pl.addWidget(hint)
        split.addWidget(info)
        split.addWidget(preview)
        split.setSizes([340, 500])
        l.addWidget(split, 1)
        return w

    def _make_settings_page(self) -> QWidget:
        w = QWidget()
        l = QVBoxLayout(w)
        l.addWidget(self._header("Настройки", "Управляй памятью Java, оптимизацией запуска и путями к файлам."))
        form_card = self._card()
        f = QFormLayout(form_card)
        f.setContentsMargins(24, 24, 24, 24)
        self.ram_spin = QSpinBox()
        self.ram_spin.setRange(2, 32)
        self.ram_spin.setSuffix(" ГБ")
        self.ram_spin.setValue(int(self.cfg.get("ram_gb", 4)))
        f.addRow("Максимальная память Java", self.ram_spin)
        self.performance_check = QCheckBox("Включить безопасные JVM-параметры производительности")
        self.performance_check.setChecked(bool(self.cfg.get("performance_args", True)))
        f.addRow("Оптимизация", self.performance_check)
        java_row = QHBoxLayout()
        self.java_path = QLineEdit(self.cfg.get("java_path", ""))
        self.java_path.setPlaceholderText("Автоматически (рекомендуется)")
        browse_java = QPushButton("Обзор…")
        browse_java.clicked.connect(self.choose_java)
        java_row.addWidget(self.java_path, 1); java_row.addWidget(browse_java)
        java_widget = QWidget(); java_widget.setLayout(java_row)
        f.addRow("Java executable", java_widget)
        key = QHBoxLayout()
        self.cf_key_input = QLineEdit(self.cfg.get("curseforge_key", ""))
        self.cf_key_input.setEchoMode(QLineEdit.Password)
        self.cf_key_input.setPlaceholderText("CurseForge API key")
        key.addWidget(self.cf_key_input, 1)
        key_widget = QWidget(); key_widget.setLayout(key)
        f.addRow("CurseForge", key_widget)
        l.addWidget(form_card)
        row = QHBoxLayout()
        save = QPushButton("Сохранить настройки")
        save.setObjectName("primary")
        save.clicked.connect(self.save_settings)
        open_game = QPushButton("Открыть папку игровых файлов")
        open_game.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(RUNTIME_DIR))))
        open_app = QPushButton("Открыть папку NovaCraft")
        open_app.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(ROOT))))
        row.addWidget(save); row.addWidget(open_game); row.addWidget(open_app)
        row_widget = QWidget(); row_widget.setLayout(row); row_widget.setContentsMargins(30, 0, 30, 10)
        l.addWidget(row_widget)
        perf_note = QLabel("Оптимизация не обещает универсальный прирост FPS: параметры JVM безопасные и умеренные. Реальный FPS зависит от системы, драйвера, Minecraft и модов.")
        perf_note.setObjectName("muted"); perf_note.setWordWrap(True); perf_note.setContentsMargins(30, 0, 30, 0)
        l.addWidget(perf_note)
        l.addStretch()
        return w

    def switch_page(self, name: str) -> None:
        mapping = {"Главная": 0, "Сборки": 1, "Моды": 2, "Аккаунт и скин": 3, "Настройки": 4}
        self.pages.setCurrentIndex(mapping[name])
        for i, button in enumerate(self.nav_buttons): button.setChecked(i == mapping[name])
        if name == "Сборки": self.refresh_build_list()
        if name == "Аккаунт и скин": self.update_account_summary(); self.load_selected_skin()

    def current_instance(self) -> dict[str, Any]:
        iid = self.cfg.get("selected_instance")
        return next((x for x in self.cfg["instances"] if x.get("id") == iid), self.cfg["instances"][0])

    def refresh_instance_combo(self) -> None:
        self.top_build_combo.blockSignals(True)
        self.top_build_combo.clear()
        for inst in self.cfg["instances"]: self.top_build_combo.addItem(instance_label(inst), inst["id"])
        idx = self.top_build_combo.findData(self.cfg.get("selected_instance"))
        self.top_build_combo.setCurrentIndex(max(idx, 0))
        self.top_build_combo.blockSignals(False)
        self.sync_instance_to_home()
        self.refresh_build_list()

    def sync_instance_to_home(self) -> None:
        inst = self.current_instance()
        self.selected_build_text.setText(instance_label(inst))
        self.home_version.blockSignals(True); self.home_loader.blockSignals(True)
        self.home_version.clear()
        for v in self.versions:
            self.home_version.addItem(v.get("id", ""), v.get("type", "release"))
        if self.home_version.findText(inst.get("version", "")) < 0 and inst.get("version"):
            self.home_version.addItem(inst["version"], "saved")
        self.home_version.setCurrentText(inst.get("version", ""))
        self.home_loader.setCurrentText(inst.get("loader", "Vanilla"))
        self.home_version.blockSignals(False); self.home_loader.blockSignals(False)

    def select_instance_from_combo(self, index: int) -> None:
        if index < 0: return
        iid = self.top_build_combo.itemData(index)
        if iid:
            self.cfg["selected_instance"] = iid
            save_config(self.cfg)
            self.sync_instance_to_home()
            self.refresh_build_list()

    def version_quick_changed(self, version: str) -> None:
        if not version: return
        inst = self.current_instance()
        if inst.get("version") != version:
            inst["version"] = version
            save_config(self.cfg)
            self.selected_build_text.setText(instance_label(inst))
            self.refresh_instance_combo()

    def loader_quick_changed(self, loader: str) -> None:
        if not loader: return
        inst = self.current_instance()
        if inst.get("loader") != loader:
            inst["loader"] = loader
            save_config(self.cfg)
            self.selected_build_text.setText(instance_label(inst))
            self.refresh_instance_combo()

    def start_version_refresh(self) -> None:
        self.version_state.setText("Обновляю список версий в фоне…")
        def task(report):
            report(5, "Запрашиваю manifest версий Minecraft…")
            return minecraft_launcher_lib.utils.get_version_list()
        self.version_worker = Worker(task)
        self.version_worker.done.connect(self._versions_loaded)
        self.version_worker.failed.connect(self._versions_failed)
        self.version_worker.start()

    def _versions_loaded(self, versions: list[dict[str, Any]]) -> None:
        self.versions = versions
        try: VERSION_CACHE.write_text(json.dumps(versions, ensure_ascii=False, default=str), encoding="utf-8")
        except OSError: pass
        if versions:
            latest = next((x["id"] for x in versions if x.get("type") == "release"), versions[0]["id"])
            inst = self.current_instance()
            if not inst.get("version") or (inst.get("version") == "1.21.11" and not any(v.get("id") == "1.21.11" for v in versions)):
                inst["version"] = latest
                save_config(self.cfg)
        self.version_state.setText(f"Доступно версий: {len(versions)}")
        self.sync_instance_to_home()
        self.refresh_build_list()
        self.status.setText("Список версий обновлён. Игровые файлы будут скачаны при первом запуске сборки.")

    def _versions_failed(self, message: str) -> None:
        if self.versions:
            self.version_state.setText(f"Кэш версий: {len(self.versions)}")
            self.sync_instance_to_home()
        else:
            self.version_state.setText("Нет подключения к manifest Minecraft")
        self.status.setText("Не удалось обновить версии: " + message)

    def refresh_build_list(self) -> None:
        if not hasattr(self, "build_list"): return
        selected = self.cfg.get("selected_instance")
        self.build_list.clear()
        for inst in self.cfg["instances"]:
            item = QListWidgetItem(instance_label(inst))
            item.setData(Qt.UserRole, inst["id"])
            self.build_list.addItem(item)
            if inst["id"] == selected: self.build_list.setCurrentItem(item)
        if self.build_list.currentItem() is None and self.build_list.count(): self.build_list.setCurrentRow(0)

    def selected_build_from_list(self) -> dict[str, Any] | None:
        item = self.build_list.currentItem() if hasattr(self, "build_list") else None
        iid = item.data(Qt.UserRole) if item else None
        return next((x for x in self.cfg["instances"] if x.get("id") == iid), None)

    def show_build_detail(self, current, _previous=None) -> None:
        if not current: return
        inst = next((x for x in self.cfg["instances"] if x.get("id") == current.data(Qt.UserRole)), None)
        if not inst: return
        self.build_detail_title.setText(inst.get("name", "Сборка"))
        directory = profile_dir(inst)
        mods_count = len(list((directory / "mods").glob("*.jar"))) if (directory / "mods").exists() else 0
        self.build_detail.setText(f"Minecraft: {inst.get('version')}\nЗагрузчик: {inst.get('loader')}\nМоды: {mods_count}\nПапка профиля: {directory}\n\nДважды щёлкни по сборке слева или нажми «Изменить параметры».")

    def create_instance(self) -> None:
        dlg = InstanceEditorDialog(None, self.versions, self)
        dlg.saved.connect(self._add_instance)
        dlg.exec()

    def _add_instance(self, inst: dict[str, Any]) -> None:
        self.cfg["instances"].append(inst)
        self.cfg["selected_instance"] = inst["id"]
        save_config(self.cfg)
        profile_dir(inst)
        self.refresh_instance_combo()

    def edit_selected_instance(self) -> None:
        inst = self.selected_build_from_list()
        if not inst: return
        dlg = InstanceEditorDialog(inst, self.versions, self)
        dlg.saved.connect(self._update_instance)
        dlg.exec()

    def _update_instance(self, updated: dict[str, Any]) -> None:
        self.cfg["instances"] = [updated if x.get("id") == updated.get("id") else x for x in self.cfg["instances"]]
        save_config(self.cfg)
        self.refresh_instance_combo()

    def activate_selected_instance(self) -> None:
        inst = self.selected_build_from_list()
        if not inst: return
        self.cfg["selected_instance"] = inst["id"]
        save_config(self.cfg)
        self.refresh_instance_combo()

    def delete_selected_instance(self) -> None:
        inst = self.selected_build_from_list()
        if not inst: return
        if len(self.cfg["instances"]) <= 1:
            QMessageBox.warning(self, "Нельзя удалить", "Оставь хотя бы одну сборку.")
            return
        answer = QMessageBox.question(self, "Удалить сборку", f"Удалить «{inst['name']}» из списка?\nМиры и моды на диске останутся.")
        if answer != QMessageBox.Yes: return
        self.cfg["instances"] = [x for x in self.cfg["instances"] if x["id"] != inst["id"]]
        if self.cfg["selected_instance"] == inst["id"]: self.cfg["selected_instance"] = self.cfg["instances"][0]["id"]
        save_config(self.cfg)
        self.refresh_instance_combo()

    def update_account_summary(self) -> None:
        account = self.cfg.get("account")
        if not account:
            self.account_type_label.setText("АККАУНТ НЕ ВЫБРАН")
            self.account_nick_label.setText("Гость")
            self.top_profile.setText("Аккаунт не выбран")
            self.account_nav.setText("Войти / профиль")
            skin = None
        else:
            typ = account.get("type", "offline")
            self.account_type_label.setText("ОФЛАЙН-ПРОФИЛЬ" if typ == "offline" else "ELY.BY")
            self.account_nick_label.setText(account.get("username", "Player"))
            self.top_profile.setText(f"Игрок: {account.get('username', 'Player')}")
            self.account_nav.setText(account.get("username", "Профиль"))
            skin = self.cfg.get("skins", {}).get(account.get("username", ""))
        self.profile_skin_path.setText(Path(skin).name if skin else "Скин не загружен — добавь свой PNG")

    def open_offline_login(self) -> None:
        dlg = OfflineDialog(self.cfg.get("account"), self)
        dlg.saved.connect(self._set_account)
        dlg.exec()

    def open_ely_login(self) -> None:
        dlg = LoginDialog(self)
        dlg.logged_in.connect(self._set_account)
        dlg.exec()

    def _set_account(self, account: dict[str, Any]) -> None:
        self.cfg["account"] = account
        save_config(self.cfg)
        self.update_account_summary()
        self.load_selected_skin()
        self.status.setText(f"Профиль активен: {account['username']}")

    def logout_account(self) -> None:
        if not self.cfg.get("account"): return
        if QMessageBox.question(self, "Выйти", "Отключить текущий игровой профиль?") == QMessageBox.Yes:
            self.cfg["account"] = None
            save_config(self.cfg)
            self.update_account_summary()
            self.skin_preview.show_skin(None)

    def current_skin_path(self) -> str | None:
        account = self.cfg.get("account")
        if not account: return None
        path = self.cfg.get("skins", {}).get(account.get("username", ""))
        return path if path and Path(path).is_file() else None

    def load_selected_skin(self) -> None:
        if hasattr(self, "skin_preview"):
            self.skin_preview.show_skin(self.current_skin_path())

    def upload_skin(self) -> None:
        account = self.cfg.get("account")
        if not account:
            QMessageBox.information(self, "Сначала выбери профиль", "Сначала создай офлайн-профиль или войди в Ely.by.")
            self.open_offline_login()
            account = self.cfg.get("account")
            if not account: return
        source, _ = QFileDialog.getOpenFileName(self, "Выбрать Minecraft-скин", str(Path.home()), "PNG image (*.png)")
        if not source: return
        pix = QPixmap(source)
        if pix.isNull():
            QMessageBox.warning(self, "Неверный файл", "Не удалось прочитать PNG.")
            return
        width, height = pix.width(), pix.height()
        scale = width // 64
        if width < 64 or height < 32 or width % 64 != 0 or scale < 1 or height not in (32 * scale, 64 * scale):
            QMessageBox.warning(self, "Размер скина", "Поддерживаются стандартные скины 64×64 или старые 64×32, а также их увеличенные версии (128×128 и т.д.).")
            return
        target = SKINS_DIR / (re.sub(r"[^A-Za-z0-9_]+", "_", account.get("username", "player")) + ".png")
        try:
            target.write_bytes(Path(source).read_bytes())
        except OSError as exc:
            QMessageBox.critical(self, "Ошибка", f"Не удалось сохранить скин: {exc}")
            return
        self.cfg.setdefault("skins", {})[account.get("username", "player")] = str(target)
        save_config(self.cfg)
        self.update_account_summary()
        self.skin_preview.show_skin(str(target))
        self.status.setText("Скин загружен и сохранён для этого профиля.")

    def install_custom_skin_loader(self) -> None:
        account = self.cfg.get("account")
        if not account:
            QMessageBox.information(self, "Сначала выбери профиль", "Войди с офлайн-ником или подключи Ely.by.")
            return
        username = account.get("username", "")
        skin_path = Path(self.cfg.get("skins", {}).get(username, ""))
        if not skin_path.is_file():
            QMessageBox.information(self, "Сначала загрузи скин", "Нажми «Загрузить PNG-скин», затем повтори установку.")
            return
        inst = dict(self.current_instance())
        loader = str(inst.get("loader", "Vanilla"))
        if loader == "Vanilla":
            QMessageBox.information(self, "Нужен загрузчик модов",
                "CustomSkinLoader работает как клиентский мод. Открой «Сборки» и выбери или создай профиль Fabric, Forge, NeoForge или Quilt.")
            self.switch_page("Сборки")
            return
        loader_id = {"Fabric": "fabric", "Quilt": "fabric", "Forge": "forge", "NeoForge": "neoforge"}.get(loader)
        if not loader_id:
            QMessageBox.warning(self, "Загрузчик", f"Неизвестный загрузчик: {loader}")
            return
        self.skin_loader_button.setEnabled(False)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.show()
        self.status.setText("Ищу версию CustomSkinLoader, совместимую с активной сборкой…")

        def task(report):
            version = str(inst.get("version", ""))
            params = {"game_versions": json.dumps([version]), "loaders": json.dumps([loader_id])}
            response = requests.get(
                "https://api.modrinth.com/v2/project/customskinloader/version",
                params=params,
                headers={"User-Agent": APP_USER_AGENT},
                timeout=30,
            )
            response.raise_for_status()
            releases = response.json()
            if not releases and loader == "Quilt":
                # The project publishes Quilt-compatible artifacts under the Fabric loader tag.
                params["loaders"] = json.dumps(["quilt"])
                response = requests.get(
                    "https://api.modrinth.com/v2/project/customskinloader/version",
                    params=params,
                    headers={"User-Agent": APP_USER_AGENT},
                    timeout=30,
                )
                response.raise_for_status()
                releases = response.json()
            if not releases:
                raise RuntimeError(f"Для Minecraft {version} и {loader} совместимый CustomSkinLoader не найден.")
            chosen = releases[0]
            file = next((x for x in chosen.get("files", []) if x.get("primary")), None)
            if file is None and chosen.get("files"):
                file = chosen["files"][0]
            if not file or not file.get("url") or not file.get("filename"):
                raise RuntimeError("Modrinth не предоставил файл CustomSkinLoader.")
            report(35, "Скачиваю CustomSkinLoader…")
            download = requests.get(file["url"], headers={"User-Agent": APP_USER_AGENT}, timeout=90)
            download.raise_for_status()
            mods_dir = profile_dir(inst) / "mods"
            mods_dir.mkdir(parents=True, exist_ok=True)
            jar = mods_dir / Path(file["filename"]).name
            jar.write_bytes(download.content)
            report(85, "Подключаю локальный скин к сборке…")
            local_skin = profile_dir(inst) / "CustomSkinLoader" / "LocalSkin" / "skins" / (username + ".png")
            local_skin.parent.mkdir(parents=True, exist_ok=True)
            local_skin.write_bytes(skin_path.read_bytes())
            return f"Установлен {jar.name}. Локальный скин подключён для ника {username}. Перезапусти игру; другие игроки не получат этот скин автоматически."

        self.skin_loader_worker = Worker(task)
        self.skin_loader_worker.progress.connect(self._on_progress)
        self.skin_loader_worker.done.connect(self._custom_skin_loader_done)
        self.skin_loader_worker.failed.connect(self._custom_skin_loader_failed)
        self.skin_loader_worker.start()

    def _custom_skin_loader_done(self, message: str) -> None:
        self.progress.hide()
        self.skin_loader_button.setEnabled(True)
        self.status.setText(message)
        QMessageBox.information(self, "Поддержка локального скина", message)

    def _custom_skin_loader_failed(self, message: str) -> None:
        self.progress.hide()
        self.skin_loader_button.setEnabled(True)
        self.status.setText("Не удалось установить поддержку скина: " + message)
        QMessageBox.warning(self, "CustomSkinLoader", message)

    def remove_skin(self) -> None:
        account = self.cfg.get("account")
        if not account: return
        self.cfg.setdefault("skins", {}).pop(account.get("username", ""), None)
        save_config(self.cfg)
        self.update_account_summary()
        self.skin_preview.show_skin(None)

    def choose_java(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Выбрать javaw.exe", str(Path.home()), "Java executable (javaw.exe java.exe);;All files (*.*)")
        if path: self.java_path.setText(path)

    def save_settings(self) -> None:
        self.cfg["ram_gb"] = int(self.ram_spin.value())
        self.cfg["performance_args"] = self.performance_check.isChecked()
        self.cfg["java_path"] = self.java_path.text().strip()
        self.cfg["curseforge_key"] = self.cf_key_input.text().strip()
        save_config(self.cfg)
        QMessageBox.information(self, "Настройки сохранены", "Настройки будут использоваться при следующем запуске сборки.")

    def _game_callbacks(self, report: Callable[[int, str], None]) -> dict[str, Callable]:
        return {
            "setStatus": lambda message: report(5, str(message)),
            "setProgress": lambda value: report(min(95, max(0, int(value))), "Скачивание игровых файлов…"),
            "setMax": lambda value: report(5, "Подготовка к скачиванию…"),
        }

    def launch_selected(self) -> None:
        account = self.cfg.get("account")
        if not account:
            QMessageBox.information(self, "Нужен профиль", "Создай офлайн-ник или войди через Ely.by на странице «Аккаунт и скин».")
            self.switch_page("Аккаунт и скин")
            return
        inst = dict(self.current_instance())
        if not inst.get("version"):
            QMessageBox.warning(self, "Версия не выбрана", "Открой «Сборки» и выбери версию Minecraft.")
            return
        self.play_button.setEnabled(False)
        self.progress.setValue(0); self.progress.show()
        self.status.setText(f"Подготовка {inst['name']}… первый запуск может скачать игровые файлы.")

        def task(report):
            RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
            game_dir = profile_dir(inst)
            for name in ("mods", "config", "resourcepacks", "saves", "screenshots"):
                (game_dir / name).mkdir(exist_ok=True)
            # Keep this profile's local skin synced for CustomSkinLoader's LocalSkin provider.
            skin_source = self.cfg.get("skins", {}).get(account.get("username", ""))
            if skin_source and Path(skin_source).is_file() and str(inst.get("loader", "Vanilla")) != "Vanilla":
                local_skin = game_dir / "CustomSkinLoader" / "LocalSkin" / "skins" / (account["username"] + ".png")
                local_skin.parent.mkdir(parents=True, exist_ok=True)
                local_skin.write_bytes(Path(skin_source).read_bytes())
            cb = self._game_callbacks(report)
            loader = str(inst.get("loader", "Vanilla"))
            base_version = str(inst["version"])
            version_id = base_version
            report(2, f"Установка Minecraft {base_version}…")
            if loader == "Vanilla":
                minecraft_launcher_lib.install.install_minecraft_version(base_version, str(RUNTIME_DIR), callback=cb)
            else:
                mod_loader = minecraft_launcher_lib.mod_loader.get_mod_loader(loader.lower())
                result = mod_loader.install(base_version, str(RUNTIME_DIR), callback=cb)
                if isinstance(result, str) and result:
                    version_id = result
                else:
                    versions_path = RUNTIME_DIR / "versions"
                    candidates = []
                    if versions_path.exists():
                        for p in versions_path.iterdir():
                            if p.is_dir() and base_version in p.name and loader.lower() in p.name.lower(): candidates.append(p.name)
                    if not candidates:
                        raise RuntimeError(f"Установщик {loader} не сообщил идентификатор установленной версии.")
                    version_id = sorted(candidates)[-1]
            token = ""
            if account.get("type") == "ely":
                token = reveal_token(account.get("token", ""))
                auth_agent = ROOT / "authlib-injector.jar"
                if not auth_agent.exists():
                    report(92, "Скачиваю компонент авторизации Ely.by…")
                    api = requests.get("https://api.github.com/repos/yushijinhun/authlib-injector/releases/latest", timeout=25).json()
                    assets = api.get("assets", [])
                    asset = next((a for a in assets if a.get("name", "").lower().endswith(".jar")), None)
                    if not asset: raise RuntimeError("Не удалось найти authlib-injector для Ely.by.")
                    with requests.get(asset["browser_download_url"], stream=True, timeout=60) as response:
                        response.raise_for_status()
                        with auth_agent.open("wb") as file:
                            for chunk in response.iter_content(128 * 1024):
                                if chunk: file.write(chunk)
            options: dict[str, Any] = {
                "username": account["username"],
                "uuid": account.get("uuid") or offline_uuid(account["username"]),
                "token": token,
                "launcherName": APP_NAME,
                "launcherVersion": APP_VERSION,
                "gameDirectory": str(game_dir),
                "jvmArguments": [],
            }
            java_path = str(self.cfg.get("java_path", "")).strip()
            if java_path: options["executablePath"] = java_path
            ram = max(2, int(self.cfg.get("ram_gb", 4)))
            jvm_args = ["-Xms1G", f"-Xmx{ram}G"]
            if self.cfg.get("performance_args", True):
                jvm_args += ["-XX:+UseG1GC", "-XX:+ParallelRefProcEnabled", "-XX:MaxGCPauseMillis=100", "-Dfile.encoding=UTF-8"]
            if account.get("type") == "ely":
                jvm_args.insert(0, f"-javaagent:{ROOT / 'authlib-injector.jar'}=https://authserver.ely.by")
            options["jvmArguments"] = jvm_args
            report(98, "Готовлю команду запуска…")
            command = minecraft_launcher_lib.command.get_minecraft_command(version_id, str(RUNTIME_DIR), options)
            return {"command": command, "game_dir": str(game_dir), "version_id": version_id, "instance": inst}

        self.launch_worker = Worker(task)
        self.launch_worker.progress.connect(self._on_progress)
        self.launch_worker.done.connect(self._run_game)
        self.launch_worker.failed.connect(self._launch_failed)
        self.launch_worker.start()

    def _on_progress(self, value: int, message: str) -> None:
        self.progress.setValue(max(0, min(100, value)))
        self.status.setText(message)

    def _run_game(self, result: dict[str, Any]) -> None:
        self.progress.hide()
        self.play_button.setEnabled(True)
        try:
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            subprocess.Popen(result["command"], cwd=result["game_dir"], creationflags=flags)
            self.status.setText(f"Запущено: {result['instance']['name']}  ·  {result['version_id']}")
        except Exception as exc:
            self._launch_failed(str(exc))

    def _launch_failed(self, message: str) -> None:
        self.progress.hide()
        self.play_button.setEnabled(True)
        self.status.setText("Ошибка запуска: " + message)
        QMessageBox.critical(self, "Не удалось запустить Minecraft", message)

    def search_mods(self) -> None:
        query = self.mod_query.text().strip()
        self.mods_list.clear()
        if not query: return
        source = self.mod_source.currentText()
        self.status.setText(f"Поиск в {source}…")
        self._set_mod_controls(False)

        def task(report):
            if source == "Modrinth":
                r = requests.get("https://api.modrinth.com/v2/search", params={"query": query, "facets": json.dumps([["project_type:mod"]]), "limit": 30}, headers={"User-Agent": APP_USER_AGENT}, timeout=25)
                r.raise_for_status()
                return [{"source": "Modrinth", "id": x["project_id"], "title": x["title"], "description": x.get("description", ""), "downloads": x.get("downloads", 0)} for x in r.json().get("hits", [])]
            key = self.cfg.get("curseforge_key", "")
            if not key: raise RuntimeError("Для CurseForge нужен API key. Добавь его в настройках лаунчера.")
            r = requests.get("https://api.curseforge.com/v1/mods/search", params={"gameId": CURSEFORGE_GAME_ID, "classId": 6, "searchFilter": query, "pageSize": 30}, headers={"x-api-key": key, "Accept": "application/json"}, timeout=25)
            r.raise_for_status()
            return [{"source": "CurseForge", "id": x["id"], "title": x["name"], "description": x.get("summary", ""), "downloads": x.get("downloadCount", 0)} for x in r.json().get("data", [])]

        self.mod_worker = Worker(task)
        self.mod_worker.done.connect(self._mods_loaded)
        self.mod_worker.failed.connect(self._mods_failed)
        self.mod_worker.start()

    def _set_mod_controls(self, enabled: bool) -> None:
        self.mod_query.setEnabled(enabled); self.mod_source.setEnabled(enabled)

    def _mods_loaded(self, mods: list[dict[str, Any]]) -> None:
        self._mod_results = mods
        self.mods_list.clear()
        for mod in mods:
            downloads = f"{int(mod.get('downloads', 0)):,}".replace(",", " ")
            item = QListWidgetItem(f"{mod['title']}  ·  {downloads} загрузок\n{mod.get('description', '')[:140]}")
            item.setData(Qt.UserRole, mod)
            self.mods_list.addItem(item)
        self._set_mod_controls(True)
        self.status.setText(f"Найдено модов: {len(mods)}")

    def _mods_failed(self, message: str) -> None:
        self._set_mod_controls(True)
        self.status.setText("Ошибка поиска модов: " + message)
        QMessageBox.warning(self, "Поиск модов", message)

    def install_selected_mod(self, item: QListWidgetItem) -> None:
        mod = item.data(Qt.UserRole)
        if not mod: return
        inst = dict(self.current_instance())
        if inst.get("loader", "Vanilla") == "Vanilla":
            QMessageBox.warning(self, "Нужен загрузчик", "Сначала выбери Fabric, Forge, NeoForge или Quilt в настройках сборки.")
            self.switch_page("Сборки")
            return
        self._set_mod_controls(False)
        self.status.setText(f"Подбираю файл для {inst['version']} / {inst['loader']}…")

        def task(report):
            if mod["source"] == "Modrinth":
                loader = "neoforge" if inst["loader"] == "NeoForge" else inst["loader"].lower()
                r = requests.get(f"https://api.modrinth.com/v2/project/{mod['id']}/version", params={"game_versions": json.dumps([inst["version"]]), "loaders": json.dumps([loader])}, headers={"User-Agent": APP_USER_AGENT}, timeout=25)
                r.raise_for_status()
                versions = r.json()
                if not versions: raise RuntimeError("Для этой версии Minecraft и загрузчика подходящий файл не найден.")
                files = versions[0].get("files", [])
                selected = next((x for x in files if x.get("primary")), files[0] if files else None)
                if not selected: raise RuntimeError("У проекта нет скачиваемого файла.")
                file_url, filename = selected["url"], selected["filename"]
            else:
                key = self.cfg.get("curseforge_key", "")
                r = requests.get(f"https://api.curseforge.com/v1/mods/{mod['id']}/files", params={"gameVersion": inst["version"], "pageSize": 50}, headers={"x-api-key": key}, timeout=25)
                r.raise_for_status()
                files = r.json().get("data", [])
                if not files: raise RuntimeError("У проекта нет файлов для выбранной версии Minecraft.")
                selected = next((x for x in files if x.get("downloadUrl")), None)
                if not selected: raise RuntimeError("CurseForge не вернул прямую ссылку на файл. Возможно, автор ограничил скачивание.")
                file_url, filename = selected["downloadUrl"], selected["fileName"]
            report(20, "Скачиваю мод…")
            response = requests.get(file_url, timeout=60)
            response.raise_for_status()
            target_dir = profile_dir(inst) / "mods"
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / Path(filename).name
            target.write_bytes(response.content)
            return target.name

        self.mod_install_worker = Worker(task)
        self.mod_install_worker.done.connect(self._mod_installed)
        self.mod_install_worker.failed.connect(self._mod_install_failed)
        self.mod_install_worker.start()

    def _mod_installed(self, filename: str) -> None:
        self._set_mod_controls(True)
        self.status.setText(f"Мод установлен: {filename}")
        QMessageBox.information(self, "Готово", f"Мод сохранён в активную сборку:\n{filename}")

    def _mod_install_failed(self, message: str) -> None:
        self._set_mod_controls(True)
        self.status.setText("Не удалось установить мод: " + message)
        QMessageBox.warning(self, "Установка мода", message)


STYLE = r"""
* { font-family: 'Segoe UI'; font-size: 13px; color: #e9eaf2; }
QMainWindow, #root { background: #090a10; }
#nav { background: #0d0f17; border-right: 1px solid #202333; }
#logo { font-size: 23px; font-weight: 900; color: #f6f4ff; line-height: 21px; }
#sub { color: #797d91; font-size: 9px; letter-spacing: 1px; padding-top: 4px; }
#navButton { border: 1px solid transparent; border-radius: 9px; background: transparent; padding: 11px 12px; text-align: left; color: #a9adbf; }
#navButton:hover { background: #161925; color: #ffffff; }
#navButton:checked { background: #211a35; border: 1px solid #3f315e; color: #d4c2ff; font-weight: 650; }
#topbar { background: #0c0e15; border-bottom: 1px solid #202333; }
#topProfile { color: #aeb2c4; }
#navFootnote { font-size: 11px; color: #63697f; }
#pageTitle { font-size: 29px; font-weight: 750; color: #ffffff; }
#pageDesc { color: #8a8fa3; font-size: 13px; }
#heroCard { background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #171328,stop:0.55 #10121d,stop:1 #12101d); border: 1px solid #3a2d56; border-radius: 18px; margin-left: 30px; margin-right: 30px; }
#heroTitle { font-size: 34px; font-weight: 850; letter-spacing: 1px; color: #c6a9ff; }
#heroSubtitle { font-size: 15px; color: #bfc1d2; }
#card { background: #10121b; border: 1px solid #242838; border-radius: 14px; }
#cardTitle { font-size: 15px; font-weight: 800; letter-spacing: 0.6px; color: #f0eaff; }
#muted { color: #888ea3; font-size: 12px; }
#dialogTitle { color: #ffffff; font-size: 23px; font-weight: 750; }
#profileNick { color: #ffffff; font-size: 25px; font-weight: 750; }
#pill { color: #cbb6ff; background: #211a35; border: 1px solid #44345f; padding: 6px 9px; border-radius: 7px; font-size: 10px; font-weight: 750; }
#error { color: #ff8e9c; }
#skinPlaceholder { color: #777e94; background: #0d0f18; border: 1px dashed #34384b; border-radius: 12px; font-size: 13px; }
QPushButton { background: #171a25; border: 1px solid #2b3043; border-radius: 9px; padding: 10px 13px; color: #e6e7f0; }
QPushButton:hover { background: #202437; border-color: #625087; }
QPushButton:pressed { background: #29213d; }
QPushButton:disabled { color: #5b6070; background: #12141b; border-color: #1f222d; }
QPushButton#primary { background: #8d67ef; border: 1px solid #9d7cff; color: #ffffff; font-weight: 800; padding: 11px 16px; }
QPushButton#primary:hover { background: #a17ef7; }
QComboBox, QLineEdit, QSpinBox { background: #0f121b; border: 1px solid #2a2f41; border-radius: 8px; padding: 9px 10px; selection-background-color: #6f50bc; }
QComboBox:hover, QLineEdit:hover, QSpinBox:hover { border-color: #51416e; }
QComboBox QAbstractItemView { background: #11141e; border: 1px solid #33384c; selection-background-color: #302447; }
QListWidget { background: #0d1018; border: 1px solid #222637; border-radius: 10px; padding: 6px; outline: 0; }
QListWidget::item { padding: 11px; border-radius: 7px; color: #d8dae7; }
QListWidget::item:hover { background: #171a27; }
QListWidget::item:selected { background: #27203c; color: #e0d2ff; border: 1px solid #4a3966; }
QProgressBar { background: #1b1f2c; border: 0; border-radius: 5px; height: 9px; text-align: center; }
QProgressBar::chunk { background: #a080f9; border-radius: 5px; }
QCheckBox { spacing: 9px; }
QCheckBox::indicator { width: 17px; height: 17px; }
QCheckBox::indicator:unchecked { background: #11141c; border: 1px solid #343a4e; border-radius: 4px; }
QCheckBox::indicator:checked { background: #8d67ef; border: 1px solid #ae91ff; border-radius: 4px; }
QSplitter::handle { background: #202333; width: 2px; }
QScrollBar:vertical { background: #0c0e15; width: 10px; margin: 0; }
QScrollBar::handle:vertical { background: #30354a; border-radius: 5px; min-height: 25px; }
"""


def main() -> int:
    ensure_dirs()
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    icon_path = Path(__file__).with_name("icon.ico")
    if icon_path.exists(): app.setWindowIcon(QIcon(str(icon_path)))
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
