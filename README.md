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

Con el foco en el cuadro de envío, **↑** recupera el último texto enviado y sigue hacia los anteriores; **↓** avanza hacia los más recientes. Al bajar después del último se recupera el borrador que estaba escrito antes de recorrer el historial. Puede editar una entrada recuperada y enviarla otra vez con Enter o Enviar. Se conservan los últimos **100 envíos aceptados por formato durante la sesión**, sin incluir intentos fallidos ni finales de línea añadidos. Recorrerlos no envía nada. La papelera del historial RX/TX no borra estas entradas.

| Formato | Entrada | Bytes transmitidos |
|---|---|---|
| UTF-8 | `Hola ñ` | `48 6F 6C 61 20 C3 B1` |
| ASCII | `Hi` | `48 69` |
| Binario | `01001000 01101001` | `48 69` |
| Hexadecimal | `48 69 FF` | `48 69 FF` |

Binario solo permite `0` y `1`, agrupados automáticamente de ocho en ocho. Hexadecimal solo permite `0–9` y `A–F`, en pares y siempre en mayúsculas. Se puede pegar una secuencia compacta; los caracteres inválidos se rechazan y no se envían bytes incompletos. No se agregan ceros para completar grupos. La entrada binaria/hexadecimal admite hasta 8192 dígitos, y la entrada de texto hasta 32768 caracteres. ASCII rechaza tildes y `ñ`; para esos caracteres use UTF-8.

**Final de envío** añade opcionalmente LF (`0A`), CR (`0D`) o CRLF (`0D 0A`) en cualquiera de los formatos. Son bytes reales transmitidos por UART, aunque no sean caracteres imprimibles. De forma predeterminada no se añade nada. En RX, esos bytes proceden del dispositivo remoto; el visor no los agrega.

El botón superior **Interpretar controles** cambia la visualización de texto UTF-8/ASCII:

- **Desactivado:** muestra los controles como `\r`, `\n`, `\t` y `\x00`, manteniendo separados los bloques de lectura.
- **Activado:** LF inicia una nueva línea; CR devuelve el cursor al inicio de los datos, sin sobrescribir RX/TX ni la hora; CRLF produce un solo salto; TAB avanza al siguiente múltiplo de ocho columnas y BS retrocede una posición. CR y BS permiten sobrescribir texto, como en un indicador de progreso. Las lecturas RX consecutivas que continúan una misma línea se unen, incluso si CR y LF llegan por separado. Cada línea lógica nueva conserva su etiqueta RX/TX. Al cambiar de dirección se abre otra fila para mantener el orden del intercambio.

La opción se guarda como `terminal.interpret_controls` en `config.json` y actualiza también el historial existente a partir de los bytes originales. No cambia lo transmitido ni convierte un texto literal `\r\n` en controles. Hexadecimal y binario siempre muestran todos los bytes. Los controles restantes siguen visibles y no se interpreta ANSI ni HTML. El decodificador conserva caracteres UTF-8 repartidos entre lecturas; con interpretación activada espera a completar el carácter antes de mostrarlo.

Las líneas largas se ajustan con **sangría desde el inicio de los datos**: sus continuaciones no invaden la columna RX/TX/hora. La sangría se recalcula al cambiar la fuente, el tamaño o las marcas de tiempo. Se utiliza el formato de párrafo de [QTextBlockFormat](https://doc.qt.io/qtforpython-6/PySide6/QtGui/QTextBlockFormat.html).

El historial conserva bloques de lectura/escritura, **no mensajes definidos por un protocolo ni un registro por byte**. UART no proporciona límites de mensaje. La vista interpretada usa LF para separar líneas. Un registro TX indica que el controlador confirmó la escritura de esos bytes; no implica que el equipo remoto los haya procesado. Los errores y bytes pendientes al desconectar se informan en la barra inferior. No hay reconexión automática.

## Menús y archivos

- **Archivo:** guardar el historial visible como texto UTF-8 (`Ctrl+S`), copiar selección del historial (`Ctrl+Shift+C`), seleccionar todo el historial (`Ctrl+Shift+A`) y salir (`Ctrl+Q`). `Ctrl+C` sigue funcionando normalmente en el cuadro de envío y en el historial.
- **Configuración:** actualizar puertos (`F5`), cambiar el límite del historial, mostrar consola al iniciar y guardar preferencias.
- **Vista:** fuente, estilo y tamaño (`Ctrl+Shift+F`), aumentar/reducir tamaño, ajuste de línea, desplazamiento automático y temas claro/oscuro. Estas opciones afectan al texto mostrado. La papelera y `Ctrl+L` eliminan el historial y reinician contadores.

El desplazamiento automático sigue las nuevas filas mientras esté al final; al subir para consultar el historial se pausa hasta volver al final. Por defecto se conservan 1000 registros, de hasta 4096 bytes cada uno; al alcanzar el límite se descartan los más antiguos. El límite configurable es de 100 a 10000 registros y también limita los párrafos visibles, ya que un bloque puede contener muchos LF. Para acotar el coste del visor, una línea interpretada sin terminadores se divide en otra fila si supera 32768 caracteres. Los contadores abarcan la sesión desde el último borrado, incluso si ya se descartaron registros antiguos. Cambiar a binario aumenta el uso de memoria por su representación de ocho dígitos por byte.

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
