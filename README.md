# Dofus Tools

Herramienta de automatización y gestión de ventanas para **Dofus Retro** multijugador en un mismo equipo.

---

## ¿Qué hace?

Detecta eventos del juego y automatiza tres acciones:

| Feature | Descripción |
|---|---|
| **Auto-focus** | Al llegar tu turno en combate, enfoca automáticamente la ventana correcta con `Ctrl+Alt+1..0` |
| **Auto-group** | Al recibir una invitación de grupo, cambia el foco al personaje invitado |
| **Auto-trade** | Al recibir una solicitud de intercambio, cambia a la ventana del personaje receptor |

Cada feature puede activarse/desactivarse desde la interfaz con un toggle.

---

## Requisitos

- Python 3.8+
- Windows

```
pip install scapy psutil pywin32
```

---

## Uso

```bash
python dofus_monitor.py
```

> ⚠ Ejecutar como **Administrador**.

Si ya tenías personajes conectados al iniciar, **reconéctalos** para que la herramienta los detecte.

---

## Gestión de Layouts (Wintabber Dofus)

Los layouts definen el orden de los personajes en los slots `Ctrl+Alt+1..0`.  
Se comparte el archivo con **Wintabber Dofus** en `%APPDATA%\DofusMiniTabber\window_positions.json`.

Desde el **Gestor de Layouts** puedes:
- Cargar y aplicar layouts guardados
- Guardar el orden actual como nuevo layout
- Importar archivos JSON de Wintabber
- Eliminar layouts existentes

También puedes reordenar los slots arrastrando las filas en la sección *Sesiones Activas*.

---

## Logs

Los eventos se registran en `dofus_monitor.log` en el mismo directorio.