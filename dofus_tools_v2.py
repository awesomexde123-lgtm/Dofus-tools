from scapy.all import sniff, TCP, IP, Raw
import psutil, logging, traceback, re, threading, ctypes, time
import tkinter as tk
from tkinter import font as tkfont, messagebox, simpledialog
import json
import os
from pathlib import Path
import socket

# ── System Tray ───────────────────────────────────────────────────────────────
try:
    import pystray
    from PIL import Image, ImageDraw
    TRAY_AVAILABLE = True
except ImportError:
    TRAY_AVAILABLE = False
    print("[TRAY] pystray / Pillow no disponible. Instala con: pip install pystray Pillow")

# ── win32 opcional (Windows) ──────────────────────────────────────────────────
try:
    import win32gui, win32con, win32process, win32api
    WIN32_AVAILABLE = True
except ImportError:
    WIN32_AVAILABLE = False

# ─── Logging solo a archivo ───────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(message)s",
    handlers=[logging.FileHandler("dofus_monitor.log", encoding="utf-8")]
)
log = logging.getLogger(__name__)

# ─── Red ──────────────────────────────────────────────────────────────────────
SERVERS = {
    "Allisteria":  "34.253.140.241",
    "Allisteria2": "18.200.38.104",
    "Fallaster":   "34.255.49.243",
    "Fallaster2":  "54.228.180.96"
}
REMOTE_IPS  = set(SERVERS.values())
REMOTE_PORT = 443
BPF_FILTER  = f"tcp and port {REMOTE_PORT}"
WINDOW_KEYWORD = "Dofus Retro"

# ─── Regex para detectar hosts dofus ───────────────────────────────────────────
HOST_REGEX = re.compile(rb"dofusretro-[\w\-]+\.ankama-games\.com")

# ─── Estado global ────────────────────────────────────────────────────────────
all_detected            = {}
id_to_port              = {}
id_to_name              = {}
orden_personajes        = []
ultimo_emisor_nombre    = None
ultimo_lider_grupo_port = None
stop_monitor            = threading.Event()

# ─── Flags de features ───────────────────────────────────────────────────────
feature_autofocus = True
feature_autogroup = True
feature_autotrade = True


# ══════════════════════════════════════════════════════════════════════════════
#  System Tray Helper
# ══════════════════════════════════════════════════════════════════════════════
def _crear_icono_tray() -> "Image.Image":
    """
    Genera un icono 64×64 para el system tray con la estética del programa
    (círculo naranja con anillo interior, sobre fondo oscuro).
    """
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Fondo circular oscuro
    draw.ellipse([2, 2, size - 2, size - 2], fill=(15, 25, 35, 255))
    # Anillo exterior (naranja/acento)
    draw.ellipse([4, 4, size - 4, size - 4], outline=(245, 166, 35, 255), width=4)
    # Punto interior verde
    cx = size // 2
    draw.ellipse([cx - 8, cx - 8, cx + 8, cx + 8], fill=(0, 230, 118, 255))

    return img


class SystemTrayManager:
    """
    Gestiona el icono en la bandeja del sistema.
    Al minimizar la ventana principal se oculta y aparece el tray icon.
    Doble clic o "Restaurar" en el menú vuelve a mostrar la ventana.
    """

    def __init__(self, app_ref: "DofusToolsApp"):
        self.app   = app_ref
        self._icon = None
        self._thread = None

    # ── Crear y arrancar el icono ─────────────────────────────────────────────
    def start(self):
        if not TRAY_AVAILABLE:
            return
        if self._icon is not None:
            return  # ya corriendo

        menu = pystray.Menu(
            pystray.MenuItem("Dofus Tools", self._on_restore, default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Restaurar ventana", self._on_restore),
            pystray.MenuItem("Salir",             self._on_quit),
        )

        self._icon = pystray.Icon(
            name    = "DofusTools",
            icon    = _crear_icono_tray(),
            title   = "Dofus Tools · Monitor activo",
            menu    = menu,
        )

        # pystray.Icon.run() es bloqueante → hilo aparte
        self._thread = threading.Thread(target=self._icon.run, daemon=True)
        self._thread.start()
        log.info("[TRAY] Icono creado en bandeja del sistema")

    # ── Ocultar la ventana principal ─────────────────────────────────────────
    def hide_window(self):
        """Oculta la ventana y asegura que el tray icon esté activo."""
        self.start()           # no-op si ya corriendo
        self.app.withdraw()
        log.info("[TRAY] Ventana ocultada → tray activo")

    # ── Restaurar la ventana ─────────────────────────────────────────────────
    def restore_window(self, icon=None, item=None):
        """Restaura la ventana desde el tray (puede llamarse desde hilo de pystray)."""
        self.app.after(0, self._do_restore)

    _on_restore = restore_window   # alias para el MenuItem

    def _do_restore(self):
        self.app.deiconify()
        self.app.lift()
        self.app.focus_force()
        log.info("[TRAY] Ventana restaurada")

    # ── Salir completamente ──────────────────────────────────────────────────
    def _on_quit(self, icon=None, item=None):
        log.info("[TRAY] Salir solicitado desde tray")
        self.app.after(0, self.app._quit_app)

    # ── Detener el icono ─────────────────────────────────────────────────────
    def stop(self):
        if self._icon:
            try:
                self._icon.stop()
            except Exception:
                pass
            self._icon = None


# ══════════════════════════════════════════════════════════════════════════════
#  Atajos de teclado — Ctrl+Alt+1..0
# ══════════════════════════════════════════════════════════════════════════════
def presionar_atajo_slot(slot_num: int):
    """Simula Ctrl+Alt+(1-0) según el slot (1-10). slot_num 10 → tecla '0'."""
    if not WIN32_AVAILABLE:
        return
    tecla_num     = 0x30 if slot_num == 10 else (0x30 + slot_num)
    tecla_display = slot_num % 10
    print(f"[DEBUG] Intentando enviar Ctrl+Alt+{tecla_display} (Slot {slot_num}, vkCode=0x{tecla_num:02X})")
    log.info(f"[DEBUG] Intentando enviar Ctrl+Alt+{tecla_display} (Slot {slot_num}, vkCode=0x{tecla_num:02X})")
    try:
        win32api.keybd_event(0x11, 0, 0, 0)
        win32api.keybd_event(0x12, 0, 0, 0)
        win32api.keybd_event(tecla_num, 0, 0, 0)
        time.sleep(0.05)
        win32api.keybd_event(tecla_num, 0, win32con.KEYEVENTF_KEYUP, 0)
        win32api.keybd_event(0x12,     0, win32con.KEYEVENTF_KEYUP, 0)
        win32api.keybd_event(0x11,     0, win32con.KEYEVENTF_KEYUP, 0)
        print(f"[DEBUG] Ctrl+Alt+{tecla_display} enviado correctamente")
        log.info(f"[DEBUG] Ctrl+Alt+{tecla_display} enviado correctamente")
    except Exception as e:
        print(f"[DEBUG] ERROR al enviar Ctrl+Alt+{tecla_display}: {e}")
        log.error(f"[DEBUG] ERROR al enviar Ctrl+Alt+{tecla_display}: {e}")


# ══════════════════════════════════════════════════════════════════════════════
#  Gestor de Layouts compatible con Wintabber Dofus
# ══════════════════════════════════════════════════════════════════════════════
class LayoutManager:
    """
    Gestor de layouts compartido con Wintabber Dofus.
    Lee y escribe DIRECTAMENTE en el archivo de Wintabber, por lo que cualquier
    cambio hecho aquí aparece en Wintabber y viceversa.
    """

    _DIR  = "DofusMiniTabber"
    _FILES = [
        "window_positions.json",
        "window_positions",
    ]

    def __init__(self):
        self.config_path = self._resolve_config_path()
        print(f"[LAYOUT] Archivo compartido con Wintabber: {self.config_path}")
        log.info(f"[LAYOUT] config_path={self.config_path}")

    def _resolve_config_path(self) -> str:
        appdata = os.environ.get("APPDATA", os.path.expanduser("~"))
        base_dir = os.path.join(appdata, self._DIR)
        for filename in self._FILES:
            candidate = os.path.join(base_dir, filename)
            if os.path.exists(candidate):
                return candidate
        return os.path.join(base_dir, self._FILES[0])

    @property
    def config_dir(self) -> str:
        return os.path.dirname(self.config_path)

    @property
    def file_exists(self) -> bool:
        return os.path.exists(self.config_path)

    def get_available_layouts(self):
        try:
            if not self.file_exists:
                print(f"[LAYOUT] Archivo no encontrado: {self.config_path}")
                return {}
            with open(self.config_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            print(f"[LAYOUT] {len(data)} layout(s) leídos desde Wintabber")
            return data
        except Exception as e:
            print(f"[LAYOUT] Error al leer layouts: {e}")
            log.error(f"[LAYOUT] Error al leer layouts: {e}")
            return {}

    def _save_all_layouts(self, layouts: dict) -> bool:
        try:
            os.makedirs(self.config_dir, exist_ok=True)
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(layouts, f, indent=2, ensure_ascii=False)
            print(f"[LAYOUT] Guardado en archivo compartido: {self.config_path}")
            return True
        except Exception as e:
            print(f"[LAYOUT] Error al escribir layouts: {e}")
            log.error(f"[LAYOUT] Error al escribir layouts: {e}")
            return False

    def load_layout(self, layout_name):
        try:
            layouts = self.get_available_layouts()
            if layout_name not in layouts:
                print(f"[LAYOUT] Layout '{layout_name}' no encontrado")
                return None
            layout    = layouts[layout_name]
            positions = layout.get('Positions', [])
            layout_dict = {}
            for pos in positions:
                layout_dict[pos['WindowName']] = pos['Position']
            print(f"[LAYOUT] Layout '{layout_name}' cargado con {len(layout_dict)} posiciones")
            return layout_dict
        except Exception as e:
            print(f"[LAYOUT] Error al cargar layout '{layout_name}': {e}")
            log.error(f"[LAYOUT] Error al cargar layout '{layout_name}': {e}")
            return None

    def apply_layout_to_slots(self, layout_name):
        global orden_personajes
        layout = self.load_layout(layout_name)
        if not layout:
            return False

        print(f"[LAYOUT] Aplicando layout '{layout_name}'")
        vivos = {v["name"]: v for v in all_detected.values()}
        sorted_windows = sorted(layout.items(), key=lambda x: x[1])
        nuevo_orden = []
        personajes_no_encontrados = []

        for window_name, _ in sorted_windows:
            personaje_encontrado = None
            for personaje_detectado in vivos.keys():
                if (window_name.lower() in personaje_detectado.lower() or
                        personaje_detectado.lower() in window_name.lower()):
                    personaje_encontrado = personaje_detectado
                    break
            if personaje_encontrado and personaje_encontrado not in nuevo_orden:
                nuevo_orden.append(personaje_encontrado)
            else:
                personajes_no_encontrados.append(window_name)

        for personaje in vivos.keys():
            if personaje not in nuevo_orden:
                nuevo_orden.append(personaje)

        if nuevo_orden:
            orden_personajes[:] = nuevo_orden
            print(f"[LAYOUT] Nuevo orden de personajes:")
            for i, personaje in enumerate(nuevo_orden):
                print(f"[LAYOUT]   Slot {i+1}: {personaje}")
            if personajes_no_encontrados:
                print(f"[LAYOUT] Personajes del layout no encontrados: {personajes_no_encontrados}")
            if app:
                app.after(0, app.update_characters)
            print(f"[LAYOUT] Layout '{layout_name}' aplicado exitosamente")
            log.info(f"[LAYOUT] Layout '{layout_name}' aplicado con {len(nuevo_orden)} personajes")
            return True
        else:
            print(f"[LAYOUT] No se pudieron encontrar personajes para el layout")
            return False

    def save_current_layout(self, layout_name, description=""):
        global orden_personajes
        try:
            layouts = self.get_available_layouts()
            positions = [
                {"WindowName": personaje, "Position": i}
                for i, personaje in enumerate(orden_personajes)
            ]
            layouts[layout_name] = {
                "Name":        layout_name,
                "Positions":   positions,
                "CreatedAt":   time.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                "Description": description or f"Guardado desde Dofus Tools · {len(positions)} personajes"
            }
            ok = self._save_all_layouts(layouts)
            if ok:
                print(f"[LAYOUT] '{layout_name}' guardado ({len(positions)} personajes)")
            return ok
        except Exception as e:
            print(f"[LAYOUT] Error al guardar layout: {e}")
            log.error(f"[LAYOUT] Error al guardar layout: {e}")
            return False

    def delete_layout(self, layout_name):
        try:
            layouts = self.get_available_layouts()
            if layout_name not in layouts:
                print(f"[LAYOUT] Layout '{layout_name}' no existe")
                return False
            del layouts[layout_name]
            ok = self._save_all_layouts(layouts)
            if ok:
                print(f"[LAYOUT] '{layout_name}' eliminado")
            return ok
        except Exception as e:
            print(f"[LAYOUT] Error al eliminar layout: {e}")
            log.error(f"[LAYOUT] Error al eliminar layout: {e}")
            return False

    def import_layout_from_file(self, filepath):
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if isinstance(data, dict):
                if "Positions" in data:
                    name = data.get("Name", Path(filepath).stem)
                    layouts_raw = {name: data}
                else:
                    layouts_raw = data
            else:
                print(f"[LAYOUT] Formato de archivo no reconocido: {filepath}")
                return None, None, False

            resultados = {}
            for layout_name, layout_data in layouts_raw.items():
                positions    = layout_data.get("Positions", [])
                window_names = [p.get("WindowName", "") for p in positions]
                unique_names = set(window_names)
                es_generico  = (
                    len(unique_names) <= 1 or
                    all("Dofus Retro" in n for n in window_names)
                )
                resultados[layout_name] = (layout_data, es_generico)

            print(f"[LAYOUT] Importados {len(resultados)} layouts desde: {filepath}")
            return resultados

        except json.JSONDecodeError as e:
            print(f"[LAYOUT] JSON inválido en '{filepath}': {e}")
            return None
        except Exception as e:
            print(f"[LAYOUT] Error al importar '{filepath}': {e}")
            log.error(f"[LAYOUT] Error al importar: {e}")
            return None

    def merge_imported_layouts(self, layouts_importados):
        try:
            existentes = self.get_available_layouts()
            existentes.update(layouts_importados)
            os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(existentes, f, indent=2, ensure_ascii=False)
            print(f"[LAYOUT] {len(layouts_importados)} layout(s) fusionado(s) y guardado(s)")
            return True
        except Exception as e:
            print(f"[LAYOUT] Error al fusionar layouts: {e}")
            log.error(f"[LAYOUT] Error al fusionar layouts: {e}")
            return False

    def show_layout_menu(self):
        layouts = self.get_available_layouts()
        if not layouts:
            print("[LAYOUT] No hay layouts guardados en Wintabber Dofus")
            return
        print("\n" + "="*50)
        print("📋 LAYOUTS DISPONIBLES (Wintabber Dofus)")
        print("="*50)
        layout_names = list(layouts.keys())
        for i, name in enumerate(layout_names, 1):
            info          = layouts[name]
            description   = info.get('Description', 'Sin descripción')
            positions_cnt = len(info.get('Positions', []))
            created_at    = info.get('CreatedAt', 'Desconocido')
            print(f"{i:2d}. {name}")
            print(f"     📝 {description}")
            print(f"     🔢 {positions_cnt} posiciones")
            print(f"     📅 {created_at}")
            print()
        try:
            seleccion = input("Selecciona un layout (número): ").strip()
            if seleccion.lower() in ['q', 'salir', 'exit']:
                print("[LAYOUT] Operación cancelada")
                return
            idx = int(seleccion) - 1
            if 0 <= idx < len(layout_names):
                self.apply_layout_to_slots(layout_names[idx])
            else:
                print("[LAYOUT] Selección inválida")
        except ValueError:
            print("[LAYOUT] Entrada inválida.")
        except KeyboardInterrupt:
            print("\n[LAYOUT] Operación cancelada por el usuario")


# Instancia global del gestor de layouts
layout_manager = LayoutManager()


# ══════════════════════════════════════════════════════════════════════════════
#  Toggle switch estilo iOS
# ══════════════════════════════════════════════════════════════════════════════
class ToggleSwitch(tk.Canvas):
    W, H  = 44, 24
    ON_C  = "#00e676"
    OFF_C = "#2e4a5a"

    def __init__(self, parent, initial=True, command=None, bg="#1b2d3e", **kw):
        super().__init__(parent, width=self.W, height=self.H,
                         bg=bg, highlightthickness=0, cursor="hand2", **kw)
        self._on      = initial
        self._command = command
        self._knob_x  = self.W - self.H + 4 if initial else 4
        self._draw()
        self.bind("<Button-1>", self._click)

    def _draw(self):
        self.delete("all")
        color = self.ON_C if self._on else self.OFF_C
        r = self.H // 2
        self.create_oval(0, 0, self.H, self.H, fill=color, outline="")
        self.create_rectangle(r, 0, self.W - r, self.H, fill=color, outline="")
        self.create_oval(self.W - self.H, 0, self.W, self.H, fill=color, outline="")
        pad = 3
        x   = self._knob_x
        self.create_oval(x, pad, x + self.H - pad * 2, self.H - pad,
                         fill="#ffffff", outline="")

    def _animate(self, target, steps=6):
        delta = (target - self._knob_x) / steps
        def step(i=0):
            if i >= steps:
                self._knob_x = target
                self._draw()
                return
            self._knob_x += delta
            self._draw()
            self.after(16, lambda: step(i + 1))
        step()

    def _click(self, _=None):
        self._on = not self._on
        target   = self.W - self.H + 4 if self._on else 4
        self._animate(target)
        if self._command:
            self._command(self._on)

    def get(self):
        return self._on


# ══════════════════════════════════════════════════════════════════════════════
#  Fila arrastrable de personaje
# ══════════════════════════════════════════════════════════════════════════════
class DraggableCharRow(tk.Frame):
    CARD   = "#1b2d3e"
    BORDER = "#223344"

    def __init__(self, parent, name, server, slot_num, app_ref, **kwargs):
        super().__init__(parent, **kwargs)
        self.name = name
        self.app  = app_ref
        self.configure(bg=self.CARD, highlightthickness=1,
                        highlightbackground=self.BORDER, cursor="fleur")

        dot = tk.Canvas(self, width=10, height=10,
                        bg=self.CARD, highlightthickness=0)
        dot.pack(side="left", padx=(12, 8), pady=12)
        dot.create_oval(1, 1, 9, 9, fill="#00e676", outline="")

        self.lbl_name = tk.Label(self, text=name, bg=self.CARD, fg="#d0dde8",
                                  font=("Segoe UI", 9, "bold"))
        self.lbl_name.pack(side="left")

        self.lbl_slot = tk.Label(
            self, text=f"SLOT {slot_num}",
            bg="#1a4a2e", fg="#00e676",
            font=tkfont.Font(family="Segoe UI", size=7, weight="bold"),
            padx=6, pady=1
        )
        self.lbl_slot.pack(side="left", padx=8)

        tk.Label(self, text=server, bg=self.CARD, fg="#6a8a9a",
                 font=("Segoe UI", 8)).pack(side="right", padx=12)

        for w in (self, self.lbl_name, self.lbl_slot):
            w.bind("<Button-1>",        self._on_start)
            w.bind("<B1-Motion>",       self._on_drag)
            w.bind("<ButtonRelease-1>", self._on_drop)

    def _on_start(self, event):
        self.configure(highlightbackground="#f5a623")

    def _on_drag(self, event):
        y = self.winfo_y() + event.y
        self.app.reordenar_personajes(self.name, y)

    def _on_drop(self, event):
        self.configure(highlightbackground=self.BORDER)
        y_in_container = self.winfo_y() + event.y
        self.app.reordenar_personajes(self.name, y_in_container)


# ══════════════════════════════════════════════════════════════════════════════
#  Interfaz Gráfica de Layouts  (Toplevel completo)
# ══════════════════════════════════════════════════════════════════════════════
class LayoutManagerGUI(tk.Toplevel):
    BG       = "#0f1923"
    PANEL    = "#162230"
    CARD     = "#1b2d3e"
    BORDER   = "#223344"
    GREEN    = "#00e676"
    GREEN_DIM= "#1a4a2e"
    RED      = "#ff5252"
    RED_DIM  = "#4a1a1a"
    TEAL     = "#00bcd4"
    TEXT     = "#d0dde8"
    TEXT_DIM = "#6a8a9a"
    ACCENT   = "#f5a623"

    def __init__(self, parent, lm: LayoutManager):
        super().__init__(parent)
        self.lm = lm
        self.title("Gestor de Layouts · Dofus Tools")
        self.geometry("620x540")
        self.resizable(True, True)
        self.configure(bg=self.BG)
        self.transient(parent)
        self.grab_set()
        self._selected_name = None
        self._setup_ui()
        self._refresh_layouts()

    def _setup_ui(self):
        header = tk.Frame(self, bg=self.PANEL, height=56)
        header.pack(fill="x")
        header.pack_propagate(False)
        ih = tk.Frame(header, bg=self.PANEL)
        ih.pack(fill="both", expand=True, padx=20)
        tk.Label(ih, text="📋", bg=self.PANEL,
                 font=tkfont.Font(size=18)).pack(side="left", pady=12)
        tk.Label(ih, text="  Gestor de Layouts", bg=self.PANEL, fg=self.TEXT,
                 font=tkfont.Font(family="Segoe UI", size=13, weight="bold")
                 ).pack(side="left")
        tk.Label(ih, text="Wintabber Dofus", bg=self.PANEL, fg=self.TEXT_DIM,
                 font=tkfont.Font(family="Segoe UI", size=9)).pack(side="right")
        tk.Frame(self, bg=self.BORDER, height=1).pack(fill="x")

        body = tk.Frame(self, bg=self.BG)
        body.pack(fill="both", expand=True, padx=16, pady=12)

        left = tk.Frame(body, bg=self.BG)
        left.pack(side="left", fill="both", expand=True)

        tk.Label(left, text="LAYOUTS DISPONIBLES", bg=self.BG, fg=self.TEXT_DIM,
                 font=tkfont.Font(family="Segoe UI", size=7, weight="bold")
                 ).pack(anchor="w", pady=(0, 6))

        list_frame = tk.Frame(left, bg=self.CARD, highlightthickness=1,
                              highlightbackground=self.BORDER)
        list_frame.pack(fill="both", expand=True)

        scrollbar = tk.Scrollbar(list_frame, orient="vertical")
        scrollbar.pack(side="right", fill="y")

        self.listbox = tk.Listbox(
            list_frame,
            bg=self.CARD, fg=self.TEXT,
            selectbackground=self.TEAL, selectforeground="#ffffff",
            font=tkfont.Font(family="Segoe UI", size=10),
            relief="flat", bd=0,
            highlightthickness=0,
            yscrollcommand=scrollbar.set,
            activestyle="none"
        )
        self.listbox.pack(fill="both", expand=True)
        scrollbar.config(command=self.listbox.yview)
        self.listbox.bind("<<ListboxSelect>>", self._on_select)
        self.listbox.bind("<Double-Button-1>", lambda e: self._apply_layout())

        right = tk.Frame(body, bg=self.BG, width=200)
        right.pack(side="right", fill="y", padx=(14, 0))
        right.pack_propagate(False)

        tk.Label(right, text="DETALLES", bg=self.BG, fg=self.TEXT_DIM,
                 font=tkfont.Font(family="Segoe UI", size=7, weight="bold")
                 ).pack(anchor="w", pady=(0, 6))

        detail_card = tk.Frame(right, bg=self.CARD, highlightthickness=1,
                               highlightbackground=self.BORDER)
        detail_card.pack(fill="x")
        inner = tk.Frame(detail_card, bg=self.CARD)
        inner.pack(fill="x", padx=10, pady=10)

        self.lbl_name  = tk.Label(inner, text="—", bg=self.CARD, fg=self.TEXT,
                                   font=tkfont.Font(family="Segoe UI", size=10, weight="bold"),
                                   wraplength=170, justify="left")
        self.lbl_name.pack(anchor="w")

        self.lbl_desc  = tk.Label(inner, text="", bg=self.CARD, fg=self.TEXT_DIM,
                                   font=tkfont.Font(family="Segoe UI", size=8),
                                   wraplength=170, justify="left")
        self.lbl_desc.pack(anchor="w", pady=(4, 0))

        self.lbl_slots = tk.Label(inner, text="", bg=self.CARD, fg=self.GREEN,
                                   font=tkfont.Font(family="Segoe UI", size=8))
        self.lbl_slots.pack(anchor="w", pady=(4, 0))

        self.lbl_date  = tk.Label(inner, text="", bg=self.CARD, fg=self.TEXT_DIM,
                                   font=tkfont.Font(family="Segoe UI", size=7))
        self.lbl_date.pack(anchor="w", pady=(2, 0))

        tk.Frame(right, bg=self.BORDER, height=1).pack(fill="x", pady=10)

        btn_cfg = dict(relief="flat", cursor="hand2",
                       font=tkfont.Font(family="Segoe UI", size=9, weight="bold"),
                       pady=7)

        self.btn_apply = tk.Button(right, text="▶  APLICAR LAYOUT",
                                   bg=self.TEAL, fg="white",
                                   command=self._apply_layout,
                                   state="disabled", **btn_cfg)
        self.btn_apply.pack(fill="x", pady=(0, 6))

        self.btn_delete = tk.Button(right, text="🗑  ELIMINAR",
                                    bg=self.RED_DIM, fg=self.RED,
                                    command=self._delete_layout,
                                    state="disabled", **btn_cfg)
        self.btn_delete.pack(fill="x", pady=(0, 6))

        tk.Frame(right, bg=self.BORDER, height=1).pack(fill="x", pady=6)

        tk.Button(right, text="💾  GUARDAR ACTUAL",
                  bg=self.GREEN_DIM, fg=self.GREEN,
                  command=self._save_current_layout,
                  **btn_cfg).pack(fill="x", pady=(0, 6))

        tk.Button(right, text="🔄  ACTUALIZAR",
                  bg=self.PANEL, fg=self.TEXT_DIM,
                  command=self._refresh_layouts,
                  **btn_cfg).pack(fill="x", pady=(0, 6))

        tk.Frame(right, bg=self.BORDER, height=1).pack(fill="x", pady=6)

        tk.Button(right, text="📂  IMPORTAR JSON",
                  bg="#1a3a4a", fg=self.TEAL,
                  command=self._import_json,
                  **btn_cfg).pack(fill="x")

        tk.Frame(self, bg=self.BORDER, height=1).pack(fill="x")
        status_bar = tk.Frame(self, bg=self.PANEL, height=28)
        status_bar.pack(fill="x")
        status_bar.pack_propagate(False)
        self.lbl_status = tk.Label(status_bar, text="Listo.", bg=self.PANEL,
                                    fg=self.TEXT_DIM,
                                    font=tkfont.Font(family="Segoe UI", size=8))
        self.lbl_status.pack(side="left", padx=12)

    def _refresh_layouts(self):
        self._layouts = self.lm.get_available_layouts()
        self.listbox.delete(0, "end")
        for name in self._layouts:
            self.listbox.insert("end", f"  {name}")
        count = len(self._layouts)
        self._set_status(f"{count} layout{'s' if count != 1 else ''} encontrado{'s' if count != 1 else ''}.")
        self._selected_name = None
        self._clear_details()
        self.btn_apply.config(state="disabled")
        self.btn_delete.config(state="disabled")

    def _on_select(self, event=None):
        sel = self.listbox.curselection()
        if not sel:
            return
        name = list(self._layouts.keys())[sel[0]]
        self._selected_name = name
        info = self._layouts[name]

        positions = info.get("Positions", [])
        desc      = info.get("Description", "Sin descripción")
        date      = info.get("CreatedAt", "")[:10]

        self.lbl_name.config(text=name)
        self.lbl_desc.config(text=desc)
        self.lbl_slots.config(text=f"🔢 {len(positions)} personajes")
        self.lbl_date.config(text=f"📅 {date}")

        self.btn_apply.config(state="normal")
        self.btn_delete.config(state="normal")
        self._set_status(f"Seleccionado: {name}")

    def _clear_details(self):
        self.lbl_name.config(text="—")
        self.lbl_desc.config(text="")
        self.lbl_slots.config(text="")
        self.lbl_date.config(text="")

    def _apply_layout(self):
        if not self._selected_name:
            return
        ok = self.lm.apply_layout_to_slots(self._selected_name)
        if ok:
            self._set_status(f"✔ Layout '{self._selected_name}' aplicado.")
            messagebox.showinfo("Layout aplicado",
                                f"Layout '{self._selected_name}' aplicado correctamente.",
                                parent=self)
        else:
            self._set_status("✖ No se pudo aplicar el layout.")
            messagebox.showwarning("Sin coincidencias",
                                   "No se encontraron personajes que coincidan con el layout.\n"
                                   "Asegúrate de tener personajes conectados.",
                                   parent=self)

    def _delete_layout(self):
        if not self._selected_name:
            return
        confirm = messagebox.askyesno(
            "Eliminar layout",
            f"¿Seguro que quieres eliminar '{self._selected_name}'?",
            parent=self
        )
        if confirm:
            ok = self.lm.delete_layout(self._selected_name)
            if ok:
                self._set_status(f"Layout '{self._selected_name}' eliminado.")
                self._refresh_layouts()
            else:
                self._set_status("✖ No se pudo eliminar el layout.")

    def _save_current_layout(self):
        if not orden_personajes:
            messagebox.showwarning("Sin personajes",
                                   "No hay personajes activos para guardar.",
                                   parent=self)
            return
        name = simpledialog.askstring(
            "Guardar layout",
            "Nombre del nuevo layout:",
            parent=self
        )
        if not name or not name.strip():
            return
        name = name.strip()
        desc = simpledialog.askstring(
            "Descripción (opcional)",
            "Descripción del layout:",
            parent=self
        ) or ""
        ok = self.lm.save_current_layout(name, desc)
        if ok:
            self._set_status(f"✔ Layout '{name}' guardado.")
            self._refresh_layouts()
            messagebox.showinfo("Guardado", f"Layout '{name}' guardado correctamente.", parent=self)
        else:
            self._set_status("✖ Error al guardar el layout.")
            messagebox.showerror("Error", "No se pudo guardar el layout.", parent=self)

    def _import_json(self):
        from tkinter import filedialog
        filepath = filedialog.askopenfilename(
            parent=self,
            title="Importar JSON de Wintabber Dofus",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")]
        )
        if not filepath:
            return

        resultados = self.lm.import_layout_from_file(filepath)
        if not resultados:
            messagebox.showerror("Error de importación",
                                 "No se pudo leer el archivo JSON.\n"
                                 "Verifica que sea un JSON válido de Wintabber Dofus.",
                                 parent=self)
            return

        layouts_a_guardar = {}
        for layout_name, (layout_data, es_generico) in resultados.items():
            positions = layout_data.get("Positions", [])
            if es_generico and positions:
                resultado_asignado = self._assign_generic_layout(
                    layout_name, len(positions)
                )
                if resultado_asignado is None:
                    continue
                new_positions = [
                    {"WindowName": nombre, "Position": i}
                    for i, nombre in enumerate(resultado_asignado)
                ]
                layout_data = dict(layout_data)
                layout_data["Positions"] = new_positions
                layouts_a_guardar[layout_name] = layout_data
            else:
                layouts_a_guardar[layout_name] = layout_data

        if not layouts_a_guardar:
            self._set_status("Importación cancelada.")
            return

        ok = self.lm.merge_imported_layouts(layouts_a_guardar)
        if ok:
            count = len(layouts_a_guardar)
            self._set_status(f"✔ {count} layout(s) importado(s) correctamente.")
            self._refresh_layouts()
            messagebox.showinfo(
                "Importación exitosa",
                f"{count} layout(s) importado(s) correctamente:\n"
                + "\n".join(f"  • {n}" for n in layouts_a_guardar),
                parent=self
            )
        else:
            self._set_status("✖ Error al guardar los layouts importados.")
            messagebox.showerror("Error", "No se pudieron guardar los layouts.", parent=self)

    def _assign_generic_layout(self, layout_name: str, num_slots: int):
        personajes_activos = [v["name"] for v in all_detected.values()]

        win = tk.Toplevel(self)
        win.title(f"Asignar personajes — {layout_name}")
        win.geometry("480x120")
        win.resizable(False, False)
        win.configure(bg=self.BG)
        win.transient(self)
        win.grab_set()

        altura = 80 + num_slots * 38 + 60
        win.geometry(f"480x{min(altura, 600)}")

        resultado = [None]

        tk.Label(win, bg=self.PANEL,
                 text=f"  📋  El layout '{layout_name}' tiene {num_slots} slots genéricos.",
                 fg=self.TEXT, font=tkfont.Font(family="Segoe UI", size=9),
                 anchor="w").pack(fill="x")
        tk.Label(win,
                 text="  Asigna qué personaje va en cada slot (deja en blanco para omitir).",
                 bg=self.PANEL, fg=self.TEXT_DIM,
                 font=tkfont.Font(family="Segoe UI", size=8),
                 anchor="w").pack(fill="x")
        tk.Frame(win, bg=self.BORDER, height=1).pack(fill="x", pady=(0, 8))

        canvas_w    = tk.Canvas(win, bg=self.BG, highlightthickness=0, bd=0)
        scrollbar_w = tk.Scrollbar(win, orient="vertical", command=canvas_w.yview)
        canvas_w.configure(yscrollcommand=scrollbar_w.set)
        scrollbar_w.pack(side="right", fill="y")
        canvas_w.pack(fill="both", expand=True)

        slots_frame = tk.Frame(canvas_w, bg=self.BG)
        canvas_w.create_window((0, 0), window=slots_frame, anchor="nw")
        slots_frame.bind("<Configure>",
                         lambda e: canvas_w.configure(scrollregion=canvas_w.bbox("all")))

        combos  = []
        opciones = [""] + personajes_activos

        for i in range(num_slots):
            row = tk.Frame(slots_frame, bg=self.BG)
            row.pack(fill="x", padx=16, pady=3)

            tk.Label(row, text=f"Slot {i + 1}", bg=self.BG, fg=self.TEXT,
                     font=tkfont.Font(family="Segoe UI", size=9, weight="bold"),
                     width=8, anchor="w").pack(side="left")

            var = tk.StringVar(win)
            if i < len(personajes_activos):
                var.set(personajes_activos[i])
            else:
                var.set("")

            combo = tk.OptionMenu(row, var, *opciones)
            combo.config(bg=self.CARD, fg=self.TEXT, activebackground=self.TEAL,
                         activeforeground="white", relief="flat",
                         font=tkfont.Font(family="Segoe UI", size=9),
                         highlightthickness=0, bd=0, width=22)
            combo["menu"].config(bg=self.CARD, fg=self.TEXT,
                                  activebackground=self.TEAL, activeforeground="white")
            combo.pack(side="left", padx=(8, 0))
            combos.append(var)

        tk.Frame(win, bg=self.BORDER, height=1).pack(fill="x", pady=(8, 0))
        btn_row = tk.Frame(win, bg=self.PANEL)
        btn_row.pack(fill="x")

        def _confirmar():
            asignados = [v.get().strip() for v in combos]
            while asignados and asignados[-1] == "":
                asignados.pop()
            resultado[0] = asignados if asignados else None
            win.destroy()

        def _cancelar():
            resultado[0] = None
            win.destroy()

        tk.Button(btn_row, text="✔  CONFIRMAR", bg=self.TEAL, fg="white",
                  font=tkfont.Font(family="Segoe UI", size=9, weight="bold"),
                  relief="flat", cursor="hand2", padx=16, pady=6,
                  command=_confirmar).pack(side="right", padx=12, pady=6)

        tk.Button(btn_row, text="Cancelar", bg=self.PANEL, fg=self.TEXT_DIM,
                  font=tkfont.Font(family="Segoe UI", size=9),
                  relief="flat", cursor="hand2", padx=10, pady=6,
                  command=_cancelar).pack(side="right", pady=6)

        win.wait_window()
        return resultado[0]

    def _set_status(self, msg: str):
        self.lbl_status.config(text=msg)


# ══════════════════════════════════════════════════════════════════════════════
#  Funciones públicas para abrir la GUI de layouts
# ══════════════════════════════════════════════════════════════════════════════
def abrir_gestor_layouts():
    if app:
        LayoutManagerGUI(app, layout_manager)
    else:
        messagebox.showerror("Error", "La interfaz gráfica no está disponible.")


def cargar_layout_wintabber():
    if app:
        LayoutManagerGUI(app, layout_manager)
    else:
        layout_manager.show_layout_menu()


# ══════════════════════════════════════════════════════════════════════════════
#  Aplicación principal
# ══════════════════════════════════════════════════════════════════════════════
class DofusToolsApp(tk.Tk):
    BG        = "#0f1923"
    PANEL     = "#162230"
    CARD      = "#1b2d3e"
    BORDER    = "#223344"
    GREEN     = "#00e676"
    GREEN_DIM = "#1a4a2e"
    RED       = "#ff5252"
    RED_DIM   = "#4a1a1a"
    TEAL      = "#00bcd4"
    TEXT      = "#d0dde8"
    TEXT_DIM  = "#6a8a9a"
    ACCENT    = "#f5a623"

    WIN_W = 460
    WIN_H = 640

    def __init__(self):
        super().__init__()
        self.title("Dofus Tools")
        self.geometry(f"{self.WIN_W}x{self.WIN_H}")
        self.resizable(False, False)
        self.configure(bg=self.BG)
        self._setup_fonts()
        self._build_ui()

        # ── System Tray ──────────────────────────────────────────────────────
        self.tray = SystemTrayManager(self)
        if TRAY_AVAILABLE:
            # Arrancar el icono en background desde el inicio
            self.tray.start()
            # Interceptar el botón minimizar de la barra de título
            self.bind("<Unmap>", self._on_unmap)
        else:
            log.warning("[TRAY] pystray/Pillow no disponibles; minimizar funcionará normalmente.")

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── Manejo de minimizar → ir al tray ─────────────────────────────────────
    def _on_unmap(self, event):
        """
        Se dispara cuando la ventana se iconifica (minimiza).
        Solo actúa si el widget es la ventana raíz (no un hijo oculto).
        """
        if event.widget is self:
            # Pequeño delay para que Tk termine la animación de minimizar
            self.after(100, self.tray.hide_window)

    # ── Salida limpia ─────────────────────────────────────────────────────────
    def _quit_app(self):
        """Cierra todo: tray, monitor de red y la ventana."""
        stop_monitor.set()
        self.tray.stop()
        self.destroy()

    def _on_close(self):
        """Clic en la X → igual que salir desde el tray."""
        self._quit_app()

    # ── Resto de métodos sin cambios ──────────────────────────────────────────
    def _setup_fonts(self):
        self.font_title   = tkfont.Font(family="Segoe UI", size=14, weight="bold")
        self.font_section = tkfont.Font(family="Segoe UI", size=7,  weight="bold")
        self.font_label   = tkfont.Font(family="Segoe UI", size=9,  weight="bold")
        self.font_sub     = tkfont.Font(family="Segoe UI", size=8)
        self.font_badge   = tkfont.Font(family="Segoe UI", size=7,  weight="bold")
        self.font_char    = tkfont.Font(family="Segoe UI", size=9)

    def _section_label(self, parent, text):
        f = tk.Frame(parent, bg=self.BG)
        f.pack(fill="x", padx=18, pady=(14, 4))
        tk.Label(f, text=text, bg=self.BG, fg=self.TEXT_DIM,
                 font=self.font_section).pack(side="left")
        tk.Frame(f, bg=self.BORDER, height=1).pack(
            side="left", fill="x", expand=True, padx=(8, 0))

    def _make_feature_card(self, parent, icon, title, subtitle,
                           initial_on, on_toggle):
        card = tk.Frame(parent, bg=self.CARD, highlightthickness=1,
                        highlightbackground=self.BORDER)
        card.pack(fill="x", padx=18, pady=4)
        inner = tk.Frame(card, bg=self.CARD)
        inner.pack(fill="x", padx=14, pady=10)

        tk.Label(inner, text=icon, bg=self.CARD, fg=self.TEAL,
                 font=tkfont.Font(size=15)).pack(side="left", padx=(0, 12))

        txt = tk.Frame(inner, bg=self.CARD)
        txt.pack(side="left", fill="x", expand=True)
        row = tk.Frame(txt, bg=self.CARD)
        row.pack(fill="x")
        tk.Label(row, text=title, bg=self.CARD, fg=self.TEXT,
                 font=self.font_label).pack(side="left")

        badge = tk.Label(
            row,
            text="ENCENDIDO" if initial_on else "APAGADO",
            bg=self.GREEN_DIM if initial_on else self.RED_DIM,
            fg=self.GREEN     if initial_on else self.RED,
            font=self.font_badge, padx=6, pady=1
        )
        badge.pack(side="left", padx=8)
        tk.Label(txt, text=subtitle, bg=self.CARD, fg=self.TEXT_DIM,
                 font=self.font_sub).pack(anchor="w")

        def _wrapped(state, b=badge, cb=on_toggle):
            b.configure(
                text="ENCENDIDO" if state else "APAGADO",
                bg=self.GREEN_DIM if state else self.RED_DIM,
                fg=self.GREEN     if state else self.RED
            )
            cb(state)

        toggle = ToggleSwitch(inner, initial=initial_on,
                              command=_wrapped, bg=self.CARD)
        toggle.pack(side="right")
        return card, badge, toggle

    def _build_ui(self):
        header = tk.Frame(self, bg=self.PANEL, height=64)
        header.pack(fill="x")
        header.pack_propagate(False)
        ih = tk.Frame(header, bg=self.PANEL)
        ih.pack(fill="both", expand=True, padx=20)

        lc = tk.Canvas(ih, width=32, height=32, bg=self.PANEL,
                       highlightthickness=0)
        lc.pack(side="left", pady=16)
        lc.create_oval(2, 2, 30, 30, fill=self.ACCENT, outline="")
        lc.create_oval(9, 9, 23, 23, fill=self.PANEL, outline="")

        tk.Label(ih, text="Dofus Tools", bg=self.PANEL, fg=self.TEXT,
                 font=self.font_title).pack(side="left", padx=10)

        of = tk.Frame(ih, bg=self.PANEL)
        of.pack(side="right")
        tk.Label(of, text="ONLINE", bg=self.PANEL, fg=self.GREEN,
                 font=self.font_badge).pack(side="left")
        oc = tk.Canvas(of, width=12, height=12, bg=self.PANEL,
                       highlightthickness=0)
        oc.pack(side="left", padx=(4, 0))
        oc.create_oval(1, 1, 11, 11, fill=self.GREEN, outline="")

        tk.Frame(self, bg=self.BORDER, height=1).pack(fill="x")

        canvas = tk.Canvas(self, bg=self.BG, highlightthickness=0,
                            bd=0, yscrollincrement=1)
        sb = tk.Scrollbar(self, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        self.scroll_frame = tk.Frame(canvas, bg=self.BG)
        win_id = canvas.create_window((0, 0), window=self.scroll_frame,
                                      anchor="nw", width=self.WIN_W)
        self.scroll_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>",
                    lambda e: canvas.itemconfig(win_id, width=e.width))
        for w in (self.scroll_frame, canvas):
            w.bind("<MouseWheel>",
                   lambda e: canvas.yview_scroll(-1*(e.delta//120), "units"))

        self._build_content(self.scroll_frame)

    def _build_content(self, parent):
        global feature_autofocus, feature_autogroup, feature_autotrade

        self._section_label(parent, "ESTADO GENERAL")
        estado_card = tk.Frame(parent, bg=self.CARD, highlightthickness=1,
                               highlightbackground=self.BORDER)
        estado_card.pack(fill="x", padx=18, pady=(0, 4))

        self._estado_labels = {}
        for key, label in [("autofocus", "Auto-focus"),
                            ("autogroup", "Auto-group"),
                            ("autotrade", "Auto-trade")]:
            row = tk.Frame(estado_card, bg=self.CARD)
            row.pack(fill="x", padx=14, pady=5)
            tk.Label(row, text=label, bg=self.CARD, fg=self.TEXT,
                     font=self.font_label).pack(side="left")
            lbl = tk.Label(row, text="ACTIVO",
                           bg=self.GREEN_DIM, fg=self.GREEN,
                           font=self.font_badge, padx=7, pady=2)
            lbl.pack(side="right")
            self._estado_labels[key] = lbl

        self._section_label(parent, "COMBAT")

        def _on_autofocus(state):
            global feature_autofocus
            feature_autofocus = state
            self._update_estado("autofocus", state)

        def _on_autogroup(state):
            global feature_autogroup
            feature_autogroup = state
            self._update_estado("autogroup", state)

        self._make_feature_card(
            parent, "⚔", "Auto-focus",
            "Ctrl+Alt+1..0 en tu turno según orden de slots",
            initial_on=True, on_toggle=_on_autofocus)

        self._make_feature_card(
            parent, "👥", "Auto-group",
            "Responde a invitaciones de grupo automáticamente",
            initial_on=True, on_toggle=_on_autogroup)

        self._section_label(parent, "INTERCAMBIO")

        def _on_autotrade(state):
            global feature_autotrade
            feature_autotrade = state
            self._update_estado("autotrade", state)

        self._make_feature_card(
            parent, "🤝", "Auto-trade",
            "Cambia de pantalla al recibir una solicitud de intercambio",
            initial_on=True, on_toggle=_on_autotrade)

        self._section_label(parent, "LAYOUTS WINTABBER DOFUS")

        layout_card = tk.Frame(parent, bg=self.CARD, highlightthickness=1,
                               highlightbackground=self.BORDER)
        layout_card.pack(fill="x", padx=18, pady=(4, 4))
        layout_inner = tk.Frame(layout_card, bg=self.CARD)
        layout_inner.pack(fill="x", padx=14, pady=12)

        tk.Label(layout_inner, text="📋", bg=self.CARD, fg=self.ACCENT,
                 font=tkfont.Font(size=16)).pack(side="left", padx=(0, 12))

        text_frame = tk.Frame(layout_inner, bg=self.CARD)
        text_frame.pack(side="left", fill="x", expand=True)
        tk.Label(text_frame, text="Cargador de Layouts", bg=self.CARD, fg=self.TEXT,
                 font=self.font_label).pack(anchor="w")
        tk.Label(text_frame, text="Carga layouts guardados en Wintabber Dofus",
                 bg=self.CARD, fg=self.TEXT_DIM, font=self.font_sub).pack(anchor="w")

        cargar_btn = tk.Button(
            layout_inner, text="GESTOR DE LAYOUTS",
            command=abrir_gestor_layouts,
            bg=self.TEAL, fg="white", font=self.font_label,
            padx=15, pady=5, relief="flat", cursor="hand2"
        )
        cargar_btn.pack(side="right", padx=(10, 0))
        cargar_btn.bind("<Enter>", lambda e: cargar_btn.config(bg="#0097a7"))
        cargar_btn.bind("<Leave>", lambda e: cargar_btn.config(bg=self.TEAL))

        self._section_label(parent, "SESIONES ACTIVAS  ·  ARRASTRA PARA REORDENAR SLOTS")
        sess_card = tk.Frame(parent, bg=self.CARD, highlightthickness=1,
                             highlightbackground=self.BORDER)
        sess_card.pack(fill="x", padx=18, pady=(4, 18))

        hdr = tk.Frame(sess_card, bg=self.BORDER)
        hdr.pack(fill="x")
        tk.Label(hdr, text="PERSONAJE", bg=self.BORDER, fg=self.TEXT_DIM,
                 font=self.font_section, pady=6).pack(side="left", padx=12)
        tk.Label(hdr, text="SERVIDOR", bg=self.BORDER, fg=self.TEXT_DIM,
                 font=self.font_section, pady=6).pack(side="right", padx=12)

        self.chars_frame  = tk.Frame(sess_card, bg=self.CARD)
        self.chars_frame.pack(fill="x", padx=4, pady=4)
        self.char_widgets = {}

        disc = tk.Frame(parent, bg="#1a2a1a", highlightthickness=1,
                        highlightbackground="#2a4a2a")
        disc.pack(fill="x", padx=18, pady=(4, 18))
        disc_inner = tk.Frame(disc, bg="#1a2a1a")
        disc_inner.pack(fill="x", padx=12, pady=10)
        tk.Label(disc_inner, text="⚠", bg="#1a2a1a", fg=self.ACCENT,
                 font=tkfont.Font(size=13)).pack(side="left", padx=(0, 10))
        tk.Label(
            disc_inner,
            text="Si ya estás conectado al servidor,\ndeberás reconectar tu personaje para que el monitor funcione.",
            bg="#1a2a1a", fg="#8aaa8a",
            font=tkfont.Font(family="Segoe UI", size=8),
            justify="left"
        ).pack(side="left")

    def _update_estado(self, key: str, active: bool):
        lbl = self._estado_labels.get(key)
        if lbl:
            lbl.configure(
                text="ACTIVO"     if active else "INACTIVO",
                bg=self.GREEN_DIM if active else self.RED_DIM,
                fg=self.GREEN     if active else self.RED
            )

    def update_characters(self):
        global orden_personajes

        vivos = {v["name"]: v for v in all_detected.values()}

        orden_personajes = [n for n in orden_personajes if n in vivos]
        for n in vivos:
            if n not in orden_personajes:
                orden_personajes.append(n)

        for name in list(self.char_widgets.keys()):
            if name not in vivos:
                self.char_widgets[name].destroy()
                del self.char_widgets[name]

        for name, info in vivos.items():
            if name not in self.char_widgets:
                row = DraggableCharRow(
                    self.chars_frame, name, info.get("server", ""),
                    slot_num=1, app_ref=self
                )
                self.char_widgets[name] = row

        for idx, name in enumerate(orden_personajes):
            row = self.char_widgets[name]
            row.pack_forget()
            row.pack(fill="x", pady=2)
            row.lbl_slot.config(text=f"SLOT {idx + 1}")

    def reordenar_personajes(self, name: str, y_in_container: int):
        global orden_personajes

        widgets  = list(self.chars_frame.winfo_children())
        nuevo_idx = len(widgets)
        for i, w in enumerate(widgets):
            if y_in_container < w.winfo_y() + w.winfo_height() // 2:
                nuevo_idx = i
                break

        if name in orden_personajes:
            orden_personajes.remove(name)
            nuevo_idx = min(nuevo_idx, len(orden_personajes))
            orden_personajes.insert(nuevo_idx, name)

        self.update_characters()
        self.update_idletasks()


# ─── Instancia global ─────────────────────────────────────────────────────────
app: DofusToolsApp = None


# ══════════════════════════════════════════════════════════════════════════════
#  Win32 helpers
# ══════════════════════════════════════════════════════════════════════════════
def get_pid_by_port(local_port: int):
    try:
        for conn in psutil.net_connections(kind="tcp"):
            if conn.laddr.port == local_port and conn.pid:
                return conn.pid
    except:
        return None


def get_hwnd_by_pid_tree(pid: int):
    if not WIN32_AVAILABLE:
        return None
    try:
        proc   = psutil.Process(pid)
        parent = proc.parent() or proc
        result = None
        def enum_handler(hwnd, _):
            nonlocal result
            if result or not win32gui.IsWindowVisible(hwnd): return
            _, found_pid = win32process.GetWindowThreadProcessId(hwnd)
            if found_pid == parent.pid and WINDOW_KEYWORD in win32gui.GetWindowText(hwnd):
                result = hwnd
        win32gui.EnumWindows(enum_handler, None)
        return result
    except:
        return None


def _set_foreground(hwnd: int) -> bool:
    if not WIN32_AVAILABLE:
        return False
    try:
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        cur_t    = ctypes.windll.kernel32.GetCurrentThreadId()
        tgt_t, _ = win32process.GetWindowThreadProcessId(hwnd)
        attached = False
        if cur_t != tgt_t:
            try:
                win32process.AttachThreadInput(cur_t, tgt_t, True)
                attached = True
            except: pass
        try:
            win32gui.BringWindowToTop(hwnd)
            win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
            win32gui.SetForegroundWindow(hwnd)
        except:
            win32api.keybd_event(0, 0, 0, 0)
            win32gui.SetForegroundWindow(hwnd)
        finally:
            if attached:
                try: win32process.AttachThreadInput(cur_t, tgt_t, False)
                except: pass
        return True
    except:
        return False


def activar_ventana_por_puerto(local_port: int) -> bool:
    pid = get_pid_by_port(local_port)
    if not pid: return False
    hwnd = get_hwnd_by_pid_tree(pid)
    if not hwnd: return False
    return _set_foreground(hwnd)


# ══════════════════════════════════════════════════════════════════════════════
#  Lógica de red
# ══════════════════════════════════════════════════════════════════════════════
def get_server_name(ip: str) -> str:
    for name, sip in SERVERS.items():
        if sip == ip: return name
    return ip


def _registrar_personaje(char_id_str, local_port, nombre, server):
    if char_id_str not in all_detected or all_detected[char_id_str]["port"] != local_port:
        all_detected[char_id_str] = {"name": nombre, "port": local_port, "server": server}
        id_to_port[char_id_str]   = local_port
        id_to_name[char_id_str]   = nombre
        log.info(f"[{server}] {nombre} registrado")
        if app:
            app.after(0, app.update_characters)


def process_message(packet) -> None:
    global ultimo_emisor_nombre, ultimo_lider_grupo_port
    if stop_monitor.is_set(): return
    if not (packet.haslayer(IP) and packet.haslayer(TCP) and packet.haslayer(Raw)): return

    data = packet[Raw].load

    match = HOST_REGEX.search(data)
    if match:
        host = match.group().decode()
        try:
            ip = socket.gethostbyname(host)
            if ip not in REMOTE_IPS:
                REMOTE_IPS.add(ip)
                nombre_server = host.split('-')[1].split('.')[0].capitalize() if '-' in host else host
                SERVERS[nombre_server] = ip
                log.info(f"🆕 Nuevo servidor detectado: {host} -> {ip}")
                print(f"🆕 Nuevo servidor detectado: {host} -> {ip}")
        except Exception as e:
            log.error(f"Error resolviendo {host}: {e}")

    ip_layer  = packet[IP]
    tcp_layer = packet[TCP]
    src_ip    = ip_layer.src

    if src_ip not in REMOTE_IPS and ip_layer.dst not in REMOTE_IPS:
        return

    local_port = tcp_layer.dport if src_ip in REMOTE_IPS else tcp_layer.sport
    server     = get_server_name(src_ip if src_ip in REMOTE_IPS else ip_layer.dst)

    for cid, name in re.findall(rb"ASK\|(\d+)\|([^|]+)\|", data):
        _registrar_personaje(cid.decode(), local_port,
                             name.decode(errors="ignore"), server)

    if feature_autofocus:
        for char_id in re.findall(rb"GTS(\d+)\|", data):
            cid_str = char_id.decode()
            if cid_str in id_to_port and local_port == id_to_port[cid_str]:
                nombre = id_to_name.get(cid_str)
                log.info(f"Turno: {nombre}")
                print(f"[DEBUG] Turno detectado → personaje='{nombre}' | orden={orden_personajes}")
                if nombre and nombre in orden_personajes:
                    slot = orden_personajes.index(nombre) + 1
                    print(f"[DEBUG] '{nombre}' → slot {slot}")
                    presionar_atajo_slot(slot)
                else:
                    print(f"[DEBUG] '{nombre}' NO en orden_personajes → fallback")
                    activar_ventana_por_puerto(local_port)

    if feature_autotrade:
        matches_erk = re.findall(rb"ERK(\d+)\|(\d+)\|", data)
        if matches_erk:
            print(f"[DEBUG-TRADE] Paquete ERK encontrado: {matches_erk}")
        elif b"ERK" in data:
            print(f"[DEBUG-TRADE] 'ERK' en data pero regex no matcheó. Raw: {data!r}")

        for emisor_id, receptor_id in matches_erk:
            emi_str, rec_str = emisor_id.decode(), receptor_id.decode()
            emi_nombre = id_to_name.get(emi_str, f"<desconocido id={emi_str}>")
            rec_nombre = id_to_name.get(rec_str, f"<desconocido id={rec_str}>")
            print(f"[DEBUG-TRADE] ERK: {emi_nombre} -> {rec_nombre}")

            if rec_str in id_to_port:
                log.info(f"Intercambio: {emi_nombre} -> {rec_nombre}")
                if rec_nombre in orden_personajes:
                    slot = orden_personajes.index(rec_nombre) + 1
                    print(f"[DEBUG-TRADE] receptor '{rec_nombre}' en slot {slot}")
                    presionar_atajo_slot(slot)
                else:
                    print(f"[DEBUG-TRADE] receptor '{rec_nombre}' NO en orden_personajes")
            else:
                print(f"[DEBUG-TRADE] receptor '{rec_nombre}' NO en id_to_port")

    if feature_autogroup:
        for emi_raw, rec_raw in re.findall(rb"PIK([^|]+)\|([^|\x00]+)", data):
            emi_n = emi_raw.decode(errors="ignore")
            rec_n = rec_raw.decode(errors="ignore")
            print(f"[DEBUG-GROUP] Invitación: {emi_n} -> {rec_n}")
            log.info(f"Invitación de grupo para {rec_n}")

            if rec_n in orden_personajes:
                slot = orden_personajes.index(rec_n) + 1
                print(f"[DEBUG-GROUP] receptor '{rec_n}' en slot {slot}")
                presionar_atajo_slot(slot)
            else:
                print(f"[DEBUG-GROUP] receptor '{rec_n}' NO en orden_personajes")


# ══════════════════════════════════════════════════════════════════════════════
#  Monitor hilo
# ══════════════════════════════════════════════════════════════════════════════
def start_monitor():
    try:
        log.info("Monitor iniciado")
        sniff(filter=BPF_FILTER, prn=process_message, store=False,
              stop_filter=lambda _: stop_monitor.is_set())
    except Exception:
        log.error(traceback.format_exc())

def monitor_desconexiones():
    while not stop_monitor.is_set():
        try:
            puertos_activos = set()
            for conn in psutil.net_connections(kind="tcp"):
                if conn.status in ("ESTABLISHED", "SYN_SENT") and conn.laddr:
                    puertos_activos.add(conn.laddr.port)

            eliminados = []
            for char_id, info in list(all_detected.items()):
                if info["port"] not in puertos_activos:
                    eliminados.append((char_id, info["name"]))

            for char_id, nombre in eliminados:
                log.info(f"[DESCONEXIÓN] {nombre} ya no tiene puerto activo → eliminado")
                print(f"[DEBUG] Personaje desconectado: {nombre}")
                all_detected.pop(char_id, None)
                id_to_port.pop(char_id, None)
                id_to_name.pop(char_id, None)
                if nombre in orden_personajes:
                    orden_personajes.remove(nombre)

            if eliminados and app:
                app.after(0, app.update_characters)

        except Exception as e:
            log.error(f"[monitor_desconexiones] {e}")

        stop_monitor.wait(5)


# ══════════════════════════════════════════════════════════════════════════════
#  Entry point
# ══════════════════════════════════════════════════════════════════════════════
def main():
    global app
    app = DofusToolsApp()
    threading.Thread(target=start_monitor,        daemon=True).start()
    threading.Thread(target=monitor_desconexiones, daemon=True).start()
    app.mainloop()
    stop_monitor.set()


if __name__ == "__main__":
    main()