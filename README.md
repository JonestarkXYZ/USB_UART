# USB UART

Terminal serie para Windows con **PySide6 6.12.0** y **QtSerialPort**. Permite enviar y recibir bytes mediante adaptadores USB-UART y puertos serie COM. No enumera dispositivos USB que no exponen un puerto serie.

## Inicio en Windows

Desde PowerShell, dentro de este proyecto:

```powershell
py run.py
```

El lanzador existente prepara `environments/windows`, instala `requirements.txt` y abre la aplicación. Requiere Python compatible con PySide6; se verificó con Python 3.14.7 de 64 bits. `show_console` controla la consola y los registros del supervisor quedan en `logs`.

Si el entorno ya está preparado, también se puede iniciar directamente:

```powershell
.\environments\windows\Scripts\python.exe src\main.py
```

## Uso

1. Seleccione el puerto COM y configure baudios, bits de datos, paridad, bits de parada y control de flujo. Valores iniciales: **115200, 8 bits, sin paridad, 1 bit de parada, sin control de flujo**. Pulse **Conectar**. Los parámetros permanecen bloqueados mientras el puerto está abierto.
2. Seleccione **Recibir como** y las marcas de tiempo: ninguna, RX, TX o ambos. El historial muestra RX y TX con colores diferentes, hora local con milisegundos y desplazamiento vertical. Cambiar el formato vuelve a representar los bytes conservados.
3. Escriba abajo y pulse **Enter** o **Enviar**. El selector junto a Enviar determina la codificación de entrada y la visualización TX. Cada formato conserva su borrador durante la sesión. Los borradores no se guardan en disco.

| Formato | Entrada | Bytes transmitidos |
|---|---|---|
| UTF-8 | `Hola ñ` | `48 6F 6C 61 20 C3 B1` |
| ASCII | `Hi` | `48 69` |
| Binario | `01001000 01101001` | `48 69` |
| Hexadecimal | `48 69 FF` | `48 69 FF` |

Binario solo permite `0` y `1`, agrupados automáticamente de ocho en ocho. Hexadecimal solo permite `0–9` y `A–F`, en pares y siempre en mayúsculas. Se puede pegar una secuencia compacta; los caracteres inválidos se rechazan y no se envían bytes incompletos. No se agregan ceros para completar grupos. La entrada binaria/hexadecimal admite hasta 8192 dígitos, y la entrada de texto hasta 32768 caracteres. ASCII rechaza tildes y `ñ`; para esos caracteres use UTF-8.

**Final de envío** añade opcionalmente LF (`0A`), CR (`0D`) o CRLF (`0D 0A`) en cualquiera de los formatos. De forma predeterminada no se añade nada. Los controles recibidos o enviados se muestran como `\r`, `\n`, `\t` y `\x00`; nunca se ejecutan secuencias de terminal ni se interpreta HTML. El decodificador conserva caracteres UTF-8 repartidos entre lecturas; una fila puede indicar una secuencia pendiente hasta recibir los bytes restantes.

El historial representa bloques de lectura/escritura, **no mensajes definidos por un protocolo ni un registro por byte**. UART no proporciona límites de mensaje. Un registro TX indica que el controlador confirmó la escritura de esos bytes; no implica que el equipo remoto los haya procesado. Los errores y bytes pendientes al desconectar se informan en la barra inferior. No hay reconexión automática.

## Menús y archivos

- **Archivo:** guardar el historial visible como texto UTF-8 (`Ctrl+S`), copiar selección del historial (`Ctrl+Shift+C`), seleccionar todo el historial (`Ctrl+Shift+A`) y salir (`Ctrl+Q`). `Ctrl+C` sigue funcionando normalmente en el cuadro de envío y en el historial.
- **Configuración:** actualizar puertos (`F5`), cambiar el límite del historial, mostrar consola al iniciar y guardar preferencias.
- **Vista:** fuente, estilo y tamaño (`Ctrl+Shift+F`), aumentar/reducir tamaño, ajuste de línea, desplazamiento automático y temas claro/oscuro. Estas opciones afectan al texto mostrado. La papelera y `Ctrl+L` eliminan el historial y reinician contadores.

El desplazamiento automático sigue las nuevas filas mientras esté al final; al subir para consultar el historial se pausa hasta volver al final. Por defecto se conservan 1000 registros, de hasta 4096 bytes cada uno; al alcanzar el límite se descartan los más antiguos. El límite configurable es de 100 a 10000 registros. Los contadores abarcan la sesión desde el último borrado, incluso si ya se descartaron registros antiguos. Cambiar a binario aumenta el uso de memoria por su representación de ocho dígitos por byte.

Todas las preferencias se guardan en **`config.json`**, incluidas las del lanzador, puerto, UART, formatos, finales de línea, marcas de tiempo, fuente, tema y tamaño de ventana. La escritura es atómica y conserva claves adicionales. Un archivo JSON ilegible se mantiene intacto y se informa del error; hay que corregirlo antes de poder guardar nuevas preferencias. No se guarda el historial automáticamente.

Las paletas están en **`themes/Oscuro.json`** y **`themes/Claro.json`**. Se pueden agregar paletas JSON con las mismas claves y un `name` distinto; se detectan al iniciar. Los iconos son SVG locales en **`icons`**, incluida la flecha de los selectores. No se necesitan imágenes descargadas.

## Estructura

| Ruta | Función |
|---|---|
| `run.py` | Lanzador portable y supervisor existentes; comprueba también las importaciones de Qt. |
| `src/main.py` | Inicio de QApplication. |
| `src/window.py` | Ventana, validadores, menús e historial. |
| `src/transport.py` | Comunicación asíncrona, cola TX acotada y errores UART. |
| `src/protocol.py` | Conversiones de bytes y decodificación UTF-8 incremental. |
| `src/settings.py` | Validación y persistencia de configuración. |
| `tests/test_terminal.py` | Pruebas sin hardware. |
| `scripts/preview.py` | Capturas de ambos temas con datos explícitamente simulados. |

## Comprobación

```powershell
.\environments\windows\Scripts\python.exe -m unittest discover -s tests -v
.\environments\windows\Scripts\python.exe -m pip check
.\environments\windows\Scripts\python.exe scripts\preview.py
```

Las capturas se generan en `docs/preview_oscuro.png` y `docs/preview_claro.png`; el script usa configuración temporal y no abre puertos. Las pruebas verifican conversiones, entradas inválidas, escritura confirmada parcialmente mediante un controlador simulado, desconexión, teclado, historial y configuración. **No sustituyen una prueba UART física.**

Para una prueba física, conecte un adaptador USB-UART compatible, seleccione su COM y sus parámetros y compruebe una respuesta del dispositivo. Como prueba de bucle local, en un adaptador UART independiente puede unir TX y RX y enviar `48 69` en hexadecimal: debe aparecer TX y después RX con los mismos bytes. No se ejecutó esta prueba en el entorno de desarrollo porque no había puertos COM disponibles.

La interfaz usa las señales `readyRead` y `bytesWritten` de [QtSerialPort](https://doc.qt.io/qtforpython-6/PySide6/QtSerialPort/QSerialPort.html), sin esperas bloqueantes en la UI.
