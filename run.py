"""Lanzador portable del proyecto para Windows y Linux.

Este archivo solo usa la biblioteca estandar de Python. Crea y mantiene el
entorno virtual adecuado, instala las dependencias y ejecuta ``src/main.py``.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import signal
import shutil
import stat
import subprocess
import sys
import venv
from datetime import datetime
from pathlib import Path
from uuid import uuid4


PROJECT_ROOT = Path(__file__).resolve().parent
ENVIRONMENTS_DIR = PROJECT_ROOT / "environments"
REQUIREMENTS_FILE = PROJECT_ROOT / "requirements.txt"
MAIN_FILE = PROJECT_ROOT / "src" / "main.py"
CONFIG_FILE = PROJECT_ROOT / "config.json"
LOGS_DIR = PROJECT_ROOT / "logs"
STATE_FILE_NAME = ".environment_state.json"
SUPPORTED_SYSTEMS = {"Windows": "windows", "Linux": "linux"}
SUPERVISOR_ARGUMENT = "--run-supervisor"
LOG_PATH_ENVIRONMENT = "APP_RUNTIME_LOG_PATH"
CLEAN_EXIT_ENVIRONMENT = "APP_CLEAN_EXIT_FILE"
RUN_TOKEN_ENVIRONMENT = "APP_RUN_TOKEN"
LOG_FILE_PREFIX = "application"


def append_supervisor_log(log_path: Path, level: str, message: str) -> None:
    """Añade y fuerza a disco una línea de diagnóstico del supervisor."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().astimezone()
    line = (
        f"{timestamp:%Y-%m-%d %H:%M:%S}.{timestamp.microsecond // 1000:03d} "
        f"[{level}] MainThread run.supervisor: {message}\n"
    )
    with log_path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(line)
        stream.flush()
        os.fsync(stream.fileno())


def describe_abnormal_exit(exit_code: int, system_name: str) -> str:
    """Describe una señal o código nativo sin inventar una causa desconocida."""
    if system_name == "Windows":
        unsigned_code = exit_code & 0xFFFFFFFF
        known_windows_codes = {
            0xC0000005: "violación de acceso a memoria",
            0xC000001D: "instrucción ilegal",
            0xC0000094: "división entera por cero",
            0xC00000FD: "desbordamiento de pila",
            0xC0000135: "dependencia DLL no encontrada",
            0xC0000139: "punto de entrada de DLL no encontrado",
            0xC0000142: "fallo al inicializar una DLL",
            0xC0000374: "corrupción de heap detectada",
            0xC0000409: "fallo de comprobación de seguridad o terminación rápida",
        }
        known_reason = known_windows_codes.get(unsigned_code)
        if known_reason is not None:
            return f"{known_reason}; código 0x{unsigned_code:08X}"
        return f"código de salida {exit_code} (0x{unsigned_code:08X})"
    if exit_code < 0:
        signal_number = -exit_code
        try:
            signal_name = signal.Signals(signal_number).name
        except ValueError:
            signal_name = "señal desconocida"
        return f"terminado por {signal_name} ({signal_number})"
    return f"código de salida {exit_code}"


def supervise_application(
    python_executable: Path,
    system_name: str,
    show_console: bool,
    application_arguments: list[str],
) -> int:
    """Espera al programa y registra el resultado aunque no exista consola."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().astimezone()
    token = uuid4().hex
    log_path = LOGS_DIR / (
        f"{LOG_FILE_PREFIX}_{timestamp:%Y-%m-%d_%H-%M-%S-%f}_"
        f"supervisor-{os.getpid()}.log"
    )
    marker_path = LOGS_DIR / f".clean-exit-{token}.json"
    environment = os.environ.copy()
    # Estas variables permiten que una aplicación futura comparta este log y
    # confirme su cierre; el supervisor no exige que las use.
    environment[LOG_PATH_ENVIRONMENT] = str(log_path)
    environment[CLEAN_EXIT_ENVIRONMENT] = str(marker_path)
    environment[RUN_TOKEN_ENVIRONMENT] = token
    command = [str(python_executable), str(MAIN_FILE), *application_arguments]
    append_supervisor_log(
        log_path,
        "INFO",
        f"Supervisor iniciado; ejecutable={python_executable} entrada={MAIN_FILE}",
    )

    try:
        if show_console:
            process = subprocess.Popen(
                command,
                cwd=PROJECT_ROOT,
                env=environment,
                close_fds=True,
            )
            exit_code = process.wait()
        else:
            # Sin consola, stdout y stderr se conservan en el mismo archivo para
            # no perder un traceback que explique el código de salida.
            with log_path.open("a", encoding="utf-8", newline="\n") as output:
                process = subprocess.Popen(
                    command,
                    cwd=PROJECT_ROOT,
                    env=environment,
                    stdin=subprocess.DEVNULL,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    close_fds=True,
                )
                exit_code = process.wait()
                output.flush()
                os.fsync(output.fileno())
    except OSError as error:
        append_supervisor_log(
            log_path,
            "CRITICAL",
            f"CIERRE INESPERADO: no se pudo iniciar o supervisar la aplicación; {error}",
        )
        return 1
    finally:
        marker_path.unlink(missing_ok=True)

    if exit_code != 0:
        reason = describe_abnormal_exit(exit_code, system_name)
        append_supervisor_log(
            log_path,
            "CRITICAL",
            f"CIERRE INESPERADO: {reason}; revise las líneas anteriores del log.",
        )
    else:
        append_supervisor_log(log_path, "INFO", "Cierre normal; código=0.")
    return exit_code


def launch_hidden_supervisor(
    python_executable: Path,
    system_name: str,
    application_arguments: list[str],
) -> int:
    """Desacopla un supervisor oculto que permanece hasta que termine Main."""
    supervisor_executable = python_executable
    options: dict[str, object] = {}
    if system_name == "Windows":
        pythonw_executable = python_executable.with_name("pythonw.exe")
        if pythonw_executable.is_file():
            supervisor_executable = pythonw_executable
        options["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    else:
        options["start_new_session"] = True
    command = [
        str(supervisor_executable),
        str(Path(__file__).resolve()),
        SUPERVISOR_ARGUMENT,
        *application_arguments,
    ]
    subprocess.Popen(
        command,
        cwd=PROJECT_ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        **options,
    )
    return 0


def detect_system() -> tuple[str, str]:
    """Devuelve el nombre del sistema y el nombre de su carpeta de entorno."""
    system_name = platform.system()
    environment_name = SUPPORTED_SYSTEMS.get(system_name)
    if environment_name is None:
        supported = ", ".join(SUPPORTED_SYSTEMS)
        raise RuntimeError(
            f"Sistema operativo no compatible: {system_name!r}. "
            f"Sistemas compatibles: {supported}."
        )
    return system_name, environment_name


def environment_python(environment_dir: Path, system_name: str) -> Path:
    """Obtiene la ruta del ejecutable Python dentro del entorno virtual."""
    if system_name == "Windows":
        return environment_dir / "Scripts" / "python.exe"
    return environment_dir / "bin" / "python"


def requirements_hash() -> str:
    """Calcula una huella para detectar cambios en las dependencias."""
    return hashlib.sha256(REQUIREMENTS_FILE.read_bytes()).hexdigest()


def expected_state(system_name: str) -> dict[str, object]:
    """Construye los datos que identifican al sistema y Python actuales."""
    return {
        "system": system_name,
        "machine": platform.machine(),
        "python_major": sys.version_info.major,
        "python_minor": sys.version_info.minor,
        "requirements_sha256": requirements_hash(),
    }


def read_state(environment_dir: Path) -> dict[str, object] | None:
    """Lee el estado guardado; devuelve None si falta o esta danado."""
    state_file = environment_dir / STATE_FILE_NAME
    try:
        data = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def write_state(environment_dir: Path, state: dict[str, object]) -> None:
    """Guarda el estado solo despues de validar entorno y dependencias."""
    state_file = environment_dir / STATE_FILE_NAME
    state_file.write_text(
        json.dumps(state, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def run_checked(command: list[str], description: str) -> None:
    """Ejecuta un comando y presenta un error claro si este falla."""
    print(f"[run.py] {description}...", flush=True)
    subprocess.run(command, cwd=PROJECT_ROOT, check=True)


def python_is_usable(python_executable: Path, environment_dir: Path) -> bool:
    """Comprueba que Python arranca y pertenece al entorno esperado."""
    if not python_executable.is_file():
        return False

    check_code = (
        "import pathlib, pip, sys; "
        "expected = pathlib.Path(sys.argv[1]).resolve(); "
        "actual = pathlib.Path(sys.prefix).resolve(); "
        "raise SystemExit(0 if actual == expected else 1)"
    )
    result = subprocess.run(
        [str(python_executable), "-c", check_code, str(environment_dir)],
        cwd=PROJECT_ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


def base_environment_matches(
    saved_state: dict[str, object] | None,
    current_state: dict[str, object],
) -> bool:
    """Valida plataforma, arquitectura y version mayor/menor de Python."""
    if saved_state is None:
        return False
    keys = ("system", "machine", "python_major", "python_minor")
    return all(saved_state.get(key) == current_state[key] for key in keys)


def remove_environment(environment_dir: Path) -> None:
    """Elimina exclusivamente el entorno incompatible de este sistema."""
    if environment_dir.exists():
        print(
            f"[run.py] Eliminando entorno incompatible: {environment_dir}",
            flush=True,
        )

        def force_remove(function: object, path: str, error: object) -> None:
            """Quita el atributo de solo lectura comun en carpetas de OneDrive."""
            del error
            os.chmod(path, stat.S_IRWXU)
            function(path)  # type: ignore[operator]

        shutil.rmtree(environment_dir, onerror=force_remove)


def create_environment(environment_dir: Path) -> None:
    """Crea un entorno virtual con pip usando el Python que lanzo run.py."""
    print(f"[run.py] Creando entorno virtual: {environment_dir}", flush=True)
    environment_dir.parent.mkdir(parents=True, exist_ok=True)
    venv.EnvBuilder(with_pip=True, clear=False).create(environment_dir)


def dependencies_are_usable(python_executable: Path) -> bool:
    """Valida conflictos e importaciones reales de los módulos usados por la UI."""
    result = subprocess.run(
        [str(python_executable), "-m", "pip", "check"],
        cwd=PROJECT_ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if result.returncode != 0:
        return False
    # pip check puede aprobar un entorno vacío o con DLL de Qt ausentes.
    imports = subprocess.run(
        [
            str(python_executable),
            "-c",
            "from PySide6 import QtWidgets, QtSerialPort, QtSvg",
        ],
        cwd=PROJECT_ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return imports.returncode == 0


def prepare_environment(system_name: str, environment_dir: Path) -> Path:
    """Crea, repara o reutiliza el entorno virtual de la plataforma actual."""
    current_state = expected_state(system_name)
    saved_state = read_state(environment_dir)
    python_executable = environment_python(environment_dir, system_name)

    environment_valid = (
        base_environment_matches(saved_state, current_state)
        and python_is_usable(python_executable, environment_dir)
    )
    if not environment_valid:
        remove_environment(environment_dir)
        create_environment(environment_dir)
        python_executable = environment_python(environment_dir, system_name)
        saved_state = None

    requirements_changed = (
        saved_state is None
        or saved_state.get("requirements_sha256")
        != current_state["requirements_sha256"]
    )
    if requirements_changed or not dependencies_are_usable(python_executable):
        run_checked(
            [
                str(python_executable),
                "-m",
                "pip",
                "install",
                "--requirement",
                str(REQUIREMENTS_FILE),
            ],
            "Instalando dependencias de requirements.txt",
        )

    if not python_is_usable(python_executable, environment_dir):
        raise RuntimeError("El entorno virtual creado no funciona correctamente.")
    if not dependencies_are_usable(python_executable):
        raise RuntimeError("Las dependencias instaladas presentan conflictos.")

    write_state(environment_dir, current_state)
    return python_executable


def validate_project_files() -> None:
    """Comprueba los archivos necesarios antes de modificar el equipo."""
    missing = [path for path in (REQUIREMENTS_FILE, MAIN_FILE) if not path.is_file()]
    if missing:
        formatted = "\n".join(f"  - {path}" for path in missing)
        raise FileNotFoundError(f"Faltan archivos obligatorios:\n{formatted}")


def load_show_console() -> bool:
    """Lee show_console y usa True como valor seguro ante datos inválidos."""
    try:
        configuration = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        print(
            f"[run.py] ADVERTENCIA: no se pudo leer {CONFIG_FILE.name}: {error}. "
            "Se mostrará la consola.",
            file=sys.stderr,
        )
        return True
    if not isinstance(configuration, dict):
        print(
            "[run.py] ADVERTENCIA: config.json debe contener un objeto JSON. "
            "Se mostrará la consola.",
            file=sys.stderr,
        )
        return True
    show_console = configuration.get("show_console", True)
    if not isinstance(show_console, bool):
        print(
            "[run.py] ADVERTENCIA: show_console debe ser true o false. "
            "Se mostrará la consola.",
            file=sys.stderr,
        )
        return True
    return show_console


def launch_application(
    python_executable: Path,
    system_name: str,
    show_console: bool,
) -> int:
    """Ejecuta Main bajo supervisión visible u oculta, según config.json."""
    if show_console:
        return supervise_application(
            python_executable,
            system_name,
            True,
            sys.argv[1:],
        )
    return launch_hidden_supervisor(
        python_executable,
        system_name,
        sys.argv[1:],
    )


def main() -> int:
    """Prepara el entorno correcto y entrega la ejecucion a src/main.py."""
    try:
        if len(sys.argv) >= 2 and sys.argv[1] == SUPERVISOR_ARGUMENT:
            system_name, _environment_name = detect_system()
            return supervise_application(
                Path(sys.executable),
                system_name,
                False,
                sys.argv[2:],
            )
        validate_project_files()
        system_name, environment_name = detect_system()
        show_console = load_show_console()
        environment_dir = ENVIRONMENTS_DIR / environment_name
        python_executable = prepare_environment(system_name, environment_dir)
        print(
            f"[run.py] Ejecutando {MAIN_FILE.relative_to(PROJECT_ROOT)}...",
            flush=True,
        )
        return launch_application(python_executable, system_name, show_console)
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"[run.py] ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
