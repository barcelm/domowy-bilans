"""
🧹 Domowy bilans — śledzenie prac domowych dwóch domowników (Shiny for Python).

Uruchomienie lokalne:   shiny run --reload app.py
Wdrożenie:              shinyapps.io (README.md); opcjonalnie Cloud Run (deploy.sh)

PRZECHOWYWANIE DANYCH
---------------------
* Google Sheets (shinyapps.io): plik service_account.json + google_sheet_id.txt obok app.py.
  shinyapps.io nie zachowuje plików zapisanych przez aplikację, więc dane trzymamy w arkuszu.
* Pliki w DATA_DIR (lokalnie / Cloud Run z bucketem; DATA_PERSISTENT=1 wyłącza baner).
Stałe zdjęcia można też wrzucić do folderu ./photos przed wdrożeniem
(p0.jpg, p1.jpg, danger.jpg, final.jpg, winner.jpg) — są częścią wdrażanej paczki.
"""
from __future__ import annotations

import base64
import html
import io
import json
import mimetypes
import os
import re
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.ticker import MaxNLocator  # noqa: E402
from shiny import App, reactive, render, ui  # noqa: E402

try:
    from PIL import Image, ImageOps
except ImportError:  # Pillow jest opcjonalny, ale zalecany (zmniejsza zdjęcia)
    Image = ImageOps = None

# =============================================================================
# KONFIGURACJA
# =============================================================================
APP_DIR = Path(__file__).resolve().parent
def _read_sheet_id() -> str:
    """ID arkusza Google: zmienna GOOGLE_SHEET_ID albo plik google_sheet_id.txt obok app.py
    (shinyapps.io nie obsługuje zmiennych środowiskowych, więc tam używamy pliku)."""
    sid = os.environ.get("GOOGLE_SHEET_ID", "").strip()
    f = APP_DIR / "google_sheet_id.txt"
    if not sid and f.exists():
        sid = f.read_text(encoding="utf-8").strip()
    m = re.search(r"/spreadsheets/d/([A-Za-z0-9_-]+)", sid)  # wklejony cały URL też zadziała
    return m.group(1) if m else sid


GOOGLE_SHEET_ID = _read_sheet_id()
GOOGLE_CREDENTIALS = APP_DIR / "service_account.json"
LOCAL_DATA_DIR = Path(os.environ.get("DATA_DIR", APP_DIR / "data"))
STATIC_PHOTO_DIR = APP_DIR / "photos"
TZ = ZoneInfo(os.environ.get("APP_TZ", "Europe/Warsaw"))

COLORS = ("#2a9d8f", "#e76f51")  # domownik 1 (lewa strona), domownik 2 (prawa strona)

PHOTO_SLOTS = {
    "p0": "Awatar — domownik 1 (lewa strona)",
    "p1": "Awatar — domownik 2 (prawa strona)",
    "danger": "⚠️ Komunikat „podciągnij się” (strefa zagrożenia)",
    "final": "🍽️ Komunikat dla przegranego (strefa finalna)",
    "winner": "🏆 Komunikat dla prowadzącego / zwycięzcy",
}

DEFAULT_CHORES = {
    "Zmywanie naczyń": 2,
    "Załadowanie zmywarki": 1,
    "Rozładowanie zmywarki": 1,
    "Gotowanie obiadu": 4,
    "Przygotowanie śniadania": 2,
    "Przygotowanie kolacji": 2,
    "Odkurzanie": 3,
    "Mycie podłóg": 4,
    "Ścieranie kurzy": 2,
    "Sprzątanie łazienki": 4,
    "Wstawienie prania": 1,
    "Rozwieszenie prania": 2,
    "Składanie prania": 2,
    "Prasowanie": 3,
    "Wyniesienie śmieci": 1,
    "Zakupy spożywcze": 3,
    "Zmiana pościeli": 2,
}

DEFAULT_CONFIG = {
    "people": ["Domownik 1", "Domownik 2"],
    "chores": DEFAULT_CHORES,
    "danger": 15.0,  # od tylu punktów różnicy — strefa zagrożenia
    "final": 30.0,  # od tylu punktów różnicy — strefa finalna
    "window_days": 31,  # pamięć prac (pełny miesiąc)
    "reward": "stawia kolację w restauracji",
    "settled_at": None,  # moment ostatniego rozliczenia (reset suwaka)
}

LOG_COLS = ["id", "timestamp", "date", "person_idx", "chore", "weight", "note"]

plt.rcParams.update(
    {
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.grid": True,
        "grid.alpha": 0.3,
        "font.size": 10,
    }
)


# =============================================================================
# FUNKCJE POMOCNICZE
# =============================================================================
def now() -> datetime:
    return datetime.now(TZ)


def today():
    return now().date()


def fmt(v) -> str:
    v = float(v)
    return str(int(v)) if v.is_integer() else f"{v:g}"


def esc(s) -> str:
    return html.escape(str(s))


def parse_ts(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s, utc=True, errors="coerce", format="ISO8601")


def ts_utc(s: str) -> pd.Timestamp:
    t = pd.Timestamp(s)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def empty_log() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "id": pd.Series(dtype=str),
            "timestamp": pd.Series(dtype=str),
            "date": pd.Series(dtype=str),
            "person_idx": pd.Series(dtype=int),
            "chore": pd.Series(dtype=str),
            "weight": pd.Series(dtype=float),
            "note": pd.Series(dtype=str),
        }
    )


def clean_log(df: pd.DataFrame | None) -> pd.DataFrame:
    if df is None or df.empty:
        return empty_log()
    df = df.copy()
    for c in LOG_COLS:
        if c not in df.columns:
            df[c] = ""
    df = df[LOG_COLS]
    df["person_idx"] = pd.to_numeric(df["person_idx"], errors="coerce")
    df["weight"] = pd.to_numeric(df["weight"], errors="coerce")
    df = df.dropna(subset=["person_idx", "weight"])
    df = df[df["person_idx"].isin([0, 1])].copy()
    df["person_idx"] = df["person_idx"].astype(int)
    for c in ("id", "timestamp", "date", "chore", "note"):
        df[c] = df[c].fillna("").astype(str)
    df = df[(df["date"] != "") & (df["chore"] != "")]
    if df.empty:
        return empty_log()
    return df.reset_index(drop=True)


def merge_config(raw) -> dict:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if isinstance(raw, dict):
        cfg.update({k: v for k, v in raw.items() if k in DEFAULT_CONFIG})
    people = [str(p) for p in (cfg.get("people") or [])][:2]
    while len(people) < 2:
        people.append(f"Domownik {len(people) + 1}")
    cfg["people"] = people
    try:
        chores = {str(k): float(v) for k, v in (cfg.get("chores") or {}).items()}
    except (TypeError, ValueError):
        chores = {}
    cfg["chores"] = chores or {k: float(v) for k, v in DEFAULT_CHORES.items()}
    cfg["danger"] = float(cfg.get("danger") or 15)
    cfg["final"] = float(cfg.get("final") or 30)
    if cfg["final"] <= cfg["danger"]:
        cfg["final"] = cfg["danger"] * 2
    cfg["window_days"] = max(1, int(cfg.get("window_days") or 31))
    cfg["reward"] = str(cfg.get("reward") or DEFAULT_CONFIG["reward"])
    return cfg


def prune(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Zostawia tylko wpisy z okna pamięci (domyślnie ostatnie 31 dni)."""
    if df.empty:
        return df
    cutoff = (today() - timedelta(days=int(cfg["window_days"]) - 1)).isoformat()
    return df[df["date"] >= cutoff].reset_index(drop=True)


def period_log(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Wpisy liczone do bilansu: okno pamięci + tylko po ostatnim rozliczeniu."""
    df = prune(df, cfg)
    st = cfg.get("settled_at")
    if st and not df.empty:
        df = df[parse_ts(df["timestamp"]) > ts_utc(st)]
    return df


def balance(df: pd.DataFrame, cfg: dict):
    d = period_log(df, cfg)
    pts = [float(d.loc[d["person_idx"] == i, "weight"].sum()) for i in (0, 1)]
    # diff > 0 -> domownik 2 zrobił więcej -> suwak jedzie w stronę domownika 1
    return pts, pts[1] - pts[0]


def parse_chores(text: str):
    chores, bad = {}, []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^(.+?)\s*[=:]\s*([0-9]+(?:[.,][0-9]+)?)\s*(?:pkt)?$", line)
        if not m or float(m.group(2).replace(",", ".")) <= 0:
            bad.append(line)
            continue
        chores[m.group(1).strip()] = float(m.group(2).replace(",", "."))
    return chores, bad


def chores_to_text(chores: dict) -> str:
    return "\n".join(f"{k} = {fmt(v)}" for k, v in chores.items())


def image_to_data_uri(path, max_chars: int = 45_000) -> str:
    """Zmniejsza zdjęcie i zwraca data-URI (mieści się w komórce Google Sheets)."""
    if Image is None:
        raw = Path(path).read_bytes()
        mime = mimetypes.guess_type(str(path))[0] or "image/jpeg"
        return f"data:{mime};base64,{base64.b64encode(raw).decode()}"
    img = Image.open(path)
    img = ImageOps.exif_transpose(img)
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        bg = Image.new("RGB", img.size, "white")
        bg.paste(img, mask=img.split()[-1])
        img = bg
    else:
        img = img.convert("RGB")
    size, quality = 480, 85
    while True:
        im = img.copy()
        im.thumbnail((size, size))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=quality, optimize=True)
        b64 = base64.b64encode(buf.getvalue()).decode()
        if len(b64) < max_chars or size <= 96:
            break
        size, quality = int(size * 0.8), max(55, quality - 5)
    return "data:image/jpeg;base64," + b64


def load_static_photos() -> dict:
    out = {}
    if STATIC_PHOTO_DIR.is_dir():
        for slot in PHOTO_SLOTS:
            for ext in ("jpg", "jpeg", "png", "webp", "gif"):
                p = STATIC_PHOTO_DIR / f"{slot}.{ext}"
                if p.exists():
                    try:
                        out[slot] = image_to_data_uri(p, max_chars=400_000)
                    except Exception:
                        pass
                    break
    return out


def normalize_import(raw: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    df = raw.rename(columns=lambda c: str(c).strip().lower())
    df = df.rename(
        columns={"data": "date", "kto": "person", "praca": "chore", "pkt": "weight",
                 "waga": "weight", "notatka": "note"}
    )
    if "date" not in df.columns or "chore" not in df.columns:
        raise ValueError("CSV musi mieć kolumny 'date' i 'chore'.")
    if "person_idx" in df.columns and df["person_idx"].astype(str).str.strip().isin(["0", "1"]).all():
        df["person_idx"] = pd.to_numeric(df["person_idx"])
    elif "person" in df.columns:
        names = {n.strip().lower(): i for i, n in enumerate(cfg["people"])}
        df["person_idx"] = df["person"].astype(str).str.strip().str.lower().map(names)
    else:
        raise ValueError("CSV musi mieć kolumnę 'person_idx' (0/1) lub 'person' (imię).")
    s = df["date"].astype(str).str.strip()
    d = pd.to_datetime(s, format="%Y-%m-%d", errors="coerce")
    d = d.fillna(pd.to_datetime(s, format="%d.%m.%Y", errors="coerce"))
    df["date"] = d.dt.strftime("%Y-%m-%d")
    df = df.dropna(subset=["date"])
    df["chore"] = df["chore"].astype(str).str.strip()
    w = pd.to_numeric(df["weight"].astype(str).str.replace(",", "."), errors="coerce") \
        if "weight" in df.columns else pd.Series(np.nan, index=df.index)
    df["weight"] = w.fillna(df["chore"].map(cfg["chores"])).fillna(1.0)
    if "id" not in df.columns:
        df["id"] = ""
    df["id"] = [i if str(i).strip() else uuid.uuid4().hex[:12] for i in df["id"]]
    if "timestamp" not in df.columns:
        df["timestamp"] = ""
    df["timestamp"] = [
        t if str(t).strip() else pd.Timestamp(f"{dd} 12:00").tz_localize(TZ).isoformat()
        for t, dd in zip(df["timestamp"], df["date"])
    ]
    if "note" not in df.columns:
        df["note"] = ""
    return clean_log(df)


# =============================================================================
# MAGAZYN DANYCH
# =============================================================================
class LocalStorage:
    persistent = os.environ.get("DATA_PERSISTENT", "").strip().lower() in ("1", "true", "yes", "tak")

    def __init__(self, folder: Path):
        self.d = Path(folder)
        self.d.mkdir(parents=True, exist_ok=True)
        self.log_f = self.d / "log.csv"
        self.cfg_f = self.d / "config.json"
        self.ph_f = self.d / "photos.json"
        self._writes = 0  # licznik własnych zapisów (bucket może chwilę cache'ować mtime)

    def version(self) -> str:
        mt = "-".join(
            str(f.stat().st_mtime_ns) if f.exists() else "0" for f in (self.log_f, self.cfg_f, self.ph_f)
        )
        return f"{self._writes}:{mt}"

    def _read_json(self, f: Path) -> dict:
        try:
            return json.loads(f.read_text("utf-8")) if f.exists() else {}
        except Exception:
            return {}

    def _atomic(self, f: Path, text: str):
        tmp = f.with_suffix(f.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, f)
        self._writes += 1

    def read_all(self):
        log = (
            clean_log(pd.read_csv(self.log_f, dtype=str, keep_default_na=False))
            if self.log_f.exists() else empty_log()
        )
        return log, self._read_json(self.cfg_f), self._read_json(self.ph_f)

    def write_log(self, df: pd.DataFrame):
        self._atomic(self.log_f, df[LOG_COLS].to_csv(index=False))

    def write_config(self, cfg: dict):
        self._atomic(self.cfg_f, json.dumps(cfg, ensure_ascii=False, indent=2))

    def write_photo(self, slot: str, uri: str | None):
        photos = self._read_json(self.ph_f)
        if uri:
            photos[slot] = uri
        else:
            photos.pop(slot, None)
        self._atomic(self.ph_f, json.dumps(photos))


class SheetsStorage:
    """Arkusze: 'log' (wpisy), 'kv' (konfiguracja + zdjęcia), 'meta' (licznik wersji)."""

    persistent = True

    def __init__(self, creds: Path, sheet_id: str):
        import gspread

        self._gs = gspread
        gc = gspread.service_account(filename=str(creds))
        self.sh = gc.open_by_key(sheet_id)
        self.ws_log = self._ws("log", 2000, len(LOG_COLS))
        self.ws_kv = self._ws("kv", 50, 2)
        self.ws_meta = self._ws("meta", 5, 2)
        self._last_ver = "0"

    def _ws(self, title, rows, cols):
        try:
            return self.sh.worksheet(title)
        except self._gs.WorksheetNotFound:
            return self.sh.add_worksheet(title=title, rows=rows, cols=cols)

    def version(self) -> str:
        try:
            self._last_ver = str(self.ws_meta.acell("B1").value or "0")
        except Exception:
            pass
        return self._last_ver

    def _bump(self):
        try:
            v = int(self.version())
        except ValueError:
            v = 0
        self.ws_meta.update(range_name="A1:B1", values=[["version", v + 1]], value_input_option="RAW")

    def read_all(self):
        rows = self.ws_log.get_all_values()
        log = clean_log(pd.DataFrame(rows[1:], columns=rows[0])) if len(rows) > 1 else empty_log()
        kv = {r[0]: r[1] for r in self.ws_kv.get_all_values() if len(r) >= 2 and r[0]}
        try:
            cfg = json.loads(kv.get("config") or "{}")
        except json.JSONDecodeError:
            cfg = {}
        photos = {k[6:]: v for k, v in kv.items() if k.startswith("photo_") and v}
        return log, cfg, photos

    def write_log(self, df: pd.DataFrame):
        values = [LOG_COLS] + df[LOG_COLS].astype(str).values.tolist()
        if len(values) + 10 > self.ws_log.row_count:
            self.ws_log.resize(rows=len(values) + 500)
        self.ws_log.clear()
        self.ws_log.update(range_name="A1", values=values, value_input_option="RAW")
        self._bump()

    def _kv_set(self, key: str, value: str):
        rows = self.ws_kv.get_all_values()
        keys = [r[0] if r else "" for r in rows]
        i = keys.index(key) + 1 if key in keys else len(rows) + 1
        if i > self.ws_kv.row_count:
            self.ws_kv.resize(rows=i + 20)
        self.ws_kv.update(range_name=f"A{i}:B{i}", values=[[key, value]], value_input_option="RAW")
        self._bump()

    def write_config(self, cfg: dict):
        self._kv_set("config", json.dumps(cfg, ensure_ascii=False))

    def write_photo(self, slot: str, uri: str | None):
        self._kv_set(f"photo_{slot}", uri or "")


STORE_ERROR = None
if GOOGLE_SHEET_ID and GOOGLE_CREDENTIALS.exists():
    try:
        STORE = SheetsStorage(GOOGLE_CREDENTIALS, GOOGLE_SHEET_ID)
    except Exception as e:  # pokaż błąd w aplikacji zamiast się wywracać
        STORE_ERROR = f"Nie udało się połączyć z Google Sheets: {e}"
        STORE = LocalStorage(LOCAL_DATA_DIR)
else:
    STORE = LocalStorage(LOCAL_DATA_DIR)

# co ile sekund sprawdzać, czy drugi domownik coś dopisał
POLL_SECONDS = float(os.environ.get("POLL_SECONDS", 10 if isinstance(STORE, SheetsStorage) else 3))
STATIC_PHOTOS = load_static_photos()
_cache = {"ver": None, "data": None}


def snapshot():
    """(log, config, zdjęcia) — współdzielone przez wszystkie sesje, odświeżane po zmianie wersji."""
    v = STORE.version()
    if _cache["ver"] != v:
        log, raw_cfg, photos = STORE.read_all()
        _cache.update(ver=v, data=(log, merge_config(raw_cfg), photos))
    return _cache["data"]


def write_log_df(df: pd.DataFrame) -> int:
    cfg = snapshot()[1]
    df = prune(clean_log(df), cfg)
    STORE.write_log(df)
    return len(df)


def update_config(**changes):
    cfg = json.loads(json.dumps(snapshot()[1]))
    cfg.update(changes)
    STORE.write_config(cfg)


def delete_entries(ids):
    log = snapshot()[0]
    write_log_df(log[~log["id"].isin(list(ids))])


# =============================================================================
# UI
# =============================================================================
CSS = """
:root { --z-ok:#b7e4c7; --z-danger:#ffd166; --z-final:#ef476f; }
.balance-card { padding:1.2rem 1rem .8rem; border-radius:1rem; background:#fff;
  box-shadow:0 2px 14px rgba(0,0,0,.08); margin-bottom:1rem; }
.balance-row { display:flex; align-items:center; gap:1rem; }
.side { display:flex; flex-direction:column; align-items:center; min-width:96px; text-align:center; }
.side-name { font-weight:700; margin-top:.3rem; }
.side-pts { font-size:.85rem; color:#666; }
.avatar { width:76px; height:76px; border-radius:50%; object-fit:cover; border:4px solid; }
.avatar-txt { display:flex; align-items:center; justify-content:center; color:#fff;
  font-weight:800; font-size:2rem; }
.lagging .avatar { animation:pulse 1.4s infinite; }
@keyframes pulse { 0%{box-shadow:0 0 0 0 rgba(239,71,111,.7)} 70%{box-shadow:0 0 0 14px rgba(239,71,111,0)}
  100%{box-shadow:0 0 0 0 rgba(239,71,111,0)} }
.track-wrap { flex:1; position:relative; padding:30px 0 28px; }
.track { height:22px; border-radius:11px; position:relative; }
.center-line { position:absolute; left:50%; top:-5px; bottom:-5px; width:2px; background:rgba(0,0,0,.35); }
.marker { position:absolute; top:50%; transform:translate(-50%,-50%); width:42px; height:42px;
  border-radius:50%; background:#fff; border:3px solid #333; display:flex; align-items:center;
  justify-content:center; font-size:1.2rem; box-shadow:0 2px 8px rgba(0,0,0,.3); z-index:2; }
.marker-label { position:absolute; top:2px; transform:translateX(-50%); font-weight:700;
  font-size:.85rem; white-space:nowrap; }
.zone-label { position:absolute; bottom:2px; transform:translateX(-50%); font-size:.9rem; }
.slide { animation:slide 1.2s cubic-bezier(.2,.8,.2,1); }
@keyframes slide { from { left:var(--from); } to { left:var(--to); } }
.balance-hint { font-size:.8rem; color:#777; text-align:center; margin-top:.3rem; }
.msg-alert { display:flex; align-items:center; gap:1rem; flex-wrap:wrap; justify-content:center; }
.msg-alert.final { animation:shake .6s 2; }
@keyframes shake { 0%,100%{transform:translateX(0)} 25%{transform:translateX(-6px)} 75%{transform:translateX(6px)} }
.msg-body { flex:1 1 260px; }
.msg-pic { text-align:center; }
.msg-img { max-height:170px; max-width:220px; border-radius:.8rem; object-fit:cover; }
.thumb-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(110px,1fr)); gap:.8rem; }
.thumb { width:100%; aspect-ratio:1; object-fit:cover; border-radius:.6rem; }
.thumb-empty { background:#f1f3f5; display:flex; align-items:center; justify-content:center; color:#aaa; }
@media (max-width:576px) {
  .side { min-width:62px; } .avatar { width:52px; height:52px; } .avatar-txt { font-size:1.4rem; }
  .balance-row { gap:.5rem; } .msg-img { max-height:120px; }
}
"""


def photo_upload_row(slot: str, label: str):
    return ui.div(
        ui.input_file(f"photo_{slot}", label, accept=["image/*"], button_label="Wybierz…",
                      placeholder="brak pliku"),
        ui.input_action_link(f"del_{slot}", "🗑️ usuń zdjęcie"),
        class_="mb-3",
    )


app_ui = ui.page_navbar(
    ui.nav_panel(
        "🏠 Bilans",
        ui.output_ui("storage_warning"),
        ui.output_ui("balance_ui"),
        ui.output_ui("message_ui"),
        ui.layout_columns(
            ui.card(
                ui.card_header("➕ Dodaj wykonaną pracę"),
                ui.input_radio_buttons("who", "Kto wykonał?", {"0": "Domownik 1", "1": "Domownik 2"},
                                       inline=True),
                ui.input_select("chore", "Praca", choices=[]),
                ui.input_date("chore_date", "Kiedy", language="pl", format="dd.mm.yyyy", weekstart=1),
                ui.input_text("note", "Notatka (opcjonalnie)", placeholder="np. po imprezie 🙈"),
                ui.input_action_button("add", "Dodaj", class_="btn-primary btn-lg w-100"),
                ui.input_action_button("undo", "↩️ Cofnij ostatni wpis",
                                       class_="btn-outline-secondary w-100 mt-2"),
            ),
            ui.card(ui.card_header("🕒 Ostatnie wpisy"), ui.output_table("recent")),
            col_widths={"sm": 12, "lg": (5, 7)},
        ),
    ),
    ui.nav_panel(
        "📊 Statystyki",
        ui.layout_columns(
            ui.card(ui.card_header("Kto najczęściej wykonuje dane prace"),
                    ui.output_plot("plot_counts", height="460px"), full_screen=True),
            ui.card(ui.card_header("Bilans w czasie"),
                    ui.output_plot("plot_balance", height="460px"), full_screen=True),
            col_widths={"sm": 12, "lg": (6, 6)},
        ),
        ui.layout_columns(
            ui.card(ui.card_header("Punkty dziennie"), ui.output_plot("plot_daily", height="340px"),
                    full_screen=True),
            ui.card(ui.card_header("Podsumowanie prac"), ui.output_data_frame("summary_table")),
            col_widths={"sm": 12, "lg": (6, 6)},
        ),
    ),
    ui.nav_panel(
        "📋 Historia",
        ui.card(
            ui.card_header("Wpisy z okna pamięci (zaznacz wiersze, aby je usunąć)"),
            ui.output_data_frame("history_table"),
            ui.div(
                ui.input_action_button("delete_sel", "🗑️ Usuń zaznaczone", class_="btn-outline-danger"),
                ui.download_button("download_csv", "⬇️ Pobierz CSV", class_="btn-success"),
                class_="d-flex gap-2 flex-wrap mt-2",
            ),
        ),
        ui.card(
            ui.card_header("⬆️ Import CSV (np. przywrócenie kopii)"),
            ui.input_radio_buttons("import_mode", "Tryb importu",
                                   {"append": "Dołącz do istniejących", "replace": "Zastąp wszystko"},
                                   inline=True),
            ui.input_file("upload_csv", None, accept=[".csv"], button_label="Wybierz CSV…"),
        ),
    ),
    ui.nav_panel(
        "⚙️ Ustawienia",
        ui.layout_columns(
            ui.card(
                ui.card_header("👥 Domownicy i zasady"),
                ui.layout_columns(ui.input_text("name0", "Domownik 1 (lewa strona)"),
                                  ui.input_text("name1", "Domownik 2 (prawa strona)")),
                ui.layout_columns(ui.input_numeric("danger", "Strefa zagrożenia od [pkt różnicy]", 15, min=1),
                                  ui.input_numeric("final", "Strefa finalna od [pkt różnicy]", 30, min=2)),
                ui.layout_columns(ui.input_numeric("window", "Pamięć prac [dni]", 31, min=1, max=366),
                                  ui.input_text("reward", "Kara w strefie finalnej")),
                ui.input_text_area("chores_text", "Prace i wagi — jedna na linię: nazwa = punkty",
                                   rows=16, width="100%"),
                ui.input_action_button("save_settings", "💾 Zapisz ustawienia", class_="btn-primary"),
                ui.hr(),
                ui.output_text("period_info"),
                ui.input_action_button("settle_btn2", "🤝 Rozlicz i wyzeruj bilans",
                                       class_="btn-outline-danger mt-2"),
            ),
            ui.card(
                ui.card_header("🖼️ Zdjęcia"),
                ui.markdown(
                    "Zdjęcia są automatycznie zmniejszane. Na stałe możesz je też wrzucić do folderu "
                    "`photos/` przed wdrożeniem (`p0.jpg`, `p1.jpg`, `danger.jpg`, `final.jpg`, `winner.jpg`)."
                ),
                *[photo_upload_row(s, label) for s, label in PHOTO_SLOTS.items()],
                ui.output_ui("photo_previews"),
            ),
            col_widths={"sm": 12, "lg": (6, 6)},
        ),
    ),
    title="🧹 Domowy bilans",
    id="tabs",
    header=ui.head_content(ui.tags.style(CSS)),
    window_title="Domowy bilans",
)


def avatar_html(uri, name, color) -> str:
    if uri:
        return f'<img class="avatar" src="{uri}" style="border-color:{color}" alt="{esc(name)}">'
    return (f'<div class="avatar avatar-txt" style="background:{color};border-color:{color}">'
            f'{esc(name[:1].upper() or "?")}</div>')


def no_data_fig(msg="Brak danych do wykresu"):
    fig, ax = plt.subplots()
    ax.text(0.5, 0.5, msg, ha="center", va="center", fontsize=13, color="#888")
    ax.axis("off")
    return fig


# =============================================================================
# SERVER
# =============================================================================
def server(input, output, session):
    bump = reactive.value(0)
    prev_pos = {"v": 50.0}
    last_synced = {"cfg": None}

    def refresh():
        bump.set(bump.get() + 1)

    def notify(msg, kind="message", duration=4):
        ui.notification_show(msg, type=kind, duration=duration)

    # --- wspólny stan (odpytywanie magazynu, żeby oba telefony widziały zmiany) -------
    @reactive.poll(STORE.version, POLL_SECONDS)
    def store_version():
        return STORE.version()

    @reactive.calc
    def snap():
        store_version()
        bump()
        return snapshot()

    @reactive.calc
    def cfg():
        return snap()[1]

    @reactive.calc
    def photos():
        ph = dict(STATIC_PHOTOS)
        ph.update(snap()[2])
        return ph

    @reactive.calc
    def log():
        c = cfg()
        df = prune(snap()[0].copy(), c)
        df["person"] = df["person_idx"].map({0: c["people"][0], 1: c["people"][1]})
        return df

    # --- synchronizacja kontrolek z konfiguracją --------------------------------------
    @reactive.effect
    def _init_date():
        t = today()
        ui.update_date("chore_date", value=t, max=t)

    @reactive.effect
    def _sync_inputs():
        c = cfg()
        key = json.dumps(c, sort_keys=True)
        if key == last_synced["cfg"]:
            return
        last_synced["cfg"] = key
        n = c["people"]
        with reactive.isolate():
            who = input.who() or "0"
            ch = input.chore()
        ui.update_radio_buttons("who", choices={"0": n[0], "1": n[1]}, selected=who)
        choices = {k: f"{k}  ·  {fmt(v)} pkt" for k, v in c["chores"].items()}
        ui.update_select("chore", choices=choices, selected=ch if ch in choices else next(iter(choices)))
        ui.update_text("name0", value=n[0])
        ui.update_text("name1", value=n[1])
        ui.update_numeric("danger", value=fmt(c["danger"]))
        ui.update_numeric("final", value=fmt(c["final"]))
        ui.update_numeric("window", value=c["window_days"])
        ui.update_text("reward", value=c["reward"])
        ui.update_text_area("chores_text", value=chores_to_text(c["chores"]))

    # --- dodawanie / cofanie / usuwanie ------------------------------------------------
    @reactive.effect
    @reactive.event(input.add)
    def _add():
        c = cfg()
        chore = input.chore()
        if not chore or chore not in c["chores"]:
            notify("Wybierz pracę z listy.", "error")
            return
        who = int(input.who() or 0)
        d = input.chore_date() or today()
        if d > today():
            notify("Nie można wpisywać prac z przyszłości 😉", "error")
            return
        if d < today() - timedelta(days=c["window_days"] - 1):
            notify(f"Ta data jest poza {c['window_days']}-dniowym oknem pamięci.", "warning")
            return
        w = float(c["chores"][chore])
        row = {
            "id": uuid.uuid4().hex[:12],
            "timestamp": now().isoformat(timespec="seconds"),
            "date": d.isoformat(),
            "person_idx": who,
            "chore": chore,
            "weight": w,
            "note": (input.note() or "").strip(),
        }
        try:
            log_df = snapshot()[0]
            new = pd.DataFrame([row])
            write_log_df(new if log_df.empty else pd.concat([log_df, new], ignore_index=True))
        except Exception as e:
            notify(f"Błąd zapisu: {e}", "error", 8)
            return
        refresh()
        ui.update_text("note", value="")
        notify(f"✅ {c['people'][who]}: {chore} (+{fmt(w)} pkt)")

    @reactive.effect
    @reactive.event(input.undo)
    def _undo():
        log_df, c, _ = snapshot()
        if log_df.empty:
            notify("Brak wpisów do cofnięcia.", "warning")
            return
        ts = parse_ts(log_df["timestamp"])
        i = ts.idxmax() if ts.notna().any() else log_df.index[-1]
        row = log_df.loc[i]
        try:
            delete_entries([row["id"]])
        except Exception as e:
            notify(f"Błąd zapisu: {e}", "error", 8)
            return
        refresh()
        notify(f"↩️ Cofnięto: {c['people'][int(row['person_idx'])]} – {row['chore']}")

    @reactive.effect
    @reactive.event(input.delete_sel)
    def _delete_selected():
        sel = history_table.cell_selection()
        rows = list(sel.get("rows", ())) if sel else []
        view = history_view()
        if not rows or view.empty:
            notify("Najpierw zaznacz wiersze w tabeli.", "warning")
            return
        ids = view.iloc[[r for r in rows if r < len(view)]]["id"].tolist()
        try:
            delete_entries(ids)
        except Exception as e:
            notify(f"Błąd zapisu: {e}", "error", 8)
            return
        refresh()
        notify(f"🗑️ Usunięto wpisów: {len(ids)}")

    # --- rozliczenie --------------------------------------------------------------------
    def ask_settle():
        ui.modal_show(
            ui.modal(
                ui.p("Suwak wróci na środek. Wpisy zostają w historii i statystykach."),
                title="🤝 Rozliczyć się?",
                footer=ui.div(
                    ui.input_action_button("confirm_settle", "Tak, rozliczone", class_="btn-danger"),
                    ui.modal_button("Anuluj"),
                ),
                easy_close=True,
            )
        )

    @reactive.effect
    @reactive.event(input.settle_btn)
    def _settle1():
        ask_settle()

    @reactive.effect
    @reactive.event(input.settle_btn2)
    def _settle2():
        ask_settle()

    @reactive.effect
    @reactive.event(input.confirm_settle)
    def _confirm_settle():
        try:
            update_config(settled_at=now().isoformat(timespec="seconds"))
        except Exception as e:
            notify(f"Błąd zapisu: {e}", "error", 8)
            return
        refresh()
        ui.modal_remove()
        notify("🤝 Rozliczone! Bilans wyzerowany.")

    # --- ustawienia ----------------------------------------------------------------------
    @reactive.effect
    @reactive.event(input.save_settings)
    def _save_settings():
        chores, bad = parse_chores(input.chores_text() or "")
        if bad:
            notify("Nie rozumiem linii: " + "; ".join(bad[:3]) + " (format: nazwa = punkty)", "error", 8)
            return
        if not chores:
            notify("Lista prac nie może być pusta.", "error")
            return
        danger, final = input.danger(), input.final()
        if not danger or not final or danger <= 0 or final <= danger:
            notify("Strefa finalna musi być większa od strefy zagrożenia (obie > 0).", "error")
            return
        n0 = (input.name0() or "").strip() or "Domownik 1"
        n1 = (input.name1() or "").strip() or "Domownik 2"
        if n0.lower() == n1.lower():
            notify("Domownicy muszą mieć różne imiona.", "error")
            return
        try:
            update_config(
                people=[n0, n1],
                chores=chores,
                danger=float(danger),
                final=float(final),
                window_days=max(1, int(input.window() or 31)),
                reward=(input.reward() or "").strip() or DEFAULT_CONFIG["reward"],
            )
        except Exception as e:
            notify(f"Błąd zapisu: {e}", "error", 8)
            return
        refresh()
        notify("💾 Zapisano ustawienia.")

    def make_photo_handlers(slot):
        @reactive.effect
        @reactive.event(input[f"photo_{slot}"])
        def _upload():
            files = input[f"photo_{slot}"]()
            if not files:
                return
            try:
                STORE.write_photo(slot, image_to_data_uri(files[0]["datapath"]))
            except Exception as e:
                notify(f"Nie udało się zapisać zdjęcia: {e}", "error", 8)
                return
            refresh()
            notify("🖼️ Zapisano zdjęcie.")

        @reactive.effect
        @reactive.event(input[f"del_{slot}"])
        def _delete():
            try:
                STORE.write_photo(slot, None)
            except Exception as e:
                notify(f"Błąd: {e}", "error", 8)
                return
            refresh()
            notify("Usunięto zdjęcie.")

    for s in PHOTO_SLOTS:
        make_photo_handlers(s)

    # --- import / eksport ----------------------------------------------------------------
    @render.download(filename=lambda: f"prace_domowe_{today():%Y-%m-%d}.csv")
    def download_csv():
        cols = ["id", "timestamp", "date", "person_idx", "person", "chore", "weight", "note"]
        yield "\ufeff" + log()[cols].to_csv(index=False)

    @reactive.effect
    @reactive.event(input.upload_csv)
    def _import_csv():
        files = input.upload_csv()
        if not files:
            return
        try:
            raw = pd.read_csv(files[0]["datapath"], dtype=str, keep_default_na=False,
                              sep=None, engine="python", encoding="utf-8-sig")
            new = normalize_import(raw, cfg())
        except Exception as e:
            notify(f"Błąd importu: {e}", "error", 8)
            return
        if new.empty:
            notify("Plik nie zawiera poprawnych wpisów.", "warning")
            return
        log_df = snapshot()[0]
        if input.import_mode() == "replace" or log_df.empty:
            df = new
        else:
            df = pd.concat([log_df, new], ignore_index=True).drop_duplicates("id", keep="last")
        try:
            n = write_log_df(df)
        except Exception as e:
            notify(f"Błąd zapisu: {e}", "error", 8)
            return
        refresh()
        notify(f"⬆️ Zaimportowano. Wpisów w oknie pamięci: {n}")

    # --- widoki: bilans ------------------------------------------------------------------
    @render.ui
    def storage_warning():
        if STORE_ERROR:
            return ui.div(f"⚠️ {STORE_ERROR} — działam w trybie lokalnym.", class_="alert alert-danger")
        if not STORE.persistent:
            return ui.div(
                "💾 Tryb lokalny: dane znikną po restarcie aplikacji. Dodaj service_account.json "
                "i google_sheet_id.txt (zapis do Google Sheets, patrz README) albo regularnie "
                "pobieraj CSV (zakładka Historia).",
                class_="alert alert-secondary small py-2",
            )
        return None

    @render.ui
    def balance_ui():
        c, ph = cfg(), photos()
        (p0, p1), diff = balance(log(), c)
        n, final, danger = c["people"], c["final"], c["danger"]
        R = final * 1.2  # suwak ma lekki zapas za granicą strefy finalnej
        pos = 50 - 50 * max(-1.0, min(1.0, diff / R))
        fz, dz = 50 - 50 * final / R, 50 - 50 * danger / R
        gap = abs(diff)
        lag = None if gap < danger else (0 if diff > 0 else 1)
        grad = (f"linear-gradient(90deg, var(--z-final) 0 {fz:.2f}%, var(--z-danger) {fz:.2f}% {dz:.2f}%, "
                f"var(--z-ok) {dz:.2f}% {100 - dz:.2f}%, var(--z-danger) {100 - dz:.2f}% {100 - fz:.2f}%, "
                f"var(--z-final) {100 - fz:.2f}% 100%)")
        start, prev_pos["v"] = prev_pos["v"], pos
        emoji = "⚖️" if lag is None else ("😬" if gap < final else "🍽️")
        label = "remis" if gap == 0 else f"{fmt(gap)} pkt"
        anim = f"--from:{start:.2f}%;--to:{pos:.2f}%;left:{pos:.2f}%"

        def side(i, pts):
            cls = "side lagging" if lag == i else "side"
            return (f'<div class="{cls}">{avatar_html(ph.get(f"p{i}"), n[i], COLORS[i])}'
                    f'<div class="side-name" style="color:{COLORS[i]}">{esc(n[i])}</div>'
                    f'<div class="side-pts">{fmt(pts)} pkt</div></div>')

        zones = "".join(
            f'<div class="zone-label" style="left:{x:.2f}%">{e}</div>'
            for x, e in [(fz / 2, "🍽️"), ((fz + dz) / 2, "⚠️"), (100 - (fz + dz) / 2, "⚠️"), (100 - fz / 2, "🍽️")]
        )
        return ui.HTML(f"""
        <div class="balance-card">
          <div class="balance-row">
            {side(0, p0)}
            <div class="track-wrap">
              <div class="marker-label slide" style="{anim}">{label}</div>
              <div class="track" style="background:{grad}">
                <div class="center-line"></div>
                <div class="marker slide" style="{anim}">{emoji}</div>
              </div>
              {zones}
            </div>
            {side(1, p1)}
          </div>
          <div class="balance-hint">Gdy jedna osoba wykonuje prace, suwak przesuwa się w stronę drugiej.
            ⚠️ od {fmt(danger)} pkt różnicy · 🍽️ od {fmt(final)} pkt.</div>
        </div>""")

    @render.ui
    def message_ui():
        c, ph = cfg(), photos()
        _, diff = balance(log(), c)
        n, gap = c["people"], abs(diff)
        if gap < c["danger"]:
            if gap == 0:
                txt = "⚖️ Idealna równowaga — tak trzymać!"
            else:
                txt = f"🙂 {n[1] if diff > 0 else n[0]} prowadzi o {fmt(gap)} pkt. Spokojnie, wszystko w normie."
            return ui.div(txt, class_="alert alert-success")

        lag = 0 if diff > 0 else 1
        win = 1 - lag
        is_final = gap >= c["final"]
        lag_img = ph.get("final" if is_final else "danger") or ph.get(f"p{lag}")
        win_img = ph.get("winner") or ph.get(f"p{win}")

        def pic(uri, caption):
            if not uri:
                return None
            return ui.div(ui.tags.img(src=uri, class_="msg-img"), ui.div(caption, class_="small mt-1 fw-bold"),
                          class_="msg-pic")

        if is_final:
            body = ui.div(
                ui.h4(f"🍽️ {n[lag]} przegrywa i {c['reward']}!"),
                ui.p(f"Różnica {fmt(gap)} pkt przekroczyła granicę {fmt(c['final'])} pkt. "
                     f"Beneficjent: {n[win]} 🎉"),
                ui.p("Gdy rachunek zostanie wyrównany, wyzerujcie suwak:"),
                ui.input_action_button("settle_btn", "🤝 Rozliczone — wyzeruj bilans", class_="btn-light"),
                class_="msg-body",
            )
            cls = "alert alert-danger msg-alert final"
        else:
            top = max(c["chores"].items(), key=lambda kv: kv[1])
            body = ui.div(
                ui.h4(f"⚠️ {n[lag]}, czas się podciągnąć!"),
                ui.p(f"{n[win]} prowadzi o {fmt(gap)} pkt. Do strefy finalnej brakuje już tylko "
                     f"{fmt(c['final'] - gap)} pkt — wtedy {n[lag]} {c['reward']} 😉"),
                ui.p(f"Szybki sposób na odrobienie strat: {top[0]} (+{fmt(top[1])} pkt).", class_="mb-0"),
                class_="msg-body",
            )
            cls = "alert alert-warning msg-alert"
        return ui.div(pic(lag_img, f"{n[lag]} 😬"), body, pic(win_img, f"{n[win]} 🏆"), class_=cls)

    @reactive.calc
    def history_view():
        df = log()
        cols = ["Data", "Kto", "Praca", "Pkt", "Notatka", "Dodano", "id"]
        if df.empty:
            return pd.DataFrame(columns=cols)
        df = df.assign(_ts=parse_ts(df["timestamp"])).sort_values(["date", "_ts"], ascending=False)
        out = pd.DataFrame({
            "Data": pd.to_datetime(df["date"]).dt.strftime("%d.%m.%Y"),
            "Kto": df["person"],
            "Praca": df["chore"],
            "Pkt": df["weight"],
            "Notatka": df["note"],
            "Dodano": df["_ts"].dt.tz_convert(TZ).dt.strftime("%d.%m %H:%M").fillna(""),
            "id": df["id"],
        })
        return out.reset_index(drop=True)

    @render.table
    def recent():
        hv = history_view().head(8)
        if hv.empty:
            return pd.DataFrame({"Info": ["Brak wpisów — dodaj pierwszą pracę 🙂"]})
        return hv[["Data", "Kto", "Praca", "Pkt"]].assign(Pkt=hv["Pkt"].map(fmt))

    @render.data_frame
    def history_table():
        return render.DataGrid(history_view(), selection_mode="rows", width="100%", height="460px")

    @render.text
    def period_info():
        c = cfg()
        start = today() - timedelta(days=c["window_days"] - 1)
        s = f"Pamięć prac: od {start:%d.%m.%Y} (ostatnie {c['window_days']} dni)."
        if c.get("settled_at"):
            s += f" Ostatnie rozliczenie: {ts_utc(c['settled_at']).tz_convert(TZ):%d.%m.%Y %H:%M}."
        return s

    @render.ui
    def photo_previews():
        ph, n = photos(), cfg()["people"]
        labels = {"p0": n[0], "p1": n[1], "danger": "⚠️ zagrożenie", "final": "🍽️ przegrany",
                  "winner": "🏆 zwycięzca"}
        items = []
        for slot, label in labels.items():
            uri = ph.get(slot)
            img = ui.tags.img(src=uri, class_="thumb") if uri else ui.div("brak", class_="thumb thumb-empty")
            items.append(ui.div(img, ui.div(label, class_="small text-muted mt-1"), class_="text-center"))
        return ui.div(*items, class_="thumb-grid")

    # --- statystyki ----------------------------------------------------------------------
    @render.plot
    def plot_counts():
        df, n = log(), cfg()["people"]
        if df.empty:
            return no_data_fig()
        pv = (df.pivot_table(index="chore", columns="person_idx", values="id", aggfunc="count", fill_value=0)
              .reindex(columns=[0, 1], fill_value=0))
        pv = pv.loc[pv.sum(axis=1).sort_values().index]
        fig, ax = plt.subplots()
        y, h = np.arange(len(pv)), 0.4
        ax.barh(y + h / 2, pv[0], h, color=COLORS[0], label=n[0])
        ax.barh(y - h / 2, pv[1], h, color=COLORS[1], label=n[1])
        ax.set_yticks(y, pv.index)
        ax.set_xlabel("Liczba wykonań")
        ax.xaxis.set_major_locator(MaxNLocator(integer=True))
        ax.grid(axis="y", visible=False)
        ax.legend(loc="lower right")
        fig.tight_layout()
        return fig

    @render.plot
    def plot_balance():
        df, c = log(), cfg()
        n = c["people"]
        if df.empty:
            return no_data_fig()
        d = df.assign(ts=parse_ts(df["timestamp"])).dropna(subset=["ts"]).sort_values("ts")
        if d.empty:
            return no_data_fig()
        signed = pd.Series(np.where(d["person_idx"] == 0, d["weight"], -d["weight"]), index=d.index)
        st = c.get("settled_at")
        seg = (d["ts"] > ts_utc(st)).astype(int) if st else pd.Series(0, index=d.index)
        cum = signed.groupby(seg).cumsum().to_numpy()
        x = d["ts"].dt.tz_convert(TZ).dt.tz_localize(None).to_numpy()
        fig, ax = plt.subplots()
        ax.step(x, cum, where="post", color="#333", lw=2)
        ax.fill_between(x, cum, 0, where=cum >= 0, step="post", color=COLORS[0], alpha=0.3)
        ax.fill_between(x, cum, 0, where=cum <= 0, step="post", color=COLORS[1], alpha=0.3)
        for sgn in (1, -1):
            ax.axhline(sgn * c["danger"], color="#f4a261", ls="--", lw=1)
            ax.axhline(sgn * c["final"], color="#e63946", ls="--", lw=1)
        ax.axhline(0, color="#999", lw=0.8)
        if st:
            ax.axvline(ts_utc(st).tz_convert(TZ).tz_localize(None), color="#6c757d", ls=":")
        lim = max(c["final"] * 1.15, float(np.abs(cum).max()) * 1.1)
        ax.set_ylim(-lim, lim)
        ax.set_ylabel("Różnica punktów")
        ax.text(0.01, 0.98, f"▲ więcej robi {n[0]}", transform=ax.transAxes, va="top",
                color=COLORS[0], fontweight="bold")
        ax.text(0.01, 0.02, f"▼ więcej robi {n[1]}", transform=ax.transAxes, va="bottom",
                color=COLORS[1], fontweight="bold")
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m"))
        fig.autofmt_xdate()
        fig.tight_layout()
        return fig

    @render.plot
    def plot_daily():
        df, n = log(), cfg()["people"]
        if df.empty:
            return no_data_fig()
        pv = (df.pivot_table(index="date", columns="person_idx", values="weight", aggfunc="sum", fill_value=0)
              .reindex(columns=[0, 1], fill_value=0))
        pv.index = pd.to_datetime(pv.index)
        end = max(pd.Timestamp(today()), pv.index.max())
        pv = pv.reindex(pd.date_range(pv.index.min(), end, freq="D"), fill_value=0)
        fig, ax = plt.subplots()
        x, w = np.arange(len(pv)), 0.4
        ax.bar(x - w / 2, pv[0], w, color=COLORS[0], label=n[0])
        ax.bar(x + w / 2, pv[1], w, color=COLORS[1], label=n[1])
        step = max(1, len(pv) // 12)
        ax.set_xticks(x[::step], [d.strftime("%d.%m") for d in pv.index[::step]], rotation=45)
        ax.set_ylabel("Punkty")
        ax.grid(axis="x", visible=False)
        ax.legend()
        fig.tight_layout()
        return fig

    @render.data_frame
    def summary_table():
        df, n = log(), cfg()["people"]
        if df.empty:
            return pd.DataFrame({"Info": ["Brak danych"]})
        cnt = (df.pivot_table(index="chore", columns="person_idx", values="id", aggfunc="count", fill_value=0)
               .reindex(columns=[0, 1], fill_value=0))
        pts = (df.pivot_table(index="chore", columns="person_idx", values="weight", aggfunc="sum", fill_value=0)
               .reindex(columns=[0, 1], fill_value=0).reindex(cnt.index))
        out = pd.DataFrame({
            "Praca": cnt.index,
            f"{n[0]} (razy)": cnt[0].astype(int).values,
            f"{n[1]} (razy)": cnt[1].astype(int).values,
            f"{n[0]} (pkt)": pts[0].map(fmt).values,
            f"{n[1]} (pkt)": pts[1].map(fmt).values,
            "Najczęściej": np.where(cnt[0] > cnt[1], n[0], np.where(cnt[1] > cnt[0], n[1], "remis")),
        })
        out = out.iloc[(cnt[0] + cnt[1]).values.argsort()[::-1]]
        t0, t1 = int(cnt[0].sum()), int(cnt[1].sum())
        total = pd.DataFrame([{
            "Praca": "RAZEM", f"{n[0]} (razy)": t0, f"{n[1]} (razy)": t1,
            f"{n[0]} (pkt)": fmt(pts[0].sum()), f"{n[1]} (pkt)": fmt(pts[1].sum()),
            "Najczęściej": n[0] if t0 > t1 else n[1] if t1 > t0 else "remis",
        }])
        return render.DataGrid(pd.concat([out, total], ignore_index=True), width="100%")


app = App(app_ui, server)
