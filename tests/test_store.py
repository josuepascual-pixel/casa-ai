"""La persistencia sostiene la confirmacion de acciones de riesgo."""

from __future__ import annotations

import time

from casa_ai import store as modulo_store
from casa_ai.store import Store

CONV = "telegram:123"


def _crear(store: Store, **extra: object) -> str:
    """Crea una pendiente en el turno actual de la conversacion."""
    datos: dict = {
        "canal": "telegram", "usuario": "123", "conversacion": CONV,
        "herramienta": "energia_modo_bateria",
        "argumentos": {"modo": "cargar", "potencia_w": 3000},
        "resumen": "Forzar carga a 3000 W",
    }
    datos.update(extra)
    return store.crear_pendiente(**datos)  # type: ignore[arg-type]


def _tomar(store: Store, token: str, **extra: object):
    datos: dict = {"canal": "telegram", "usuario": "123", "conversacion": CONV}
    datos.update(extra)
    return store.tomar_pendiente(token, **datos)  # type: ignore[arg-type]


def test_pendiente_se_consume_una_sola_vez(store: Store) -> None:
    store.nuevo_turno(CONV)
    token = _crear(store)
    store.nuevo_turno(CONV)  # el usuario responde

    primera, motivo = _tomar(store, token)
    assert primera is not None, motivo
    assert primera["herramienta"] == "energia_modo_bateria"
    assert primera["argumentos"]["potencia_w"] == 3000

    # Un replay del mismo token no debe volver a ejecutar la accion.
    repetida, motivo = _tomar(store, token)
    assert repetida is None
    assert "ya se uso" in motivo


def test_no_se_puede_confirmar_en_el_mismo_turno(store: Store) -> None:
    """El agujero que hacia que la garantia dependiera del prompt.

    Sin esta comprobacion, el modelo podia proponer una accion de riesgo,
    recibir el token y confirmarlo en la misma vuelta del bucle, sin que
    ningun humano dijera nada.
    """
    store.nuevo_turno(CONV)
    token = _crear(store)

    accion, motivo = _tomar(store, token)

    assert accion is None
    assert "mismo turno" in motivo

    # Y en el turno siguiente si vale: el token no se ha quemado.
    store.nuevo_turno(CONV)
    accion, _ = _tomar(store, token)
    assert accion is not None


def test_atada_al_canal_no_solo_al_usuario(store: Store) -> None:
    """El mismo usuario por otro canal tampoco puede confirmarlo."""
    store.nuevo_turno(CONV)
    token = _crear(store)
    store.nuevo_turno(CONV)

    accion, motivo = _tomar(store, token, canal="whatsapp")
    assert accion is None
    assert "otra conversacion" in motivo


def test_atada_a_la_conversacion(store: Store) -> None:
    store.nuevo_turno(CONV)
    token = _crear(store)
    store.nuevo_turno(CONV)
    store.nuevo_turno("telegram:999")

    accion, _ = _tomar(store, token, conversacion="telegram:999")
    assert accion is None


def test_el_token_tiene_entropia_suficiente(store: Store) -> None:
    """Autoriza una accion fisica: 32 bits eran adivinables en 15 minutos."""
    tokens = {_crear(store) for _ in range(50)}
    assert len(tokens) == 50
    assert all(len(x) >= 20 for x in tokens)


def test_los_turnos_son_por_conversacion(store: Store) -> None:
    assert store.turno_actual("a") == 0
    assert store.nuevo_turno("a") == 1
    assert store.nuevo_turno("a") == 2
    assert store.nuevo_turno("b") == 1
    assert store.turno_actual("a") == 2


def test_cancelar_pendientes(store: Store) -> None:
    store.nuevo_turno(CONV)
    token = _crear(store)
    assert store.cancelar_pendientes(CONV) == 1
    store.nuevo_turno(CONV)
    accion, motivo = _tomar(store, token)
    assert accion is None
    assert "cancelada" in motivo


def test_olvidar_la_conversacion_cancela_lo_pendiente(store: Store) -> None:
    """Un token vivo de una conversacion borrada seria un cabo suelto."""
    store.nuevo_turno(CONV)
    token = _crear(store)
    store.limpiar_conversacion(CONV)
    store.nuevo_turno(CONV)

    accion, _ = _tomar(store, token)
    assert accion is None


def test_pendiente_atada_a_su_usuario(store: Store) -> None:
    store.nuevo_turno(CONV)
    token = _crear(store, herramienta="red_wifi_activar",
                   argumentos={"ssid": "Casa", "activar": False},
                   resumen="Apagar el wifi")
    store.nuevo_turno(CONV)

    # Otro chat no puede confirmar lo que pidio otro, aunque tenga el token.
    assert _tomar(store, token, usuario="999")[0] is None
    assert _tomar(store, token)[0] is not None


def test_pendiente_caduca(store: Store, monkeypatch) -> None:
    store.nuevo_turno(CONV)
    token = _crear(store, herramienta="knx_escribir",
                   argumentos={"direccion": "2/1/10", "valor": True},
                   resumen="Abrir riego")
    store.nuevo_turno(CONV)
    monkeypatch.setattr(modulo_store, "CADUCIDAD_PENDIENTE_S", -1)

    accion, motivo = _tomar(store, token)
    assert accion is None
    assert "caducado" in motivo


def test_token_inexistente(store: Store) -> None:
    assert _tomar(store, "noexiste")[0] is None


def test_historial_no_empieza_por_tool_result(store: Store) -> None:
    """La API rechaza un historial que arranque con un tool_result huerfano."""
    store.anadir_mensaje("c1", "assistant", [{"type": "tool_use", "id": "t1", "name": "x"}])
    store.anadir_mensaje("c1", "user", [{"type": "tool_result", "tool_use_id": "t1"}])
    store.anadir_mensaje("c1", "user", [{"type": "text", "text": "hola"}])
    store.anadir_mensaje("c1", "assistant", [{"type": "text", "text": "que tal"}])

    historial = store.historial("c1")
    assert historial[0]["role"] == "user"
    assert historial[0]["content"][0]["text"] == "hola"
    assert len(historial) == 2


def test_historial_vacio_si_solo_hay_tool_results(store: Store) -> None:
    store.anadir_mensaje("c2", "user", [{"type": "tool_result", "tool_use_id": "t1"}])
    assert store.historial("c2") == []


def test_auditoria_ordenada_y_limitada(store: Store) -> None:
    for i in range(5):
        store.registrar(
            canal="cli",
            usuario="local",
            herramienta=f"h{i}",
            argumentos={"i": i},
            riesgo="alto",
            resultado="ok",
        )
        time.sleep(0.001)
    registros = store.auditoria(3)
    assert len(registros) == 3
    assert registros[0]["herramienta"] == "h4"


def test_limpiar_conversacion(store: Store) -> None:
    store.anadir_mensaje("c3", "user", [{"type": "text", "text": "hola"}])
    store.limpiar_conversacion("c3")
    assert store.historial("c3") == []


def test_un_limite_negativo_no_vuelca_la_tabla(store: Store) -> None:
    """En SQLite `LIMIT -1` significa "sin limite": limite=-1 devolvia todo."""
    for i in range(30):
        store.registrar(
            canal="cli", usuario="local", herramienta=f"h{i}",
            argumentos={}, riesgo="alto", resultado="ok",
        )

    assert len(store.auditoria(-1)) == 1
    assert len(store.auditoria(0)) == 1
    assert len(store.auditoria(5)) == 5
    assert len(store.auditoria(10_000)) == 30  # se topa, no se desborda


def test_un_limite_negativo_tampoco_en_el_historial(store: Store) -> None:
    for i in range(5):
        store.anadir_mensaje("c", "user", [{"type": "text", "text": f"m{i}"}])
    assert len(store.historial("c", -1)) <= 1


# --- Confirmacion fuera de banda --------------------------------------------


def test_pendientes_de_una_conversacion(store: Store) -> None:
    store.nuevo_turno(CONV)
    token = _crear(store)
    _crear(store, conversacion="telegram:999")

    pendientes = store.pendientes_de(CONV)

    assert [p["token"] for p in pendientes] == [token]
    assert pendientes[0]["resumen"] == "Forzar carga a 3000 W"


def test_las_pendientes_caducadas_no_se_ofrecen(store: Store, monkeypatch) -> None:
    store.nuevo_turno(CONV)
    _crear(store)
    monkeypatch.setattr(modulo_store, "CADUCIDAD_PENDIENTE_S", -1)
    assert store.pendientes_de(CONV) == []


def test_una_pulsacion_de_boton_no_necesita_turno_nuevo(store: Store) -> None:
    """El guardia de turno existe para probar que hablo un humano. Una
    pulsacion ES esa prueba, y ese camino no pasa por el modelo."""
    store.nuevo_turno(CONV)
    token = _crear(store)

    # Por conversacion, en el mismo turno, no se puede.
    assert _tomar(store, token)[0] is None

    # Con el boton si.
    accion, motivo = store.tomar_pendiente(
        token, canal="telegram", usuario="123", conversacion=CONV,
        exige_turno_nuevo=False,
    )
    assert accion is not None, motivo


def test_el_boton_sigue_atado_al_chat_que_lo_pidio(store: Store) -> None:
    """Saltarse el turno no significa saltarse el resto de comprobaciones."""
    store.nuevo_turno(CONV)
    token = _crear(store)

    accion, _ = store.tomar_pendiente(
        token, canal="whatsapp", usuario="123", conversacion=CONV,
        exige_turno_nuevo=False,
    )
    assert accion is None

    accion, _ = store.tomar_pendiente(
        token, canal="telegram", usuario="otro", conversacion=CONV,
        exige_turno_nuevo=False,
    )
    assert accion is None


def test_cancelar_una_pendiente_concreta(store: Store) -> None:
    store.nuevo_turno(CONV)
    token = _crear(store)

    assert store.cancelar_pendiente(
        token, canal="telegram", usuario="123", conversacion=CONV
    ) is True
    # Y no se puede cancelar dos veces ni desde otro chat.
    assert store.cancelar_pendiente(
        token, canal="telegram", usuario="123", conversacion=CONV
    ) is False


def test_no_se_cancela_lo_de_otro_chat(store: Store) -> None:
    store.nuevo_turno(CONV)
    token = _crear(store)
    assert store.cancelar_pendiente(
        token, canal="telegram", usuario="intruso", conversacion=CONV
    ) is False
    # Sigue viva para su dueno.
    assert store.pendientes_de(CONV)


def test_las_pendientes_se_pueden_filtrar_por_turno(store: Store) -> None:
    """El canal solo ofrece boton para lo propuesto en el turno en curso."""
    store.nuevo_turno(CONV)
    viejo = _crear(store, resumen="Apagar el wifi")
    store.nuevo_turno(CONV)
    nuevo = _crear(store, resumen="Forzar carga")

    del_turno_actual = store.pendientes_de(CONV, turno=store.turno_actual(CONV))
    assert [p["token"] for p in del_turno_actual] == [nuevo]

    # Sin filtro siguen estando las dos: el viejo no se ha perdido, solo no se
    # vuelve a ofrecer con un boton.
    todas = {p["token"] for p in store.pendientes_de(CONV)}
    assert todas == {viejo, nuevo}


def test_detalle_pendiente_no_la_consume(store: Store) -> None:
    store.nuevo_turno(CONV)
    token = _crear(store)

    detalle = store.detalle_pendiente(token)
    assert detalle is not None
    assert detalle["herramienta"] == "energia_modo_bateria"
    assert detalle["argumentos"]["potencia_w"] == 3000
    assert detalle["estado"] == "pendiente"

    # Sigue disponible para confirmarse.
    store.nuevo_turno(CONV)
    assert _tomar(store, token)[0] is not None


def test_detalle_de_un_token_inexistente(store: Store) -> None:
    assert store.detalle_pendiente("nada") is None


def test_una_fila_sin_conversacion_se_puede_cancelar(store: Store) -> None:
    """Filas de antes de que existiera la columna: si se pueden confirmar,
    tienen que poder cancelarse."""
    token = _crear(store, conversacion="")

    assert store.cancelar_pendiente(
        token, canal="telegram", usuario="123", conversacion=CONV
    ) is True
