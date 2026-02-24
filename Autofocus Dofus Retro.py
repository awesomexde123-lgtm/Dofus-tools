from scapy.all import sniff, TCP, IP, Raw
import win32gui
import win32con
import win32process
import win32api
import psutil
import logging
import traceback
import re
import threading
import ctypes

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("packet_sniffer.log", encoding="utf-8")
    ]
)
log = logging.getLogger(__name__)

## IPS Fallaster = 34.255.49.243 || Allisteria = 34.253.140.241

REMOTE_IP      = "34.253.140.241"
REMOTE_PORT    = 443
WINDOW_KEYWORD = "Dofus Retro"

stats = {"total": 0, "matched_ip": 0, "matched_port": 0, "raw": 0, "hits": 0}

mode                 = None
capture_total        = 0
captured_ids         = []
captured_ids_memoria = []
watch_ids            = []
id_to_port           = {}
id_to_name           = {}
all_detected         = {}
ids_en_lista         = []
ids_ya_avisados      = set()

sniffer_thread = None
stop_sniffer   = threading.Event()
capture_done   = threading.Event()


def get_dofus_windows() -> dict:
    windows = {}
    def enum_handler(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd)
            if WINDOW_KEYWORD in title:
                windows[hwnd] = title
    win32gui.EnumWindows(enum_handler, None)
    return windows


def get_pid_by_port(local_port: int) -> int | None:
    try:
        for conn in psutil.net_connections(kind="tcp"):
            if conn.laddr.port == local_port and conn.pid:
                return conn.pid
    except psutil.AccessDenied:
        log.error("Permiso denegado. Ejecutá como administrador.")
    return None


def get_hwnd_by_pid_tree(pid: int) -> int | None:
    try:
        proc   = psutil.Process(pid)
        parent = proc.parent()
        if not parent:
            return None

        result = None
        def enum_handler(hwnd, _):
            nonlocal result
            if result or not win32gui.IsWindowVisible(hwnd):
                return
            _, found_pid = win32process.GetWindowThreadProcessId(hwnd)
            if found_pid == parent.pid and WINDOW_KEYWORD in win32gui.GetWindowText(hwnd):
                result = hwnd
        win32gui.EnumWindows(enum_handler, None)
        return result

    except (psutil.NoSuchProcess, psutil.AccessDenied):
        log.error("No se pudo acceder al PID %d", pid)
        return None


def _set_foreground(hwnd: int) -> bool:
    try:
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)

        current_thread = ctypes.windll.kernel32.GetCurrentThreadId()
        target_thread, _ = win32process.GetWindowThreadProcessId(hwnd)

        attached = False
        if current_thread != target_thread:
            try:
                win32process.AttachThreadInput(current_thread, target_thread, True)
                attached = True
            except Exception:
                pass

        try:
            win32gui.BringWindowToTop(hwnd)
            win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
            win32gui.SetForegroundWindow(hwnd)
        except Exception:
            win32api.keybd_event(0, 0, 0, 0)
            win32gui.SetForegroundWindow(hwnd)
        finally:
            if attached:
                try:
                    win32process.AttachThreadInput(current_thread, target_thread, False)
                except Exception:
                    pass

        title = win32gui.GetWindowText(hwnd)
        log.info("✔ Ventana activada: '%s' (hwnd=%s)", title, hwnd)
        return True

    except Exception:
        log.error("Error al activar hwnd=%s:\n%s", hwnd, traceback.format_exc())
        return False


def activar_ventana_por_puerto(local_port: int) -> bool:
    pid = get_pid_by_port(local_port)
    if not pid:
        log.warning("No se encontró PID para el puerto %d.", local_port)
        windows = get_dofus_windows()
        if len(windows) == 1:
            return _set_foreground(list(windows.keys())[0])
        return False
    hwnd = get_hwnd_by_pid_tree(pid)
    if not hwnd:
        log.warning("No se encontró ventana para PID %d.", pid)
        return False
    return _set_foreground(hwnd)


def aprender_mapeo(data: bytes, local_port: int) -> None:
    as_matches = re.findall(rb"AS(\d{5,})", data)
    for char_id in as_matches:
        _registrar_mapeo(char_id.decode(), local_port, nombre=None, fuente="AS")


def _registrar_mapeo(char_id_str: str, local_port: int,
                     nombre: str | None, fuente: str) -> None:
    changed = False

    if char_id_str not in all_detected:
        all_detected[char_id_str] = {"port": local_port, "name": nombre or "?"}
        changed = True
    else:
        if nombre and all_detected[char_id_str]["name"] == "?":
            all_detected[char_id_str]["name"] = nombre
            changed = True
        if all_detected[char_id_str]["port"] != local_port:
            all_detected[char_id_str]["port"] = local_port
            changed = True

    if char_id_str in watch_ids:
        puerto_anterior = id_to_port.get(char_id_str)
        if puerto_anterior != local_port or (nombre and char_id_str not in id_to_name):
            id_to_port[char_id_str] = local_port
            if nombre:
                id_to_name[char_id_str] = nombre
            display_name = id_to_name.get(char_id_str, char_id_str)
            log.info("✔ Mapeado via %s | %s (ID=%s) → puerto=%d",
                     fuente, display_name, char_id_str, local_port)
            print(f"\n  ✔ [{fuente}] {display_name} (ID={char_id_str}) → puerto {local_port}")
            _print_mapeos()
    elif changed and nombre:
        log.debug("Detectado (no en watch) via %s | %s (ID=%s) → puerto=%d",
                  fuente, nombre, char_id_str, local_port)


def _print_mapeos() -> None:
    mapeados   = [cid for cid in watch_ids if cid in id_to_port]
    sin_mapear = [cid for cid in watch_ids if cid not in id_to_port]
    print(f"  📡 Mapeos: {len(mapeados)}/{len(watch_ids)}", end="")
    if sin_mapear:
        print(f" | Esperando: {', '.join(sin_mapear)}", end="")
    print()


def handle_capture_mode(data: bytes) -> None:
    global captured_ids, capture_total

    matches = re.findall(rb"ERK\d+\|(\d+)\|", data)
    for char_id in matches:
        char_id_str = char_id.decode()
        if char_id_str in ids_en_lista:
            if char_id_str not in ids_ya_avisados:
                print(f"\n  ⚠ ID {char_id_str} ya existe en la lista, ignorando.")
                print(f"  → Escribe 'g' + Enter para guardar | 'q' + Enter para salir")
                ids_ya_avisados.add(char_id_str)
            continue
        if char_id_str not in captured_ids:
            captured_ids.append(char_id_str)
            print(f"\n  ✔ ID capturado [{len(captured_ids)}/{capture_total}]: {char_id_str}")
            print(f"  → Escribe 'g' + Enter para guardar | 'q' + Enter para salir")
            log.info("ID capturado [%d/%d]: %s", len(captured_ids), capture_total, char_id_str)

            if len(captured_ids) >= capture_total:
                print("\n" + "─"*50)
                print(f"  ✔ Captura completa! {capture_total} personajes:")
                for i, cid in enumerate(captured_ids, 1):
                    print(f"    [{i}] {cid}")
                print("─"*50)
                log.info("Captura completa: %s", captured_ids)
                captured_ids_memoria.clear()
                captured_ids_memoria.extend(captured_ids)
                capture_done.set()


def handle_watch_mode(data: bytes, local_port: int) -> None:
    aprender_mapeo(data, local_port)

    matches = re.findall(rb"GTS(\d+)\|", data)
    for char_id in matches:
        char_id_str = char_id.decode()
        if char_id_str not in watch_ids:
            continue

        expected_port = id_to_port.get(char_id_str)
        if not expected_port:
            log.warning("GTS para ID=%s sin mapeo aún, ignorando.", char_id_str)
            continue

        if local_port != expected_port:
            continue

        nombre = id_to_name.get(char_id_str, char_id_str)
        stats["hits"] += 1
        log.info("★ Turno de %s (ID=%s) | puerto=%d", nombre, char_id_str, local_port)
        print(f"\n  ★ Turno de {nombre}!")
        activar_ventana_por_puerto(local_port)


def handle_auto_mode(data: bytes, local_port: int) -> None:
    aprender_mapeo(data, local_port)


def packet_callback(packet) -> None:
    if stop_sniffer.is_set():
        return

    stats["total"] += 1

    if not (packet.haslayer(IP) and packet.haslayer(TCP)):
        return

    ip_layer  = packet[IP]
    tcp_layer = packet[TCP]
    src_ip, dst_ip = ip_layer.src, ip_layer.dst
    sport, dport   = tcp_layer.sport, tcp_layer.dport

    if src_ip != REMOTE_IP and dst_ip != REMOTE_IP:
        return
    stats["matched_ip"] += 1

    if sport != REMOTE_PORT and dport != REMOTE_PORT:
        return
    stats["matched_port"] += 1

    if not packet.haslayer(Raw):
        return
    stats["raw"] += 1

    data = packet[Raw].load
    local_port = dport if src_ip == REMOTE_IP else sport

    if mode == "capture":
        handle_capture_mode(data)
    elif mode == "watch":
        handle_watch_mode(data, local_port)
    elif mode == "auto":
        handle_auto_mode(data, local_port)


def print_stats() -> None:
    log.info(
        "Stats → total=%d | ip=%d | port=%d | raw=%d | hits=%d",
        stats["total"], stats["matched_ip"],
        stats["matched_port"], stats["raw"], stats["hits"]
    )


def start_sniffer() -> None:
    try:
        sniff(
            filter=f"tcp and host {REMOTE_IP} and port {REMOTE_PORT}",
            prn=packet_callback,
            store=False,
            stop_filter=lambda _: stop_sniffer.is_set()
        )
    except Exception:
        log.critical("Error fatal en sniff():\n%s", traceback.format_exc())


def launch_sniffer() -> None:
    global sniffer_thread
    stop_sniffer.clear()
    capture_done.clear()
    sniffer_thread = threading.Thread(target=start_sniffer, daemon=True)
    sniffer_thread.start()


def kill_sniffer() -> None:
    stop_sniffer.set()
    if sniffer_thread:
        sniffer_thread.join(timeout=3)


def debug_arbol_completo() -> None:
    print("\n" + "─"*50)
    print("  DEBUG — Árbol de procesos Dofus Retro")
    print("─"*50)

    dofus_pids = []
    for proc in psutil.process_iter(['pid', 'name']):
        try:
            if 'dofus' in proc.info['name'].lower():
                dofus_pids.append(proc.info['pid'])
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    for pid in dofus_pids:
        try:
            proc   = psutil.Process(pid)
            parent = proc.parent()
            print(f"\n  PID={pid} | padre={parent.pid if parent else 'None'}")

            ventanas = []
            def enum(hwnd, _, cpid=pid):
                if win32gui.IsWindowVisible(hwnd):
                    _, fp = win32process.GetWindowThreadProcessId(hwnd)
                    if fp == cpid:
                        ventanas.append(f"hwnd={hwnd} '{win32gui.GetWindowText(hwnd)}'")
            win32gui.EnumWindows(enum, None)

            for v in ventanas:
                print(f"    VENTANA → {v}")
            if not ventanas:
                print("    sin ventana visible")

            conns = [c for c in psutil.net_connections('tcp') if c.pid == pid and c.raddr]
            for c in conns:
                print(f"    TCP → local={c.laddr.port} → {c.raddr.ip}:{c.raddr.port}")

        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    print("\n" + "─"*50)


def print_header() -> None:
    print("\n" + "═"*50)
    print("       DOFUS RETRO - PACKET SNIFFER")
    print("═"*50)


def menu_principal() -> None:
    while True:
        print_header()
        print("  [1] Modo CAPTURA       → capturar IDs de personajes")
        print("  [2] Modo WATCH lista   → cargar IDs desde ids_capturados.txt")
        print("  [0] Salir")
        print("═"*50)

        try:
            with open("ids_capturados.txt", "r", encoding="utf-8") as f:
                ids_archivo = [l.strip() for l in f if re.match(r"^\d+$", l.strip())]
            if ids_archivo:
                print(f"\n  IDs CARGADOS [{len(ids_archivo)}]: {', '.join(ids_archivo)}")
        except FileNotFoundError:
            pass

        if captured_ids_memoria:
            print(f"\n  💾 IDs en memoria ({len(captured_ids_memoria)}): "
                  f"{', '.join(captured_ids_memoria)}")

        if all_detected:
            print(f"\n  🔍 Personajes detectados ({len(all_detected)}):")
            for cid, info in all_detected.items():
                print(f"    {info['name']} (ID={cid}) puerto={info['port']}")

        choice = input("\n  Selecciona un modo [0/1/2]: ").strip().lower()

        if choice == "0":
            print("\n  Saliendo...\n")
            break
        elif choice == "1":
            menu_captura()
        elif choice == "2":
            menu_watch_desde_txt()
        else:
            print("  ✗ Opción inválida.")


def menu_watch_desde_txt(filepath: str = "ids_capturados.txt") -> None:
    print("\n" + "─"*50)
    print(f"  MODO WATCH — cargando desde '{filepath}'")
    print("─"*50)

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            ids = [line.strip() for line in f if re.match(r"^\d+$", line.strip())]
    except FileNotFoundError:
        print(f"\n  ✗ No se encontró '{filepath}'. Realiza una captura primero.")
        log.warning("Archivo %s no encontrado.", filepath)
        return
    except Exception:
        log.error("Error leyendo %s:\n%s", filepath, traceback.format_exc())
        return

    if not ids:
        print(f"\n  ✗ El archivo está vacío o no contiene IDs válidos.")
        return

    print(f"\n  ✔ {len(ids)} ID(s) cargados:")
    for i, cid in enumerate(ids, 1):
        print(f"    [{i}] {cid}")

    menu_watch(ids_precargados=ids)


def menu_captura() -> None:
    global mode, capture_total, captured_ids

    print("\n" + "─"*50)
    print("  MODO CAPTURA")
    print("  [0] ← Volver al menú principal")
    print("─"*50)

    while True:
        val = input("  ¿Cuántos personajes deseas capturar? [0 para volver]: ").strip()
        if val == "0":
            return
        try:
            capture_total = int(val)
            if capture_total > 0:
                break
            print("  ✗ Ingresa un número mayor a 0.")
        except ValueError:
            print("  ✗ Ingresa un número válido.")

    captured_ids = []
    ids_en_lista.clear()
    ids_ya_avisados.clear()
    try:
        with open("ids_capturados.txt", "r", encoding="utf-8") as f:
            ids_en_lista.extend(line.strip() for line in f if re.match(r"^\d+$", line.strip()))
        if ids_en_lista:
            print(f"  ℹ {len(ids_en_lista)} ID(s) ya en lista, serán ignorados.")
    except FileNotFoundError:
        pass

    mode = "capture"
    capture_done.clear()

    print("\n" + "─"*50)
    print(f"  Esperando {capture_total} personajes...")
    print("  → Envía intercambio a cada personaje para capturar su ID")
    print("  → Escribe 'g' + Enter para guardar lo capturado hasta ahora")
    print("  → Escribe 'q' + Enter para cancelar sin guardar")
    print("─"*50 + "\n")

    launch_sniffer()

    cancelled   = False
    finish_flag = threading.Event()

    def listen_input():
        nonlocal cancelled
        while not capture_done.is_set() and not finish_flag.is_set():
            try:
                cmd = input("").strip().lower()
                if cmd == "q":
                    cancelled = True
                    finish_flag.set()
                elif cmd == "g":
                    if captured_ids:
                        finish_flag.set()
                    else:
                        print("\n  ✗ Todavía no se capturó ningún ID.")
            except EOFError:
                return

    listener = threading.Thread(target=listen_input, daemon=True)
    listener.start()

    while not capture_done.is_set() and not finish_flag.is_set():
        capture_done.wait(timeout=0.2)

    kill_sniffer()
    finish_flag.set()

    if cancelled:
        print("\n  Captura cancelada.")
        return

    if not capture_done.is_set():
        print(f"\n  ✔ Captura finalizada manualmente con {len(captured_ids)} personaje(s):")
        for i, cid in enumerate(captured_ids, 1):
            print(f"    [{i}] {cid}")
        captured_ids_memoria.clear()
        captured_ids_memoria.extend(captured_ids)

    try:
        with open("ids_capturados.txt", "a", encoding="utf-8") as f:
            for cid in captured_ids:
                f.write(cid + "\n")
        print(f"\n  💾 IDs guardados en 'ids_capturados.txt'")
        log.info("IDs guardados en ids_capturados.txt: %s", captured_ids)
    except Exception:
        log.error("No se pudo guardar el archivo:\n%s", traceback.format_exc())


def menu_watch(ids_precargados: list = None) -> None:
    global mode, watch_ids
    id_to_port.clear()
    id_to_name.clear()

    print("\n" + "─"*50)
    print("  MODO WATCH")
    print("  [0] ← Volver al menú principal")
    print("─"*50)

    if ids_precargados:
        watch_ids = ids_precargados
        print(f"\n  IDs cargados desde captura ({len(watch_ids)}):")
        for i, cid in enumerate(watch_ids, 1):
            print(f"    [{i}] {cid}")

    elif captured_ids_memoria:
        print(f"\n  💾 Hay {len(captured_ids_memoria)} IDs en memoria:")
        for i, cid in enumerate(captured_ids_memoria, 1):
            print(f"    [{i}] {cid}")
        choice = input("\n  ¿Usar IDs de memoria? [s/n]: ").strip().lower()
        if choice == "s":
            watch_ids = captured_ids_memoria.copy()
        else:
            watch_ids = _pedir_ids_manual()
            if watch_ids is None:
                return
    else:
        watch_ids = _pedir_ids_manual()
        if watch_ids is None:
            return

    if not watch_ids:
        return

    for cid in watch_ids:
        if cid in all_detected:
            id_to_port[cid] = all_detected[cid]["port"]
            if all_detected[cid]["name"] != "?":
                id_to_name[cid] = all_detected[cid]["name"]
            log.info("Pre-cargado mapeo | ID=%s → puerto=%d", cid, id_to_port[cid])

    mode = "watch"

    print("\n" + "─"*50)
    print(f"  WATCH activo — vigilando {len(watch_ids)} personaje(s):")
    for i, cid in enumerate(watch_ids, 1):
        nombre = id_to_name.get(cid, "?")
        puerto = id_to_port.get(cid, "sin mapear")
        print(f"    [{i}] {nombre} (ID={cid}) → puerto {puerto}")
    print("  → Conecta a los personaje y el modo focus estara activado. (Cambiar personaje -> Entrear denuevo si ya estan conectados.)")
    print("  → Escribe 'stop' + Enter para volver al menú")
    print("─"*50 + "\n")

    launch_sniffer()

    stop_flag = threading.Event()

    def listen_stop():
        while not stop_flag.is_set():
            try:
                line = input("").strip().lower()
                if line == "stop":
                    stop_flag.set()
            except EOFError:
                return

    listener = threading.Thread(target=listen_stop, daemon=True)
    listener.start()

    while not stop_flag.is_set():
        stop_flag.wait(timeout=0.2)

    kill_sniffer()
    print("\n  Watch detenido.")
    if id_to_port:
        print("  Mapeos finales:")
        for cid, port in id_to_port.items():
            nombre = id_to_name.get(cid, cid)
            print(f"    {nombre} (ID={cid}) → puerto {port}")


def _pedir_ids_manual() -> list | None:
    ids = []
    print("\n  Ingresa los IDs a vigilar (línea vacía para terminar, 0 para volver):")
    while True:
        cid = input(f"  ID [{len(ids)+1}]: ").strip()
        if cid == "0":
            return None
        if not cid:
            if ids:
                return ids
            print("  ✗ Ingresa al menos un ID.")
        elif re.match(r"^\d+$", cid):
            ids.append(cid)
            print(f"  ✔ ID {cid} agregado.")
        else:
            print("  ✗ ID inválido, solo números.")


if __name__ == "__main__":
    try:
        menu_principal()
    except KeyboardInterrupt:
        print("\n\n  Interrupted.")
        kill_sniffer()
    finally:
        print_stats()