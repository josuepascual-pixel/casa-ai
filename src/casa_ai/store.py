"""Persistencia: auditoria de acciones, acciones pendientes de confirmacion e historial.

Todo en SQLite. Tres razones para tener esto y no fiarse solo de logs:

1. Auditoria: queda registro de cada accion fisica que un agente ejecuta en la
   casa, con quien la pidio y el resultado. Imprescindible cuando un agente
   puede cortar el wifi o descargar la bateria.
2. Confirmaciones: una accion de riesgo alto se guarda como "pendiente" y solo
   se ejecuta cuando el usuario confirma en un mensaje POSTERIOR, que puede
   llegar minutos despues. Eso no cabe en memoria de proceso. Y "posterior" se
   comprueba aqui, contando turnos humanos: si dependiera de que el modelo
   respete la instruccion del prompt, no seria una garantia.
3. Historial de conversacion por chat, para que el agente tenga contexto.
"""

from __future__ import annotations

import json
import secrets
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

_ESQUEMA = """
CREATE TABLE IF NOT EXISTS auditoria (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    canal TEXT NOT NULL,
    usuario TEXT NOT NULL,
    herramienta TEXT NOT NULL,
    argumentos TEXT NOT NULL,
    riesgo TEXT NOT NULL,
    resultado TEXT NOT NULL,
    error INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_auditoria_ts ON auditoria(ts DESC);

CREATE TABLE IF NOT EXISTS pendientes (
    token TEXT PRIMARY KEY,
    ts REAL NOT NULL,
    canal TEXT NOT NULL,
    usuario TEXT NOT NULL,
    conversacion TEXT NOT NULL DEFAULT '',
    turno INTEGER NOT NULL DEFAULT 0,
    herramienta TEXT NOT NULL,
    argumentos TEXT NOT NULL,
    resumen TEXT NOT NULL,
    estado TEXT NOT NULL DEFAULT 'pendiente'
);

-- Cuenta de turnos HUMANOS por conversacion. No sirve contar mensajes de la
-- tabla `mensajes`: los tool_result tambien se guardan con rol 'user', asi
-- que el modelo podria "avanzar de turno" el solo.
CREATE TABLE IF NOT EXISTS turnos (
    conversacion TEXT PRIMARY KEY,
    contador INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS mensajes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    conversacion TEXT NOT NULL,
    rol TEXT NOT NULL,
    contenido TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mensajes_conv ON mensajes(conversacion, id);

CREATE TABLE IF NOT EXISTS ajustes (
    clave TEXT PRIMARY KEY,
    valor TEXT NOT NULL
);

-- Ordenes para mas tarde pedidas por chat («sube la persiana a las 8»). Se
-- ejecutan como un turno del agente con la identidad de quien las pidio, y
-- el resultado vuelve por su mismo chat. `siguiente` es el proximo disparo
-- (epoch); una repetida se reprograma, una suelta se desactiva.
CREATE TABLE IF NOT EXISTS programaciones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    creado REAL NOT NULL,
    canal TEXT NOT NULL,
    usuario TEXT NOT NULL,
    conversacion TEXT NOT NULL,
    orden TEXT NOT NULL,
    siguiente REAL NOT NULL,
    hora TEXT,
    dias TEXT,
    activa INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_programaciones_siguiente ON programaciones(activa, siguiente);
"""

# Una accion pendiente caduca: confirmar a ciegas algo pedido hace dos horas
# es una via de accidentes.
CADUCIDAD_PENDIENTE_S = 15 * 60


class Store:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(_ESQUEMA)
            self._migrar(c)

    @staticmethod
    def _migrar(c: sqlite3.Connection) -> None:
        """Anade columnas que falten en bases de datos ya existentes."""
        columnas = {f[1] for f in c.execute("PRAGMA table_info(pendientes)")}
        if "conversacion" not in columnas:
            c.execute("ALTER TABLE pendientes ADD COLUMN conversacion TEXT NOT NULL DEFAULT ''")
        if "turno" not in columnas:
            c.execute("ALTER TABLE pendientes ADD COLUMN turno INTEGER NOT NULL DEFAULT 0")

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # --- Auditoria -------------------------------------------------------
    def registrar(
        self,
        *,
        canal: str,
        usuario: str,
        herramienta: str,
        argumentos: dict[str, Any],
        riesgo: str,
        resultado: str,
        error: bool = False,
    ) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO auditoria (ts, canal, usuario, herramienta, argumentos,"
                " riesgo, resultado, error) VALUES (?,?,?,?,?,?,?,?)",
                (
                    time.time(),
                    canal,
                    usuario,
                    herramienta,
                    json.dumps(argumentos, ensure_ascii=False)[:4000],
                    riesgo,
                    resultado[:4000],
                    int(error),
                ),
            )

    def auditoria(self, limite: int = 20) -> list[dict[str, Any]]:
        limite = _acotar(limite, 200)
        with self._conn() as c:
            filas = c.execute(
                "SELECT ts, canal, usuario, herramienta, argumentos, riesgo, resultado, error"
                " FROM auditoria ORDER BY ts DESC LIMIT ?",
                (limite,),
            ).fetchall()
        return [dict(f) for f in filas]

    # --- Acciones pendientes de confirmacion -----------------------------
    def turno_actual(self, conversacion: str) -> int:
        with self._conn() as c:
            return _turno_actual(c, conversacion)

    def nuevo_turno(self, conversacion: str) -> int:
        """Registra que ha llegado un mensaje HUMANO nuevo y devuelve el turno.

        Lo llama el agente una vez por entrada del usuario. Es la referencia
        contra la que se comprueba que una confirmacion llega en un turno
        posterior al que propuso la accion.
        """
        with self._conn() as c:
            c.execute(
                "INSERT INTO turnos (conversacion, contador) VALUES (?, 1)"
                " ON CONFLICT(conversacion) DO UPDATE SET contador = contador + 1",
                (conversacion,),
            )
            fila = c.execute(
                "SELECT contador FROM turnos WHERE conversacion = ?", (conversacion,)
            ).fetchone()
        return int(fila["contador"])

    def crear_pendiente(
        self,
        *,
        canal: str,
        usuario: str,
        conversacion: str,
        herramienta: str,
        argumentos: dict[str, Any],
        resumen: str,
    ) -> str:
        # 128 bits. El token autoriza una accion fisica irreversible, asi que
        # no se recorta: con 32 bits era adivinable dentro de su validez.
        token = secrets.token_urlsafe(16)
        with self._conn() as c:
            c.execute(
                "INSERT INTO pendientes (token, ts, canal, usuario, conversacion,"
                " turno, herramienta, argumentos, resumen)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    token,
                    time.time(),
                    canal,
                    usuario,
                    conversacion,
                    _turno_actual(c, conversacion),
                    herramienta,
                    json.dumps(argumentos, ensure_ascii=False),
                    resumen,
                ),
            )
        return token

    def pendientes_de(
        self, conversacion: str, *, turno: int | None = None
    ) -> list[dict[str, Any]]:
        """Acciones aun sin confirmar, de mas nueva a mas antigua.

        `turno` restringe a las propuestas en ese turno concreto, y el canal lo
        usa siempre. Sin el, una propuesta que el usuario ignoro volveria a
        salir con un boton vivo tras cada mensaje posterior durante 15 minutos;
        y como la pulsacion se salta el guardia de turno, un toque por error
        ejecutaria algo que el usuario ya habia declinado en la practica.
        """
        limite = time.time() - CADUCIDAD_PENDIENTE_S
        condicion = "conversacion = ? AND estado = 'pendiente' AND ts > ?"
        params: list[Any] = [conversacion, limite]
        if turno is not None:
            condicion += " AND turno = ?"
            params.append(turno)
        with self._conn() as c:
            filas = c.execute(
                f"SELECT token, resumen, herramienta, argumentos, ts, turno"
                f" FROM pendientes WHERE {condicion} ORDER BY ts DESC",
                params,
            ).fetchall()
        return [dict(f) for f in filas]

    def tomar_pendiente(
        self,
        token: str,
        *,
        canal: str,
        usuario: str,
        conversacion: str,
        exige_turno_nuevo: bool = True,
    ) -> tuple[dict[str, Any] | None, str]:
        """Consume una accion pendiente de forma atomica.

        Devuelve (accion, motivo). La accion es None si no se puede ejecutar, y
        el motivo dice por que, para poder explicarselo al agente.

        Cuatro comprobaciones, y cada una tapa un agujero concreto:

        * canal y usuario: un token filtrado no permite a otro chat, ni al
          mismo usuario por otro canal, ejecutar la accion de un tercero.
        * caducidad: confirmar a ciegas algo pedido hace dos horas es una via
          de accidentes.
        * turno posterior: la accion se propuso en el turno N, asi que solo se
          puede confirmar en el N+1 o mas tarde. Esto es lo que obliga a que
          haya pasado un mensaje HUMANO por medio. Se salta con
          `exige_turno_nuevo=False`, y solo para la confirmacion fuera de
          banda: una pulsacion de boton en Telegram ES la prueba de que hablo
          un humano, que es justo lo que el guardia de turno intenta
          establecer. Ese camino no pasa por el modelo. Sin esta comprobacion el
          modelo podia proponer y confirmar en la misma vuelta del bucle, y la
          garantia dependia de que respetase el prompt, no del codigo, lo que
          la dejaba al alcance de una inyeccion en cualquier texto que entre
          en su contexto (el nombre de un equipo en la red, el titulo de una
          emisora, el nombre de una entidad).
        """
        with self._conn() as c:
            fila = c.execute(
                "SELECT * FROM pendientes WHERE token = ? AND estado = 'pendiente'",
                (token,),
            ).fetchone()
            if fila is None:
                return None, "no existe, ya se uso o fue cancelada"
            if (
                fila["canal"] != canal
                or fila["usuario"] != usuario
                or (fila["conversacion"] and fila["conversacion"] != conversacion)
            ):
                return None, "pertenece a otra conversacion"
            if time.time() - fila["ts"] > CADUCIDAD_PENDIENTE_S:
                c.execute("UPDATE pendientes SET estado='caducada' WHERE token=?", (token,))
                return None, "ha caducado"
            if exige_turno_nuevo and _turno_actual(c, conversacion) <= fila["turno"]:
                return None, (
                    "el usuario todavia no ha respondido: una accion de riesgo no "
                    "se puede confirmar en el mismo turno en que se propone"
                )
            c.execute("UPDATE pendientes SET estado='ejecutada' WHERE token=?", (token,))
            return (
                {
                    "herramienta": fila["herramienta"],
                    "argumentos": json.loads(fila["argumentos"]),
                    "resumen": fila["resumen"],
                },
                "ok",
            )

    def detalle_pendiente(self, token: str) -> dict[str, Any] | None:
        """Datos de una pendiente sin consumirla, para poder auditarla."""
        with self._conn() as c:
            fila = c.execute(
                "SELECT token, canal, usuario, conversacion, herramienta,"
                " argumentos, resumen, estado FROM pendientes WHERE token = ?",
                (token,),
            ).fetchone()
        if fila is None:
            return None
        datos = dict(fila)
        datos["argumentos"] = json.loads(datos["argumentos"])
        return datos

    def cancelar_pendiente(
        self, token: str, *, canal: str, usuario: str, conversacion: str
    ) -> bool:
        """Descarta una accion concreta. Atada a quien la pidio y donde.

        La conversacion vacia se acepta igual que en `tomar_pendiente`: son
        filas de antes de que existiera esa columna, y si se pueden confirmar
        tienen que poder cancelarse.
        """
        with self._conn() as c:
            cur = c.execute(
                "UPDATE pendientes SET estado='cancelada' WHERE token = ?"
                " AND estado = 'pendiente' AND canal = ? AND usuario = ?"
                " AND (conversacion = ? OR conversacion = '')",
                (token, canal, usuario, conversacion),
            )
            return cur.rowcount > 0

    # --- Bloqueo ---------------------------------------------------------
    # Un interruptor de emergencia: con la casa bloqueada no se ejecuta nada
    # que no sea una lectura, por ningun canal, hasta que el dueno la
    # desbloquee. Es lo que se pulsa si se pierde un movil.

    def bloquear(self, quien: str) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO ajustes (clave, valor) VALUES ('bloqueo', ?)",
                (json.dumps({"quien": quien, "ts": time.time()}),),
            )
            c.execute("UPDATE pendientes SET estado='cancelada' WHERE estado='pendiente'")

    def desbloquear(self) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM ajustes WHERE clave = 'bloqueo'")

    def bloqueo(self) -> dict[str, Any] | None:
        """Quien bloqueo la casa y cuando, o None si esta abierta."""
        with self._conn() as c:
            fila = c.execute("SELECT valor FROM ajustes WHERE clave = 'bloqueo'").fetchone()
        return json.loads(fila["valor"]) if fila else None

    def cancelar_pendientes(self, conversacion: str) -> int:
        """Cancela lo pendiente de una conversacion (p.ej. al hacer /reset)."""
        with self._conn() as c:
            cur = c.execute(
                "UPDATE pendientes SET estado='cancelada'"
                " WHERE conversacion = ? AND estado = 'pendiente'",
                (conversacion,),
            )
            return cur.rowcount

    # --- Historial de conversacion ---------------------------------------
    def anadir_mensaje(self, conversacion: str, rol: str, contenido: Any) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO mensajes (ts, conversacion, rol, contenido) VALUES (?,?,?,?)",
                (time.time(), conversacion, rol, json.dumps(contenido, ensure_ascii=False)),
            )

    def historial(self, conversacion: str, limite: int = 40) -> list[dict[str, Any]]:
        """Devuelve los ultimos mensajes en orden cronologico.

        El historial se recorta al primer mensaje de usuario para no empezar
        nunca con un tool_result huerfano, que la API rechaza.
        """
        limite = _acotar(limite, 400)
        with self._conn() as c:
            filas = c.execute(
                "SELECT rol, contenido FROM mensajes WHERE conversacion = ?"
                " ORDER BY id DESC LIMIT ?",
                (conversacion, limite),
            ).fetchall()
        mensajes = [
            {"role": f["rol"], "content": json.loads(f["contenido"])} for f in reversed(filas)
        ]
        for i, m in enumerate(mensajes):
            if m["role"] == "user" and not _es_solo_tool_result(m["content"]):
                return mensajes[i:]
        return []

    def limpiar_conversacion(self, conversacion: str) -> None:
        """Olvida el historial y cancela lo que estuviera pendiente.

        Dejar vivo un token de una conversacion que el usuario acaba de borrar
        seria un cabo suelto: el usuario cree que no hay nada en curso.
        """
        with self._conn() as c:
            c.execute(
                "UPDATE pendientes SET estado='cancelada'"
                " WHERE conversacion = ? AND estado = 'pendiente'",
                (conversacion,),
            )
            c.execute("DELETE FROM mensajes WHERE conversacion = ?", (conversacion,))

    # --- Programaciones ---------------------------------------------------
    def programar(
        self,
        *,
        canal: str,
        usuario: str,
        conversacion: str,
        orden: str,
        siguiente: float,
        hora: str | None = None,
        dias: str | None = None,
    ) -> int:
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO programaciones (creado, canal, usuario, conversacion, orden,"
                " siguiente, hora, dias) VALUES (?,?,?,?,?,?,?,?)",
                (time.time(), canal, usuario, conversacion, orden, siguiente, hora, dias),
            )
            return int(cur.lastrowid or 0)

    def programaciones(self, *, canal: str, usuario: str) -> list[dict[str, Any]]:
        """Las activas de una persona, la mas proxima primero."""
        with self._conn() as c:
            filas = c.execute(
                "SELECT * FROM programaciones WHERE activa = 1 AND canal = ? AND usuario = ?"
                " ORDER BY siguiente",
                (canal, usuario),
            ).fetchall()
        return [dict(f) for f in filas]

    def programaciones_vencidas(self, ahora: float) -> list[dict[str, Any]]:
        with self._conn() as c:
            filas = c.execute(
                "SELECT * FROM programaciones WHERE activa = 1 AND siguiente <= ?"
                " ORDER BY siguiente",
                (ahora,),
            ).fetchall()
        return [dict(f) for f in filas]

    def reprogramar(self, id_: int, siguiente: float | None) -> None:
        """Siguiente disparo de una repetida, o se desactiva si ya no hay mas."""
        with self._conn() as c:
            if siguiente is None:
                c.execute("UPDATE programaciones SET activa = 0 WHERE id = ?", (id_,))
            else:
                c.execute(
                    "UPDATE programaciones SET siguiente = ? WHERE id = ?", (siguiente, id_)
                )

    def cancelar_programacion(self, id_: int, *, canal: str, usuario: str) -> bool:
        """Solo la suya: nadie cancela lo que programo otro."""
        with self._conn() as c:
            cur = c.execute(
                "UPDATE programaciones SET activa = 0 WHERE id = ? AND activa = 1"
                " AND canal = ? AND usuario = ?",
                (id_, canal, usuario),
            )
            return cur.rowcount > 0


def _turno_actual(c: sqlite3.Connection, conversacion: str) -> int:
    """El contador de turnos humanos, sobre una conexion ya abierta.

    Se lee desde dentro de dos escrituras (al crear y al tomar un pendiente).
    Abrir ahi una conexion anidada era trabajo doble y dejaba una trampa de
    SQLITE_BUSY a un reordenamiento de distancia.
    """
    fila = c.execute(
        "SELECT contador FROM turnos WHERE conversacion = ?", (conversacion,)
    ).fetchone()
    return int(fila["contador"]) if fila else 0


def _acotar(limite: int, tope: int) -> int:
    """En SQLite un LIMIT negativo significa "sin limite".

    Asi que `limite=-1` volcaba la tabla entera. Y un limite enorme tampoco
    tiene sentido: esto lo lee un modelo y un endpoint HTTP.
    """
    return max(1, min(int(limite), tope))


def _es_solo_tool_result(contenido: Any) -> bool:
    if not isinstance(contenido, list):
        return False
    bloques = [b for b in contenido if isinstance(b, dict)]
    return bool(bloques) and all(b.get("type") == "tool_result" for b in bloques)
