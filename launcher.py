import json, os, sys, subprocess, uuid, threading, webbrowser
from pathlib import Path
from urllib.parse import urlencode

import requests
import minecraft_launcher_lib
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QIcon, QPixmap, QFont
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QComboBox, QLineEdit, QListWidget, QListWidgetItem, QStackedWidget,
    QMessageBox, QProgressBar, QFrame, QCheckBox, QDialog, QFormLayout)

APP_NAME = "NovaCraft Launcher"
APP_VERSION = "0.1.0"
ROOT = Path(os.getenv("APPDATA", Path.home())) / "NovaCraft"
MC_DIR = ROOT / "minecraft"
CONFIG = ROOT / "config.json"
CLIENT_TOKEN = "novacraft-" + str(uuid.uuid5(uuid.NAMESPACE_DNS, "novacraft-launcher"))
CF_GAME_ID = 432  # Minecraft: Java Edition


def load_config():
    try: return json.loads(CONFIG.read_text(encoding="utf-8"))
    except Exception: return {"account": None, "curseforge_key": ""}


def save_config(c):
    ROOT.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps(c, ensure_ascii=False, indent=2), encoding="utf-8")


def ely_auth(username, password):
    r = requests.post("https://authserver.ely.by/auth/authenticate", json={
        "username": username, "password": password, "clientToken": CLIENT_TOKEN, "requestUser": True
    }, timeout=25)
    data = r.json()
    if not r.ok: raise RuntimeError(data.get("errorMessage") or data.get("error") or "Ely.by authentication failed")
    return {"type":"ely", "username": data["selectedProfile"]["name"], "uuid": data["selectedProfile"]["id"], "token": data["accessToken"]}


def download_authlib_injector():
    target = ROOT / "authlib-injector.jar"
    if target.exists(): return target
    api = requests.get("https://api.github.com/repos/yushijinhun/authlib-injector/releases/latest", timeout=20).json()
    assets = api.get("assets", [])
    asset = next((a for a in assets if a.get("name", "").endswith(".jar")), None)
    if not asset: raise RuntimeError("Не удалось найти authlib-injector")
    with requests.get(asset["browser_download_url"], stream=True, timeout=60) as r:
        r.raise_for_status()
        with open(target, "wb") as f:
            for chunk in r.iter_content(1024 * 128): f.write(chunk)
    return target


class Worker(QThread):
    progress = Signal(int, str)
    done = Signal(object)
    failed = Signal(str)
    def __init__(self, fn): super().__init__(); self.fn = fn
    def run(self):
        try: self.done.emit(self.fn())
        except Exception as e: self.failed.emit(str(e))


class LoginDialog(QDialog):
    logged = Signal(object)
    def __init__(self, parent=None):
        super().__init__(parent); self.setWindowTitle("Вход через Ely.by"); self.setModal(True); self.setMinimumWidth(390)
        form=QFormLayout(self); self.user=QLineEdit(); self.user.setPlaceholderText("Ник или E-mail")
        self.pw=QLineEdit(); self.pw.setEchoMode(QLineEdit.Password); self.pw.setPlaceholderText("Пароль")
        form.addRow("Логин",self.user); form.addRow("Пароль",self.pw)
        self.status=QLabel(); form.addRow(self.status)
        b=QPushButton("ВОЙТИ"); b.clicked.connect(self.login); form.addRow(b)
    def login(self):
        self.status.setText("Авторизация…"); self.status.setStyleSheet("color:#aaa")
        try:
            a=ely_auth(self.user.text().strip(),self.pw.text()); self.logged.emit(a); self.accept()
        except Exception as e: self.status.setText(str(e)); self.status.setStyleSheet("color:#ff6b7a")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__(); self.cfg=load_config(); self.setWindowTitle(f"{APP_NAME} • {APP_VERSION}"); self.resize(1120,720)
        icon=Path(__file__).with_name("icon.ico")
        if icon.exists(): self.setWindowIcon(QIcon(str(icon)))
        self.build_ui(); self.refresh_versions()

    def build_ui(self):
        root=QWidget(); root.setObjectName("root"); self.setCentralWidget(root); outer=QHBoxLayout(root); outer.setContentsMargins(0,0,0,0)
        nav=QFrame(); nav.setObjectName("nav"); nav.setFixedWidth(220); nv=QVBoxLayout(nav); nv.setContentsMargins(18,24,18,18)
        logo=QLabel("NOVA\nCRAFT"); logo.setObjectName("logo"); nv.addWidget(logo)
        sub=QLabel("MINECRAFT LAUNCHER"); sub.setObjectName("sub"); nv.addWidget(sub); nv.addSpacing(25)
        self.buttons=[]
        for text in ["Главная","Версии","Моды","Настройки"]:
            b=QPushButton(text); b.setCheckable(True); b.clicked.connect(lambda _,x=text:self.switch(x)); nv.addWidget(b); self.buttons.append(b)
        nv.addStretch(); acc=QPushButton(); acc.clicked.connect(self.account); nv.addWidget(acc); self.account_btn=acc; self.update_account_button()
        outer.addWidget(nav)
        self.pages=QStackedWidget(); outer.addWidget(self.pages)
        self.pages.addWidget(self.home_page()); self.pages.addWidget(self.versions_page()); self.pages.addWidget(self.mods_page()); self.pages.addWidget(self.settings_page()); self.buttons[0].setChecked(True)

    def switch(self,text):
        idx={"Главная":0,"Версии":1,"Моды":2,"Настройки":3}[text]; self.pages.setCurrentIndex(idx)
        for i,b in enumerate(self.buttons): b.setChecked(i==idx)

    def header(self,title,desc):
        w=QWidget(); l=QVBoxLayout(w); l.setContentsMargins(28,28,28,10); a=QLabel(title); a.setObjectName("title"); b=QLabel(desc); b.setObjectName("desc"); l.addWidget(a); l.addWidget(b); return w

    def home_page(self):
        w=QWidget(); l=QVBoxLayout(w); l.addWidget(self.header("Добро пожаловать", "Один лаунчер для vanilla, Fabric, Forge, NeoForge и Quilt."))
        card=QFrame(); card.setObjectName("card"); cl=QVBoxLayout(card); cl.setContentsMargins(26,26,26,26)
        self.hero=QLabel("ГОТОВ К ЗАПУСКУ"); self.hero.setObjectName("hero"); cl.addWidget(self.hero)
        row=QHBoxLayout(); self.version=QComboBox(); self.version.setMinimumHeight(46); row.addWidget(self.version,1)
        self.play=QPushButton("ИГРАТЬ"); self.play.setObjectName("primary"); self.play.setMinimumHeight(46); self.play.clicked.connect(self.launch); row.addWidget(self.play,0); cl.addLayout(row)
        self.loader=QComboBox(); self.loader.addItems(["Vanilla","Fabric","Forge","NeoForge","Quilt"]); cl.addWidget(self.loader)
        self.progress=QProgressBar(); self.progress.hide(); cl.addWidget(self.progress); self.status=QLabel("Выберите версию и нажмите Играть."); cl.addWidget(self.status)
        l.addWidget(card); l.addStretch(); return w

    def versions_page(self):
        w=QWidget(); l=QVBoxLayout(w); l.addWidget(self.header("Все версии", "Список берётся напрямую из официального manifest Minecraft."))
        self.versions_list=QListWidget(); l.addWidget(self.versions_list); return w

    def mods_page(self):
        w=QWidget(); l=QVBoxLayout(w); l.addWidget(self.header("Моды", "Поиск и установка из Modrinth. CurseForge подключается через API key в настройках."))
        row=QHBoxLayout(); self.mod_source=QComboBox(); self.mod_source.addItems(["Modrinth","CurseForge"]); self.mod_query=QLineEdit(); self.mod_query.setPlaceholderText("Например: Sodium, JEI, Create…"); b=QPushButton("НАЙТИ"); b.clicked.connect(self.search_mods); row.addWidget(self.mod_source); row.addWidget(self.mod_query,1); row.addWidget(b); l.addLayout(row)
        self.mods=QListWidget(); l.addWidget(self.mods); return w

    def settings_page(self):
        w=QWidget(); l=QVBoxLayout(w); l.addWidget(self.header("Настройки", "Папка игры и интеграции.")); card=QFrame(); card.setObjectName("card"); f=QFormLayout(card); self.cfkey=QLineEdit(self.cfg.get("curseforge_key","")); self.cfkey.setEchoMode(QLineEdit.Password); f.addRow("CurseForge API key",self.cfkey); save=QPushButton("СОХРАНИТЬ"); save.clicked.connect(self.save_settings); f.addRow(save); l.addWidget(card); l.addStretch(); return w

    def update_account_button(self):
        a=self.cfg.get("account"); self.account_btn.setText(("●  "+a["username"]) if a else "Войти через Ely.by")

    def account(self):
        if self.cfg.get("account"):
            if QMessageBox.question(self,"Аккаунт","Выйти из Ely.by?")==QMessageBox.Yes:
                self.cfg["account"]=None; save_config(self.cfg); self.update_account_button(); self.status.setText("Аккаунт отключён."); return
        d=LoginDialog(self); d.logged.connect(self.set_account); d.exec()

    def set_account(self,a): self.cfg["account"]=a; save_config(self.cfg); self.update_account_button(); self.status.setText(f"Выполнен вход: {a['username']}")
    def save_settings(self): self.cfg["curseforge_key"]=self.cfkey.text().strip(); save_config(self.cfg); QMessageBox.information(self,"Сохранено","Настройки сохранены.")

    def refresh_versions(self):
        try:
            vs=minecraft_launcher_lib.utils.get_available_versions(str(MC_DIR)); self.version.clear(); self.versions_list.clear()
            for v in vs:
                self.version.addItem(v["id"],v["type"]); self.versions_list.addItem(f"{v['id']}   •   {v['type']}")
            latest=minecraft_launcher_lib.utils.get_latest_version().get("release");
            if latest:
                i=self.version.findText(latest); self.version.setCurrentIndex(max(i,0))
        except Exception as e: self.status.setText("Не удалось получить список версий: "+str(e))

    def callbacks(self):
        return {"setStatus": lambda s:self.status.setText(str(s)), "setProgress": lambda p:self.progress.setValue(int(p)), "setMax": lambda m:self.progress.setMaximum(int(m))}

    def launch(self):
        account=self.cfg.get("account")
        if not account: QMessageBox.warning(self,"Нужен аккаунт","Войди через Ely.by перед запуском."); return
        ver=self.version.currentText(); loader=self.loader.currentText(); self.progress.show(); self.progress.setValue(0); self.play.setEnabled(False)
        def work():
            MC_DIR.mkdir(parents=True,exist_ok=True)
            installed=ver
            cb=self.callbacks()
            if loader=="Vanilla": minecraft_launcher_lib.install.install_minecraft_version(ver,str(MC_DIR),callback=cb)
            else: installed=minecraft_launcher_lib.mod_loader.get_mod_loader(loader.lower()).install(ver,str(MC_DIR),callback=cb)
            inj=download_authlib_injector()
            opts={"username":account["username"],"uuid":account["uuid"],"token":account["token"],"launcherName":APP_NAME,"launcherVersion":APP_VERSION,"jvmArguments":[f"-javaagent:{inj}=ely.by"]}
            return minecraft_launcher_lib.command.get_minecraft_command(installed,str(MC_DIR),opts)
        self.worker=Worker(work); self.worker.done.connect(self.run_game); self.worker.failed.connect(self.fail); self.worker.start()

    def run_game(self,cmd):
        self.progress.hide(); self.play.setEnabled(True); self.status.setText("Игра запускается…")
        try: subprocess.Popen(cmd,cwd=str(MC_DIR),creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0)); self.status.setText("Minecraft запущен.")
        except Exception as e: self.fail(str(e))

    def fail(self,msg): self.progress.hide(); self.play.setEnabled(True); self.status.setText("Ошибка: "+msg); QMessageBox.critical(self,"Ошибка",msg)

    def search_mods(self):
        q=self.mod_query.text().strip(); self.mods.clear()
        if not q: return
        if self.mod_source.currentText()=="Modrinth":
            try:
                r=requests.get("https://api.modrinth.com/v2/search",params={"query":q,"facets":json.dumps([["project_type:mod"]]),"limit":20},headers={"User-Agent":"bs9973866-png/novacraft-launcher/0.1.0"},timeout=20); r.raise_for_status()
                for hit in r.json().get("hits",[]):
                    item=QListWidgetItem(f"{hit['title']}  —  {hit.get('description','')[:90]}"); item.setData(Qt.UserRole,hit["project_id"]); self.mods.addItem(item)
                self.mods.itemDoubleClicked.connect(self.install_modrinth)
            except Exception as e: QMessageBox.critical(self,"Modrinth",str(e))
        else:
            key=self.cfg.get("curseforge_key","")
            if not key: QMessageBox.warning(self,"CurseForge","Добавь CurseForge API key в Настройки. API CurseForge требует ключ для запросов."); return
            try:
                r=requests.get("https://api.curseforge.com/v1/mods/search",params={"gameId":CF_GAME_ID,"searchFilter":q,"pageSize":20},headers={"x-api-key":key,"Accept":"application/json"},timeout=20); r.raise_for_status()
                for hit in r.json().get("data",[]):
                    item=QListWidgetItem(f"{hit['name']}  —  {hit.get('summary','')[:90]}"); item.setData(Qt.UserRole,hit["id"]); self.mods.addItem(item)
                self.mods.itemDoubleClicked.connect(self.install_curseforge)
            except Exception as e: QMessageBox.critical(self,"CurseForge",str(e))

    def install_modrinth(self,item):
        pid=item.data(Qt.UserRole); ver=self.version.currentText(); loader=self.loader.currentText().lower()
        if loader=="vanilla": QMessageBox.warning(self,"Моды","Сначала выбери Fabric, Forge, NeoForge или Quilt."); return
        try:
            facets=json.dumps([[f"project_type:mod"],[f"versions:{ver}"],[f"categories:{loader}"]]); r=requests.get("https://api.modrinth.com/v2/search",params={"query":"","facets":facets,"limit":1},headers={"User-Agent":"bs9973866-png/novacraft-launcher/0.1.0"},timeout=20)
            versions=requests.get(f"https://api.modrinth.com/v2/project/{pid}/version",params={"game_versions":json.dumps([ver]),"loaders":json.dumps([loader])},headers={"User-Agent":"bs9973866-png/novacraft-launcher/0.1.0"},timeout=20).json()
            if not versions: raise RuntimeError("Для выбранной версии и загрузчика файл не найден.")
            file=next((x for x in versions[0]["files"] if x.get("primary")),versions[0]["files"][0]); mods=MC_DIR/"mods"; mods.mkdir(parents=True,exist_ok=True); data=requests.get(file["url"],timeout=60).content; (mods/file["filename"]).write_bytes(data); QMessageBox.information(self,"Готово",f"Установлено: {file['filename']}")
        except Exception as e: QMessageBox.critical(self,"Modrinth",str(e))

    def install_curseforge(self,item):
        key=self.cfg.get("curseforge_key",""); mid=item.data(Qt.UserRole); ver=self.version.currentText()
        try:
            r=requests.get(f"https://api.curseforge.com/v1/mods/{mid}/files",params={"gameVersion":ver,"pageSize":10},headers={"x-api-key":key},timeout=20); r.raise_for_status(); files=r.json().get("data",[])
            if not files: raise RuntimeError("Для выбранной версии файл не найден.")
            f=files[0]; data=requests.get(f["downloadUrl"],timeout=60).content; mods=MC_DIR/"mods"; mods.mkdir(parents=True,exist_ok=True); (mods/f["fileName"]).write_bytes(data); QMessageBox.information(self,"Готово",f"Установлено: {f['fileName']}")
        except Exception as e: QMessageBox.critical(self,"CurseForge",str(e))


STYLE="""
*{font-family:'Segoe UI';font-size:14px;color:#e8e8f0;} QMainWindow,#root{background:#090a0f;} #nav{background:#0d0f17;border-right:1px solid #202331;} #logo{font-size:30px;font-weight:900;color:#b99cff;line-height:25px;} #sub{font-size:10px;color:#666b7d;letter-spacing:2px;} QPushButton{background:#151823;border:1px solid #252a3b;border-radius:10px;padding:12px;text-align:left;} QPushButton:hover{background:#1c2030;border-color:#514778;} QPushButton:checked{background:#201b31;border-color:#8c68e8;color:#cdbbff;} #primary{background:#8d67ef;color:white;border:0;font-weight:800;text-align:center;padding:13px 28px;} #primary:hover{background:#a17ef5;} #title{font-size:32px;font-weight:800;color:#fff;} #desc{color:#747b90;} #card{background:#10131c;border:1px solid #24283a;border-radius:18px;} #hero{font-size:52px;font-weight:900;color:#c5adff;} QComboBox,QLineEdit{background:#11141d;border:1px solid #2a2e40;border-radius:10px;padding:11px;} QListWidget{background:#0d1018;border:1px solid #202433;border-radius:12px;padding:8px;} QListWidget::item{padding:12px;border-radius:8px;} QListWidget::item:selected{background:#242038;} QProgressBar{height:10px;border:0;background:#1b1e29;border-radius:5px;} QProgressBar::chunk{background:#8d67ef;border-radius:5px;}"""

if __name__=="__main__":
    ROOT.mkdir(parents=True,exist_ok=True); app=QApplication(sys.argv); app.setApplicationName(APP_NAME); app.setStyleSheet(STYLE); win=MainWindow(); win.show(); sys.exit(app.exec())
