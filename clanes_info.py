"""Información de los clanes (nombre, jugadores, honor) y honor ganado por cada bot/escuadra.

- traer_clan(): le pregunta al juego por el clan y sus miembros.
- El honor "ganado" se va sumando: cada vez que el honor de un bot sube, se anota la
  diferencia. Se guarda en honor_ganado.json para que no se pierda al reiniciar.
"""
import json
import os
import ssl
import time

import aiohttp
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

try:
    import infoClan_pb2
except Exception:  # sin el archivo no hay datos de clan, pero el bot sigue
    infoClan_pb2 = None

AES_KEY = bytes([89, 103, 38, 116, 99, 37, 68, 69, 117, 104, 54, 37, 90, 99, 94, 56])
AES_IV = bytes([54, 111, 121, 90, 68, 114, 50, 50, 69, 51, 121, 99, 104, 106, 77, 37])

ARCHIVO_HONOR = "honor_ganado.json"

# ---------- lectura de protobuf (a mano, sin esquema) ----------

def _varint(data, off):
    r = 0
    sh = 0
    while off < len(data):
        b = data[off]
        r |= (b & 0x7F) << sh
        off += 1
        if not (b & 0x80):
            return r, off
        sh += 7
    return None, off


def _leer(data):
    out = {}
    off = 0
    n = len(data)
    while off < n:
        h, off = _varint(data, off)
        if h is None:
            break
        fn, wt = h >> 3, h & 7
        if wt == 0:
            v, off = _varint(data, off)
            if v is not None:
                out[fn] = v
        elif wt == 2:
            ln, off = _varint(data, off)
            if ln is None:
                break
            val = data[off:off + ln]
            off += ln
            try:
                s = val.decode("utf-8")
                out[fn] = s if s else val.hex()
            except UnicodeDecodeError:
                sub = _leer(val)
                out[fn] = sub if sub else val.hex()
        elif wt == 1:
            off += 8
        elif wt == 5:
            off += 4
        else:
            break
    return out


def _repetidos(data):
    res = []
    off = 0
    n = len(data)
    while off < n:
        h, off = _varint(data, off)
        if h is None:
            break
        fn, wt = h >> 3, h & 7
        if wt == 2:
            ln, off = _varint(data, off)
            if ln is None:
                break
            val = data[off:off + ln]
            off += ln
            if fn == 1:
                res.append(_leer(val))
        elif wt == 0:
            _, off = _varint(data, off)
        elif wt == 1:
            off += 8
        elif wt == 5:
            off += 4
        else:
            break
    return res


def _pedido(clan_id):
    if infoClan_pb2 is None:
        return None
    m = infoClan_pb2.GuildReq()
    m.guildId = int(clan_id)
    m.garena = 1
    return AES.new(AES_KEY, AES.MODE_CBC, AES_IV).encrypt(pad(m.SerializeToString(), AES.block_size))


def _num(d, k):
    try:
        return int(d.get(k, 0) or 0)
    except Exception:
        return 0


async def traer_clan(token, clan_id, base_url, release_v):
    """Devuelve {"clan_id","clan_name","level","capacity","member_count","total_glory",
    "weekly_glory","members":[{uid,nickname,level,role,total,weekly}]} o None."""
    payload = _pedido(clan_id)
    if not payload or not token or not base_url:
        return None
    host = base_url.replace("https://", "").replace("http://", "").split("/")[0]
    hdr = {
        "User-Agent": "UnityPlayer/2022.3.47f1 (UnityWebRequest/1.0, libcurl/8.5.0-DEV)",
        "Accept": "*/*", "Accept-Encoding": "deflate, gzip",
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/x-www-form-urlencoded",
        "X-GA": "v1 1", "X-GA-SV": str(int(time.time())),
        "ReleaseVersion": str(release_v or ""),
        "X-Unity-Version": "2022.3.47f1", "Host": host,
    }
    hdr_m = {**hdr, "User-Agent": "UnityPlayer/2018.4.12f1 (UnityWebRequest/1.0, libcurl/8.5.0-DEV)",
             "X-Unity-Version": "2018.4.12f1"}
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    base_url = base_url.rstrip("/")
    info = {}
    miembros = []
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=12)) as s:
            async with s.post(f"{base_url}/GetClanMainPageInfo", headers=hdr, data=payload, ssl=ctx) as r:
                if r.status == 200:
                    b = await r.read()
                    if b and len(b) >= 10:
                        g = _leer(b).get(1, {})
                        if isinstance(g, dict):
                            info = {
                                "clan_id": _num(g, 1) or int(clan_id),
                                "clan_name": str(g.get(2, "") or ""),
                                "level": _num(g, 5), "capacity": _num(g, 6),
                                "member_count": _num(g, 7), "region": str(g.get(13, "") or ""),
                                "total_glory": _num(g, 36), "weekly_glory": _num(g, 37),
                            }
            async with s.post(f"{base_url}/GetClanMembers", headers=hdr_m, data=payload, ssl=ctx) as r:
                if r.status == 200:
                    b = await r.read()
                    if b and len(b) >= 10:
                        for m in _repetidos(b):
                            basic = m.get(1, {}) if isinstance(m.get(1), dict) else {}
                            uid = _num(basic, 1)
                            if not uid:
                                continue
                            a, c = _num(m, 10), _num(m, 11)
                            miembros.append({
                                "uid": str(uid), "nickname": str(basic.get(3, "") or ""),
                                "level": _num(basic, 6),
                                "role": {3: "leader", 2: "co-leader"}.get(_num(m, 4), "member"),
                                # el total siempre es el mayor de los dos
                                "total": max(a, c), "weekly": min(a, c),
                            })
    except Exception:
        return None
    if not info and not miembros:
        return None
    info.setdefault("clan_id", int(clan_id))
    info.setdefault("clan_name", "")
    info.setdefault("level", 0)
    info.setdefault("capacity", 0)
    info.setdefault("total_glory", 0)
    info.setdefault("weekly_glory", 0)
    info["member_count"] = len(miembros) or info.get("member_count", 0)
    info["members"] = miembros
    info["leida"] = time.time()
    return info


# ---------- honor ganado por bot ----------

_ganado = {}   # uid del bot -> {"ult": último total visto, "acum": honor ganado, "desde": cuándo}
_cargado = False


def _cargar():
    global _cargado, _ganado
    if _cargado:
        return
    _cargado = True
    try:
        if os.path.exists(ARCHIVO_HONOR):
            with open(ARCHIVO_HONOR, "r", encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict) and isinstance(d.get("bots"), dict):
                _ganado = d["bots"]
    except Exception:
        _ganado = {}


def _guardar():
    try:
        tmp = ARCHIVO_HONOR + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"bots": _ganado}, f)
        os.replace(tmp, ARCHIVO_HONOR)
    except Exception:
        pass


def anotar(miembros, uids_bots):
    """Suma lo que subió el honor de cada bot desde la última lectura."""
    _cargar()
    ahora = time.time()
    cambio = False
    por_uid = {m["uid"]: m for m in miembros}
    for u in uids_bots:
        m = por_uid.get(str(u))
        if not m:
            continue
        tot = int(m["total"])
        d = _ganado.get(str(u))
        if d is None:
            _ganado[str(u)] = {"ult": tot, "acum": 0, "desde": ahora}
            cambio = True
            continue
        if tot > d["ult"]:
            d["acum"] += tot - d["ult"]
            d["ult"] = tot
            cambio = True
        elif tot < d["ult"]:      # cambió de clan o se reinició: se toma como nuevo punto de partida
            d["ult"] = tot
            cambio = True
    if cambio:
        _guardar()


def ganado_de(uid):
    _cargar()
    return int((_ganado.get(str(uid)) or {}).get("acum", 0))


def desde_de(uid):
    _cargar()
    return float((_ganado.get(str(uid)) or {}).get("desde", 0))
