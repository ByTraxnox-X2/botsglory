# -*- coding: utf-8 -*-
"""
Cerebro de las escuadras (GUILD GLORY BOT) - v13.5

Cada escuadra tiene UN líder fijo: el primer bot de la escuadra (en el orden de
accounts.json, el mismo que ves en el panel). Solo él crea el equipo, invita,
le da a INICIAR y, al terminar cada partida, disuelve el equipo para armarlo
de nuevo desde cero. Los demás solo aceptan la invitación de su líder.

Una ronda (la hace el líder):
  1. Espera a que todos los bots de su escuadra salgan de la partida (un bot que
     muere se queda mirando a los suyos; vuelven juntos cuando muere el último).
  2. Disuelve: todos los bots de la escuadra salen de su equipo y quedan solos.
  3. Crea un equipo nuevo y espera el código que manda el juego.
  4. Invita a sus compañeros. Cada uno acepta UNA vez esa invitación (si no le
     llega, entra con el código del equipo).
  5. Le da a INICIAR. Si en 2,5 min no hay partida, vuelve al paso 2.

Reglas:
  * El líder no cambia. Solo si se desconecta y no vuelve en 60 s, el siguiente
    bot de la escuadra lleva el equipo mientras tanto; cuando el líder vuelve,
    retoma el mando al empezar la siguiente ronda (nunca a mitad de una búsqueda).
  * Un miembro solo acepta la invitación que su líder le acaba de mandar en ESTA
    ronda. Los avisos viejos o repetidos del juego no se aceptan: aceptar dos
    veces lo saca del equipo y le corta la búsqueda a toda la escuadra.
  * Ninguna espera es infinita.
  * Si el modo elegido no arma equipo o no encuentra partida 2 rondas seguidas,
    la escuadra juega Battle Royale clásico un rato (así nunca se queda parada)
    y después vuelve a probar el modo elegido.

Todo lo que habla con el juego entra por el objeto `io` (ver main.py). Así este
archivo se puede probar con un simulador sin abrir el juego.
"""


class Ajustes:
    """Tiempos, en segundos."""
    PAUSA_TRAS_PARTIDA = 2.0       # todos de vuelta → espera esto y arma el equipo
    PAUSA_TRAS_SALIR = 1.0         # tras disolver, respiro antes de crear el equipo
    ESPERA_CODIGO = 3.0            # tras crear, cuánto espera el código del juego
    ESPERA_ACEPTAR = 5.0           # tras invitar, tope para que acepten
    ESPERA_ANTES_DE_CODIGO = 4.0   # si al miembro no le llega la invitación en esto, entra con el código
    ESPERA_ACEPTAR_2 = 4.0         # segunda invitación a los que no contestaron
    INVITACION_VALE = 30.0         # una invitación sin aceptar caduca tras esto
    REINVITAR_BUSCANDO = 30.0      # buscando partida: reinvita a los que faltan cada tanto
    REINTENTO_INICIAR = 10.0       # buscando partida: vuelve a darle a INICIAR cada tanto
    REINVITAR_ARMANDO = 8.0        # esperando a que estén todos: reinvita a los que faltan cada tanto
    ESPERA_COMPLETAR = 25.0        # tras invitar: tope para tener a todos en el equipo antes de iniciar
    REARMAR_SIN_PARTIDA = 150.0    # buscando sin partida este rato → ronda nueva (mismo líder)
    PAUSA_EQUIPO_FALLIDO = 2.0     # el equipo no se armó → espera esto y prueba otra ronda
    FALLOS_PARA_MODO_SEGURO = 2    # rondas fallidas seguidas en el modo elegido → BR clásico
    MODO_SEGURO_DURA = 3600.0      # cuánto juega BR clásico antes de volver a probar
    PARTIDA_MAX = 1590.0           # una marca de "en partida" más vieja que esto se borra
    LISTO_CADA = 5.0               # el miembro avisa "listo" cada tanto
    ESPERA_INICIAL_MIEMBRO = 1.0   # al conectar, el miembro espera esto antes de empezar
    ESPERA_MIEMBRO_TRAS_PARTIDA = 0.5
    ESPERA_EQUIPO_MAX = 10.0       # al arrancar, cuánto espera a que se conecten sus bots antes de armar
    ESPERA_EQUIPO_REARME = 10.0    # al volver de partida, cuánto espera a un bot desconectado
    ESPERA_ARRANQUE = 60.0         # al arrancar: no inicia hasta tener a todos en el equipo (tope)
    ESPERA_LIDER_MAX = 60.0        # el líder no está: tras esto, el siguiente lleva el equipo
    LIDER_ESTABLE = 20.0           # tras volver, el líder retoma el mando solo si aguanta conectado esto
    ESCALON_ESCUADRA = 1.2         # cada escuadra arranca un poquito después que la anterior
    CON_HUMANO_DURA = 3600.0       # entraste tú al equipo: 1 hora sin disolver (desde tu último toque en "Esperarme")
    HUMANO_RONDAS_SIN_PARTIDA = 4  # con tú dentro: 4 rondas seguidas sin partida (≈10 min) → el equipo se arma de nuevo
    AVISO_CADA = 20.0              # mensajes repetidos en consola, como mucho cada tanto


# ----------------------------------------------------------------------------------
#  Estado compartido de una escuadra (vive dentro del dict de la escuadra en main.py)
# ----------------------------------------------------------------------------------
_DICTS = ("en_equipo", "en_partida", "invitado", "grupo", "muertos", "codigo_usado")


def preparar(sq):
    """Asegura las claves que usa el cerebro."""
    for k in _DICTS:
        if not isinstance(sq.get(k), dict):
            sq[k] = {}
    if not isinstance(sq.get("jugaron"), set):
        sq["jugaron"] = set()
    if not isinstance(sq.get("estable_hasta"), dict):
        sq["estable_hasta"] = {}       # bot -> cuándo se fue tras estar conectado un buen rato
    if not isinstance(sq.get("ronda"), int):
        sq["ronda"] = 0
    if not isinstance(sq.get("fallos_modo"), int):
        sq["fallos_modo"] = 0
    sq.setdefault("modo_seguro_hasta", 0.0)
    sq.setdefault("modo_ya_fallo", "")
    sq.setdefault("titular_falta_desde", None)


def conectados(sq):
    return list((sq.get("peers") or {}).keys())


def marcar_entra_partida(sq, uid, ahora):
    """Un bot de la escuadra entró a una partida."""
    preparar(sq)
    uid = str(uid)
    sq["en_partida"][uid] = float(ahora)
    sq["muertos"].pop(uid, None)
    sq["jugaron"].add(uid)
    sq["invitado"].pop(uid, None)
    sq["fallos_modo"] = 0          # hubo partida: el modo funciona
    if not en_modo_seguro(sq, ahora):
        sq["modo_ya_fallo"] = ""   # jugó en el modo elegido: se le vuelven a dar 2 rondas
    lider = str(sq.get("leader_uid") or "")
    if lider and uid != lider:
        sq["en_equipo"][uid] = ("jugo", float(ahora), sq["ronda"])


def marcar_sale_partida(sq, uid):
    """Un bot salió de su partida (terminó bien, falló al entrar, lo que sea)."""
    preparar(sq)
    sq["en_partida"].pop(str(uid), None)
    sq["muertos"].pop(str(uid), None)


def marcar_muerto(sq, uid, ahora):
    """El bot murió dentro de la partida: se queda mirando a su escuadra (sigue en partida)."""
    preparar(sq)
    uid = str(uid)
    if uid in sq["en_partida"] and uid not in sq["muertos"]:
        sq["muertos"][uid] = float(ahora)
        return True
    return False


def esta_muerto(sq, uid):
    preparar(sq)
    return str(uid) in sq["muertos"]


def vivos_en_partida(sq, excluir=None):
    """Bots de la escuadra que siguen VIVOS dentro de la partida (sin contar a `excluir`)."""
    preparar(sq)
    peers = sq.get("peers") or {}
    return [u for u in sq["en_partida"]
            if u != excluir and u in peers and u not in sq["muertos"]]


def anotar_salida(sq, uid, ahora):
    """Llamar ANTES de soltar a un bot que se desconecta (todavía está en peers).
    Si estuvo conectado un buen rato, la espera por él empieza AHORA y no desde una
    falta vieja (por ejemplo, de cuando sus compañeros se conectaron antes que él)."""
    preparar(sq)
    uid = str(uid)
    if _conectado_hace(sq, uid, ahora) >= Ajustes.LIDER_ESTABLE:
        sq["estable_hasta"][uid] = float(ahora)


def marcar_desconectado(sq, uid):
    """Un bot se desconectó: se borra todo lo suyo."""
    preparar(sq)
    uid = str(uid)
    for k in _DICTS:
        sq[k].pop(uid, None)
    sq["jugaron"].discard(uid)


def en_partida_ahora(sq, ahora, excluir=None):
    """Compañeros CONECTADOS que siguen dentro de una partida. Borra marcas viejas o de
    bots que ya no están, así nunca se queda esperando a un fantasma."""
    preparar(sq)
    peers = sq.get("peers") or {}
    quedan = []
    for u, t in list(sq["en_partida"].items()):
        try:
            vieja = (float(ahora) - float(t)) > Ajustes.PARTIDA_MAX
        except Exception:
            vieja = True
        if u not in peers or vieja:
            sq["en_partida"].pop(u, None)
            continue
        if u != excluir:
            quedan.append(u)
    return quedan


def nueva_ronda(sq):
    """El líder va a armar un equipo nuevo: nadie está dentro todavía y las
    invitaciones de antes ya no valen."""
    preparar(sq)
    sq["ronda"] += 1
    sq["en_equipo"].clear()
    sq["invitado"].clear()
    sq["grupo"].clear()
    sq["jugaron"] = set()
    return sq["ronda"]


def equipo_nuevo(sq):
    """Nombre viejo de nueva_ronda (por si algo de afuera lo llama)."""
    return nueva_ronda(sq)


def hay_equipo(sq):
    """True si el equipo ya existe: el juego ya dio su código o hay algún compañero dentro.
    Sirve para no tratar como 'equipo que hay que cuidar' algo que todavía no se creó."""
    preparar(sq)
    return bool(sq.get("team_code")) or bool(sq["en_equipo"])


def marcar_invitado(sq, uid, ahora):
    """El líder va a invitar a este bot ahora: su próxima invitación sí se acepta."""
    preparar(sq)
    sq["invitado"][str(uid)] = (sq["ronda"], float(ahora))


def faltan_en_equipo(sq, lider, pausado=None):
    """Compañeros conectados (y no pausados ni en partida) que todavía no están en el equipo."""
    preparar(sq)
    salida = []
    for u in conectados(sq):
        if u == lider or u in sq["en_equipo"] or u in sq["en_partida"]:
            continue
        if pausado and pausado(u):
            continue
        salida.append(u)
    return salida


def revisar_ultima_partida(sq, lider):
    """Solo cuando estás tú en el equipo (no se disuelve): quien jugó sigue dentro;
    quien no jugó, falta y se le vuelve a invitar. Devuelve (jugaron, no_jugaron)."""
    preparar(sq)
    jug = set(sq["jugaron"])
    if not jug:
        return None
    sq["jugaron"] = set()
    otros = [u for u in conectados(sq) if u != lider]
    si = [u for u in otros if u in jug]
    no = [u for u in otros if u not in jug]
    for u in no:
        sq["en_equipo"].pop(u, None)
        sq["invitado"].pop(u, None)
    return si, no


def decidir_aceptar(sq, uid, ids_en_paquete, grupo, ahora):
    """¿Este paquete de equipo es la invitación que este bot tiene que aceptar?
    Devuelve el id del líder al que hay que aceptarle, o None.

    - Solo se acepta al líder de SU escuadra (su id tiene que venir en el paquete).
    - Solo si el líder lo acaba de invitar en esta ronda, y UNA sola vez por
      invitación. El juego manda varios avisos sobre el mismo equipo; aceptar otra
      vez lo saca y lo vuelve a meter, y eso corta la búsqueda de partida.
    """
    preparar(sq)
    lider = str(sq.get("leader_uid") or "")
    uid = str(uid)
    if not lider or lider == uid or not grupo:
        return None
    ids = {str(x) for x in (ids_en_paquete or ())}
    if lider not in ids:
        return None
    inv = sq["invitado"].get(uid)
    if not inv:
        return None
    ronda, t = inv
    if ronda != sq["ronda"] or float(ahora) - float(t) > Ajustes.INVITACION_VALE:
        sq["invitado"].pop(uid, None)
        return None
    sq["invitado"].pop(uid, None)
    sq["en_equipo"][uid] = ("acepto", float(ahora), ronda)
    sq["grupo"][uid] = str(grupo)
    return lider


def elegir_lider(sq, orden, ahora, pausado=None, en_frontera=False):
    """Quién lleva la escuadra. Devuelve (lider, cambio).

    El titular es el primer bot de la escuadra según `orden`. Si está conectado,
    él es el líder. Si no está (o lo pausaste), se le espera ESPERA_LIDER_MAX y
    después lleva el equipo el siguiente conectado. Cuando el titular vuelve, solo
    retoma el mando en una frontera de ronda (en_frontera=True), nunca a mitad de
    una búsqueda.
    """
    preparar(sq)
    peers = sq.get("peers") or {}
    orden = [str(u) for u in (orden or [])]

    def ok(u):
        return u in peers and not (pausado and pausado(u))

    activos = [u for u in orden if ok(u)] + [u for u in peers if u not in orden and ok(u)]
    titular = orden[0] if orden else (activos[0] if activos else None)
    actual = str(sq.get("leader_uid") or "") or None
    actual_ok = bool(actual) and ok(actual)

    if titular and ok(titular):
        # Un titular que se cae y vuelve a cada rato (cuenta bloqueada, red mala) no debe
        # dejar a su escuadra parada: la cuenta de "no está" solo se borra cuando aguanta
        # LIDER_ESTABLE conectado, y solo así le quita el mando a quien lo lleva.
        estable = _conectado_hace(sq, titular, ahora) >= Ajustes.LIDER_ESTABLE
        if estable:
            sq["titular_falta_desde"] = None
        if actual_ok and actual != titular:
            nuevo = titular if (en_frontera and estable) else actual
        elif actual_ok:
            nuevo = actual
        elif estable or sq.get("titular_falta_desde") is None:
            nuevo = titular
        else:
            # Volvió hace poco tras faltar: lleva el equipo si todavía no se cumplió la
            # espera; si ya se cumplió y hay otro bot, lo lleva el otro.
            esperado = float(ahora) - float(sq["titular_falta_desde"])
            otros = [u for u in activos if u != titular]
            nuevo = otros[0] if (otros and esperado >= Ajustes.ESPERA_LIDER_MAX) else titular
    else:
        titular_pausado = bool(titular and pausado and pausado(titular))
        falta = sq.get("titular_falta_desde")
        salio = sq["estable_hasta"].get(titular) if titular else None
        if falta is None:
            sq["titular_falta_desde"] = float(ahora)
        elif salio is not None and float(salio) > float(falta):
            # Estuvo bien conectado después de esa falta vieja: la espera cuenta desde que se fue
            sq["titular_falta_desde"] = float(salio)
        esperado = float(ahora) - float(sq["titular_falta_desde"])
        if actual_ok:
            nuevo = actual
        elif activos and (titular_pausado or not titular or esperado >= Ajustes.ESPERA_LIDER_MAX):
            nuevo = activos[0]
        else:
            nuevo = None

    cambio = nuevo != actual
    if cambio:
        sq["leader_uid"] = nuevo
        sq["team_code"] = None
    return nuevo, cambio


def _conectado_hace(sq, u, ahora):
    """Hace cuántos segundos se conectó ese bot (0 si no se sabe)."""
    try:
        t = ((sq.get("peers") or {}).get(u) or {}).get("conectado")
        return max(0.0, float(ahora) - float(t)) if t is not None else 0.0
    except Exception:
        return 0.0


def registrar_fallo(sq, ahora, especial):
    """Una ronda terminó sin equipo o sin partida. Devuelve True si toca pasar a
    Battle Royale clásico (modo seguro)."""
    preparar(sq)
    if not especial:
        sq["fallos_modo"] = 0
        return False
    sq["fallos_modo"] += 1
    modo = str(sq.get("modo") or "")
    # Si este mismo modo ya falló antes (y desde entonces no se jugó ninguna partida en él),
    # con una sola ronda fallida vuelve a BR: así la escuadra no pierde 5 minutos cada hora.
    tope = 1 if (modo and sq.get("modo_ya_fallo") == modo) else Ajustes.FALLOS_PARA_MODO_SEGURO
    if sq["fallos_modo"] >= tope:
        sq["fallos_modo"] = 0
        sq["modo_seguro_hasta"] = float(ahora) + Ajustes.MODO_SEGURO_DURA
        sq["modo_ya_fallo"] = modo
        return True
    return False


def en_modo_seguro(sq, ahora):
    try:
        return float(sq.get("modo_seguro_hasta") or 0) > float(ahora)
    except Exception:
        return False


def huella(sq):
    """Cuándo se conectó cada bot. Si cambia, es que se reconectó."""
    salida = {}
    for u, info in (sq.get("peers") or {}).items():
        try:
            salida[u] = (info or {}).get("conectado")
        except Exception:
            salida[u] = None
    return salida


# ----------------------------------------------------------------------------------
#  Ciclos (uno por bot): el del líder y el del miembro
# ----------------------------------------------------------------------------------
class _Avisos:
    """Para no llenar la consola con el mismo mensaje."""
    def __init__(self, io):
        self.io = io
        self.t = {}

    def una_vez_cada(self, clave, nivel, texto, cada=Ajustes.AVISO_CADA):
        ahora = self.io.ahora()
        if ahora - self.t.get(clave, -1e9) >= cada:
            self.t[clave] = ahora
            self.io.log(nivel, texto)


def _miembros_para_invitar(io, sq, yo):
    return faltan_en_equipo(sq, yo, io.esta_pausado)


def _nombres(io, uids):
    """Nombres cortos para la consola (apodo del bot si se sabe)."""
    try:
        return ", ".join(str(io.nombre(u)) for u in uids)
    except Exception:
        return ", ".join(str(u) for u in uids)


async def _invitar(io, sq, uids):
    for u in uids:
        marcar_invitado(sq, u, io.ahora())
        try:
            await io.invitar(io.id_juego(u))
        except Exception as e:
            io.log("aviso", f"no se pudo invitar a {u}: {e}")
        await io.dormir(0.35)


async def _esperar_aceptan(io, sq, uids, tope):
    """Espera a que esos bots acepten. Devuelve los que siguen sin aceptar (y siguen
    conectados)."""
    t0 = io.ahora()
    while True:
        peers = sq.get("peers") or {}
        quedan = [u for u in uids if u in peers and u not in sq["en_equipo"]]
        if not quedan or io.ahora() - t0 >= tope:
            return quedan
        await io.dormir(0.4)


async def _dormir_atento(io, sq, yo, segundos):
    """Duerme, pero se despierta enseguida si alguien de la escuadra entra a partida."""
    fin = io.ahora() + segundos
    while io.ahora() < fin and io.activo():
        if en_partida_ahora(sq, io.ahora(), excluir=yo):
            return
        await io.dormir(min(0.5, max(0.05, fin - io.ahora())))


async def ciclo_lider(io, rearme=False):
    """Lo corren todos los bots; solo sigue de largo el que es líder."""
    A = Ajustes
    sq, yo = io.sq, str(io.uid)
    preparar(sq)
    av = _Avisos(io)

    # 1) Esperar a que estén conectados los bots de la escuadra (con tope)
    tope = A.ESPERA_EQUIPO_REARME if rearme else A.ESPERA_EQUIPO_MAX
    desde = io.ahora()
    while True:
        if io.pausado():
            return
        n = len(sq.get("peers") or {})
        esperados = max(1, int(io.esperados() or 1))
        if n >= esperados:
            break
        if n >= 1 and io.ahora() - desde >= tope:
            av.una_vez_cada("faltan", "aviso",
                            f"faltan {esperados - n} bot(s) por conectar; juegan los {n} que están")
            break
        io.fase("esperando")
        av.una_vez_cada("conectar", "info", f"esperando a sus bots ({n}/{esperados})")
        await io.dormir(0.5)

    # 2) ¿Quién lleva la escuadra? Solo el líder actual (o nadie) decide en la frontera.
    actual = str(sq.get("leader_uid") or "")
    frontera = (not actual) or actual == yo
    lider, cambio = elegir_lider(sq, io.orden(), io.ahora(), io.esta_pausado, en_frontera=frontera)
    if cambio and lider:
        io.log("lider", f"el líder de la escuadra es {lider}")
    if lider != yo:
        return  # es miembro: lo suyo lo hace ciclo_miembro

    # 3) El líder: rondas
    escalonado = rearme
    hubo_partida = rearme
    arranque = not rearme          # primera ronda tras conectarse: espera a que estén todos
    fallidas_seguidas = 0
    sin_partida_humano = 0         # rondas seguidas sin partida estando tú en el equipo

    while io.activo():
        if io.pausado():
            return
        if str(sq.get("leader_uid") or "") != yo:
            io.hacerse_miembro()
            return
        ahora = io.ahora()

        # a) Compañeros todavía dentro de una partida → se espera
        en_p = en_partida_ahora(sq, ahora, excluir=yo)
        if en_p:
            sin_partida_humano = 0          # hubo partida: el equipo sí está funcionando
            io.fase("esperando_partida")
            av.una_vez_cada("en_partida", "info",
                            f"espera a que terminen la partida {len(en_p)} compañero(s)")
            hubo_partida = True
            await io.dormir(2.0)
            continue

        # b) Pediste entrar tú: la escuadra se queda quieta e invita a tu cuenta
        if await io.toca_esperar():
            await io.invitar_usuario()
            await io.dormir(2.0)
            continue

        # c) Frontera de ronda: si el líder titular volvió, le devuelve el mando
        lider, cambio = elegir_lider(sq, io.orden(), io.ahora(), io.esta_pausado, en_frontera=True)
        if lider != yo:
            if lider:
                io.log("lider", f"vuelve el líder {lider}; {yo} le devuelve el mando")
            io.hacerse_miembro()
            return

        # d) Pausa corta tras la partida
        if hubo_partida:
            hubo_partida = False
            io.fase("revisando")
            io.log("info", f"todos fuera de la partida; arma el equipo en {A.PAUSA_TRAS_PARTIDA:.0f} s")
            await io.dormir(A.PAUSA_TRAS_PARTIDA)
            continue   # vuelve a mirar, por si alguien entró a partida mientras tanto

        # e) Armar el equipo
        codigo = sq.get("team_code")
        if not io.con_humano():
            sin_partida_humano = 0
        # Estás tú en el equipo (y el equipo ya existe): no se disuelve; solo se invita a los
        # bots que faltan. Si todavía no hay equipo, se crea igual (si no, nunca se armaría).
        # Red de seguridad: si llevan muchas rondas sin partida, se arma de nuevo (tendrás que
        # volver a entrar con el código); así no se queda atorado durante la hora entera.
        humano_en_equipo = io.con_humano() and hay_equipo(sq)
        if humano_en_equipo and sin_partida_humano >= Ajustes.HUMANO_RONDAS_SIN_PARTIDA:
            io.log("aviso", f"{sin_partida_humano} rondas sin partida con tú dentro; arma el equipo de nuevo "
                            f"(vuelve a entrar con el código)")
            humano_en_equipo = False
            sin_partida_humano = 0
        if humano_en_equipo:
            r = revisar_ultima_partida(sq, yo)
            if r is not None and r[1]:
                io.log("aviso", f"{len(r[1])} bot(s) no jugaron la última partida; los invita")
        else:
            io.fase("armando")
            io.estado("ARMANDO")
            salieron = await io.disolver()
            io.log("lider", f"{yo} disuelve el equipo ({salieron} bot(s) quedan solos) y arma uno nuevo")
            await io.dormir(A.PAUSA_TRAS_SALIR)
            nueva_ronda(sq)
            await io.crear_equipo()
            t0 = io.ahora()
            codigo = None
            while io.ahora() - t0 < A.ESPERA_CODIGO:
                codigo = sq.get("team_code")
                if codigo:
                    break
                await io.dormir(0.25)
            if codigo:
                io.log("ok", f"equipo creado (código {codigo})")
            else:
                av.una_vez_cada("sin_codigo", "aviso", "el juego no mandó el código del equipo")
            await io.refrescar_equipo()
            await io.dormir(0.4)

        if not escalonado:
            escalonado = True
            espera = ((max(1, int(io.numero_escuadra() or 1)) - 1) % 4) * A.ESCALON_ESCUADRA
            if espera:
                await io.dormir(espera)

        # f) Invitar a los compañeros y darles tiempo de aceptar
        faltan = _miembros_para_invitar(io, sq, yo)
        invitados = len(faltan)
        quedan = []
        if faltan:
            io.fase("invitando")
            io.log("info", f"invita a {len(faltan)} bot(s)")
            await _invitar(io, sq, faltan)
            quedan = await _esperar_aceptan(io, sq, faltan, A.ESPERA_ACEPTAR)
            if quedan:
                io.log("aviso", f"no contestaron {_nombres(io, quedan)}; los invita otra vez")
                await _invitar(io, sq, quedan)
                quedan = await _esperar_aceptan(io, sq, quedan, A.ESPERA_ACEPTAR_2)
            dentro = 1 + len([u for u in conectados(sq) if u in sq["en_equipo"] and u != yo])
            if quedan:
                io.log("aviso", f"{dentro} en el equipo; no entraron {_nombres(io, quedan)}")
            else:
                io.log("ok", f"los {dentro} están en el equipo")
            await io.refrescar_equipo()
            await io.dormir(0.6)

        # El equipo no existe: nadie aceptó y el juego no mandó código. Se prueba otra
        # ronda enseguida (cada vez espera un poco más). A la 3.ª seguida busca partida
        # igual con los que haya, para que el líder nunca se quede parado.
        if invitados and len(quedan) == invitados and not codigo and not humano_en_equipo:
            fallidas_seguidas += 1
            if registrar_fallo(sq, io.ahora(), io.modo_especial()):
                io.log("aviso", "el modo elegido no arma equipo; juega Battle Royale clásico un rato")
            if fallidas_seguidas < 3:
                espera = A.PAUSA_EQUIPO_FALLIDO * fallidas_seguidas
                io.log("aviso", f"el equipo no se armó (nadie recibió la invitación); "
                                f"lo intenta de nuevo en {espera:.0f} s")
                await io.dormir(espera)
                continue
            io.log("aviso", "el equipo sigue sin armarse; busca partida igual con los que hay")
        fallidas_seguidas = 0

        # g) Darle a INICIAR hasta que llegue la partida (la tarea se corta sola cuando llega).
        #    Antes de iniciar espera a tener a TODOS sus compañeros conectados dentro del
        #    equipo (los reinvita cada pocos segundos). Al arrancar (bots todavía
        #    conectándose) espera también a los que faltan por entrar. Con tope: si alguno
        #    no entra, inicia con los que están.
        busca_desde = io.ahora()
        ult_reinv = io.ahora()
        intentos = 0
        conocidos = huella(sq)
        sin_partida = False
        en_arranque = arranque
        completar_hasta = io.ahora() + (A.ESPERA_ARRANQUE if arranque else A.ESPERA_COMPLETAR)
        arranque = False
        avisado_completar = False
        while io.activo():
            if io.pausado():
                return
            if str(sq.get("leader_uid") or "") != yo:
                io.hacerse_miembro()
                return
            if en_partida_ahora(sq, io.ahora(), excluir=yo):
                break   # la escuadra entró a partida (y a este bot no le llegó el aviso)
            if await io.toca_esperar():
                break   # pediste entrar tú: se queda quieta (arriba)

            completando = io.ahora() < completar_hasta
            # Bot que se acaba de (re)conectar y no está en el equipo: se le invita
            ahora_h = huella(sq)
            nuevos = [u for u, h in ahora_h.items()
                      if u != yo and conocidos.get(u, "x") != h and u not in sq["en_equipo"]
                      and not io.esta_pausado(u)]
            conocidos = ahora_h
            if nuevos:
                io.log("info", f"se conectó {len(nuevos)} bot(s); los invita")
                await _invitar(io, sq, nuevos)
                ult_reinv = io.ahora()
            elif io.ahora() - ult_reinv >= (A.REINVITAR_ARMANDO if completando else A.REINVITAR_BUSCANDO):
                ult_reinv = io.ahora()
                faltan = _miembros_para_invitar(io, sq, yo)
                if faltan:
                    io.log("info", f"reinvita a {_nombres(io, faltan)} (no están en el equipo)")
                    await _invitar(io, sq, faltan)

            if completando:
                faltan = _miembros_para_invitar(io, sq, yo)
                dentro = 1 + len([u for u in conectados(sq) if u in sq["en_equipo"] and u != yo])
                if en_arranque:
                    esperados = max(1, int(io.esperados() or 1))
                    falta = dentro < esperados
                else:
                    falta = bool(faltan)
                if falta:
                    io.fase("armando")
                    if not avisado_completar:
                        avisado_completar = True
                        if en_arranque:
                            io.log("info", f"arma el equipo mientras se conectan ({dentro}/{esperados}); "
                                           f"inicia apenas estén todos")
                        else:
                            io.log("info", f"espera a {_nombres(io, faltan)} para iniciar con todos")
                    await io.dormir(1.0)
                    busca_desde = io.ahora()
                    continue
                completar_hasta = 0.0
                if avisado_completar:
                    io.log("ok", f"los {dentro} están en el equipo")
            elif completar_hasta:
                completar_hasta = 0.0
                faltan = _miembros_para_invitar(io, sq, yo)
                if faltan:
                    io.log("aviso", f"inicia sin {_nombres(io, faltan)} (no entraron al equipo)")

            io.fase("buscando")
            io.estado("SEARCHING")
            await io.listo()
            await io.dormir(0.8)
            await io.iniciar()
            intentos += 1
            if intentos == 1:
                io.log("info", "el líder le dio a INICIAR")
            await _dormir_atento(io, sq, yo, A.REINTENTO_INICIAR)

            if io.ahora() - busca_desde >= A.REARMAR_SIN_PARTIDA:
                sin_partida = True
                break

        if sin_partida:
            if io.con_humano():
                sin_partida_humano += 1
            if registrar_fallo(sq, io.ahora(), io.modo_especial()):
                io.log("aviso", "el modo elegido no encuentra partida; juega Battle Royale "
                                "clásico un rato y después vuelve a probar")
            else:
                io.log("aviso", f"sin partida en {A.REARMAR_SIN_PARTIDA:.0f} s; arma un equipo nuevo")


async def ciclo_miembro(io, rearme=False):
    """El miembro: NO crea equipo ni inicia nada. Solo espera la invitación de su líder
    (si no le llega, entra con el código del equipo) y avisa "listo"."""
    A = Ajustes
    sq, yo = io.sq, str(io.uid)
    preparar(sq)
    await io.dormir(A.ESPERA_MIEMBRO_TRAS_PARTIDA if rearme else A.ESPERA_INICIAL_MIEMBRO)
    ult_listo = -1e9
    estado_puesto = None
    firma_antes = ()
    while io.activo():
        if io.pausado():
            return
        lid = str(sq.get("leader_uid") or "")
        peers = sq.get("peers") or {}
        if not lid or lid not in peers or io.esta_pausado(lid):
            nuevo, cambio = elegir_lider(sq, io.orden(), io.ahora(), io.esta_pausado)
            if cambio and nuevo:
                io.log("lider", f"el líder de la escuadra ahora es {nuevo}")
            lid = str(nuevo or "")
        if lid == yo:
            io.hacerse_lider()
            return

        # Respaldo: el líder lo invitó pero la invitación no llegó → entra con el código
        # del equipo, como haría una persona. Una sola vez por ronda.
        inv = sq["invitado"].get(yo)
        codigo = sq.get("team_code")
        if (inv and yo not in sq["en_equipo"] and codigo and inv[0] == sq["ronda"]
                and io.ahora() - float(inv[1]) >= A.ESPERA_ANTES_DE_CODIGO
                and sq["codigo_usado"].get(yo) != (sq["ronda"], codigo)):
            sq["codigo_usado"][yo] = (sq["ronda"], codigo)
            sq["invitado"].pop(yo, None)
            sq["en_equipo"][yo] = ("codigo", float(io.ahora()), sq["ronda"])
            io.log("info", f"a {io.nombre(yo)} no le llegó la invitación; entra con el código {codigo}")
            try:
                await io.unirse_con_codigo(codigo)
            except Exception as e:
                io.log("aviso", f"{io.nombre(yo)} no pudo entrar con el código: {e}")

        # Lo que muestra el panel
        dentro = yo in sq["en_equipo"]
        firma = tuple(sorted(sq["en_equipo"])) if dentro else ()
        if dentro and firma != firma_antes:
            # Recién entró al equipo, o entró otro compañero: avisa "listo" ya (si el juego
            # borra los "listo" cuando cambia el equipo, así el INICIAR del líder igual sirve)
            ult_listo = -1e9
        firma_antes = firma
        if dentro:
            nuevo_estado = "SEARCHING" if sq.get("fase") == "buscando" else "EN_EQUIPO"
        else:
            nuevo_estado = "ESPERA_LIDER"
        if nuevo_estado != estado_puesto:
            estado_puesto = nuevo_estado
            io.estado(nuevo_estado)

        if io.esperando_usuario():
            await io.dormir(1.0)
            continue
        # "Listo" siempre (como el bot original): estando solo el juego lo ignora, y si
        # está en el equipo sin que lo sepamos, igual cuenta para que el líder pueda iniciar.
        if io.ahora() - ult_listo >= A.LISTO_CADA:
            ult_listo = io.ahora()
            try:
                await io.listo()
            except Exception as e:
                io.log("aviso", f"error del miembro al avisar listo: {e}")
        await io.dormir(0.5)


def ids_en(obj, limite=2000):
    """Todos los números largos (posibles id de cuenta) que hay en un paquete decodificado."""
    salida = set()
    pila = [obj]
    vistos = 0
    while pila and vistos < limite:
        x = pila.pop()
        vistos += 1
        if isinstance(x, dict):
            pila.extend(x.values())
        elif isinstance(x, (list, tuple)):
            pila.extend(x)
        elif isinstance(x, bool):
            continue
        elif isinstance(x, int):
            if x >= 100000:
                salida.add(str(x))
        elif isinstance(x, str):
            s = x.strip()
            if s.isdigit() and 6 <= len(s) <= 15:
                salida.add(s)
    return salida
