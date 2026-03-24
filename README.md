🎯 Autofocus – Dofus Retro

Autofocus es una herramienta que detecta automáticamente cuándo es tu turno en Dofus Retro y cambia el foco a la ventana correcta del personaje.

Está pensada especialmente para jugadores en multicuenta, donde cambiar manualmente entre ventanas puede ser lento o incómodo.

⚠️ Aviso importante

Este proyecto fue creado con fines educativos.

No modifica el juego ni interactúa directamente con él. Solo analiza tráfico de red local para detectar eventos.

Aun así, el uso de herramientas externas puede ir en contra de los términos de servicio de Ankama.
Úsalo bajo tu propia responsabilidad.

-  Uso rápido (versión ejecutable)

Si solo quieres usar la herramienta sin instalar nada complicado:

Ve a la sección de Releases
Descarga DofusTools.zip
Extrae los archivos
Instala Npcap (ver abajo)
Ejecuta DofusTools.exe como administrador


- Requisitos

Para que el programa funcione correctamente necesitas instalar:

Npcap
Durante la instalación asegúrate de marcar la opción:

Install Npcap in WinPcap API-compatible Mode

Esto es necesario para poder capturar paquetes de red.

-  Uso desde código fuente

Si prefieres ejecutarlo manualmente:

Requisitos
Python 3.10 o superior
Instalación
pip install scapy pywin32 psutil
Ejecución
python main.py


🧠 ¿Cómo funciona?

La herramienta escucha paquetes de red relacionados con el juego y detecta cuándo ocurre un evento específico (como el inicio de turno).

En base a esa información, identifica qué ventana corresponde al personaje activo y la pone en primer plano automáticamente.

📌 Notas

Compatible con Dofus Retro (probado en servidores como Fallaster y Allisteria)
No modifica archivos del juego
No inyecta código ni automatiza acciones dentro del cliente
