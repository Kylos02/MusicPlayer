import os
import re
import time
import base64
import json
import threading
import urllib.request
import urllib.parse
import digitalio
import board
from PIL import Image, ImageDraw, ImageFont
import adafruit_rgb_display.ili9341 as ili9341
import RPi.GPIO as GPIO
import xpt2046_circuitpython

# ==========================================
# 1. CONFIGURACIÓN GENERAL
# ==========================================
PIPE_PATH = "/tmp/shairport-sync-metadata"
COVER_DIR = "/tmp/shairport-sync/.cache/coverart"
SAMPLE_RATE = 44100.0

ANCHO_PANTALLA = 240
ALTO_PANTALLA = 320
COVER_SIZE = 240
INFO_Y_START = COVER_SIZE
MARGEN = 10

COLOR_FONDO = (0, 0, 0)
COLOR_TITULO = (255, 255, 255)
COLOR_ARTISTA = (170, 170, 170)
COLOR_ALBUM = (120, 120, 120)
COLOR_TIEMPO = (170, 170, 170)
COLOR_FONDO_BARRA = (60, 60, 60)
COLOR_PROGRESO = (255, 255, 255)

# Transición slide
SLIDE_DURACION = 0.5

# Volumen overlay
VOL_VISIBLE_SEG = 1.5
VOL_FADEOUT_SEG = 0.5
VOL_TOTAL_SEG = VOL_VISIBLE_SEG + VOL_FADEOUT_SEG

# Letras
LRCLIB_URL = "https://lrclib.net/api/get"
LINEA_ALTO = 26
LETRAS_VISIBLES = 9

# Fuentes
try:
    fuente_titulo = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 14)
    fuente_artista = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 12)
    fuente_album = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 10)
    fuente_tiempo = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 10)
    fuente_volumen = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 13)
    fuente_letra_info = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 10)

    # Fuentes para letras: tamaños escalonados para auto-ajuste
    FUENTES_LETRA_BOLD = [
        ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", s)
        for s in [14, 12, 10, 9, 8, 7]
    ]
    FUENTES_LETRA_NORMAL = [
        ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", s)
        for s in [12, 11, 10, 9, 8, 7]
    ]
except IOError:
    _def = ImageFont.load_default()
    fuente_titulo = _def
    fuente_artista = _def
    fuente_album = _def
    fuente_tiempo = _def
    fuente_volumen = _def
    fuente_letra_info = _def
    FUENTES_LETRA_BOLD = [_def]
    FUENTES_LETRA_NORMAL = [_def]


# ==========================================
# 2. FUNCIONES DE COLOR
# ==========================================

def extraer_color_dominante(imagen):
    try:
        pequena = imagen.resize((1, 1), Image.LANCZOS)
        r, g, b = pequena.getpixel((0, 0))
        factor = 0.35
        return (int(r * factor), int(g * factor), int(b * factor))
    except Exception:
        return (20, 20, 20)


def luminancia(color):
    r, g, b = color
    return (0.299 * r + 0.587 * g + 0.114 * b) / 255.0


def mezclar_color(c1, c2, t):
    return (
        int(c1[0] + (c2[0] - c1[0]) * t),
        int(c1[1] + (c2[1] - c1[1]) * t),
        int(c1[2] + (c2[2] - c1[2]) * t),
    )


def generar_colores_letras(color_fondo):
    lum = luminancia(color_fondo)
    fr, fg, fb = color_fondo

    if lum > 0.45:
        return {
            "actual":    (0, 0, 0),
            "cerca_1":   (40, 40, 40),
            "cerca_2":   (80, 80, 80),
            "lejos":     mezclar_color((fr, fg, fb), (40, 40, 40), 0.5),
            "muy_lejos": mezclar_color((fr, fg, fb), (60, 60, 60), 0.6),
            "info":      mezclar_color((fr, fg, fb), (50, 50, 50), 0.5),
            "separador": mezclar_color((fr, fg, fb), (0, 0, 0), 0.3),
            "barra_bg":  mezclar_color((fr, fg, fb), (0, 0, 0), 0.25),
            "barra_fg":  (0, 0, 0),
            "tiempo":    (50, 50, 50),
        }
    else:
        return {
            "actual":    (255, 255, 255),
            "cerca_1":   (200, 200, 200),
            "cerca_2":   (150, 150, 150),
            "lejos":     mezclar_color((fr, fg, fb), (180, 180, 180), 0.35),
            "muy_lejos": mezclar_color((fr, fg, fb), (120, 120, 120), 0.4),
            "info":      mezclar_color((fr, fg, fb), (200, 200, 200), 0.4),
            "separador": mezclar_color((fr, fg, fb), (255, 255, 255), 0.15),
            "barra_bg":  mezclar_color((fr, fg, fb), (255, 255, 255), 0.2),
            "barra_fg":  (255, 255, 255),
            "tiempo":    mezclar_color((fr, fg, fb), (220, 220, 220), 0.4),
        }


# ==========================================
# 3. ESTADO DEL REPRODUCTOR (thread-safe)
# ==========================================

class EstadoReproductor:
    def __init__(self):
        self.lock = threading.Lock()
        self.titulo = ""
        self.artista = ""
        self.album = ""
        self.duracion_seg = None
        self.posicion_seg = None
        self.timestamp_posicion = None
        self.esta_pausado = False
        self.volumen_pct = 50.0
        self.timestamp_volumen = 0
        self.letras_sync = []
        self.letras_estado = "idle"
        self.hubo_cambio_cancion = False

    def obtener_posicion_actual(self):
        with self.lock:
            if self.posicion_seg is None or self.timestamp_posicion is None:
                return 0.0
            if self.esta_pausado:
                return self.posicion_seg
            transcurrido = time.time() - self.timestamp_posicion
            pos = self.posicion_seg + transcurrido
            if self.duracion_seg and pos > self.duracion_seg:
                return self.duracion_seg
            return max(0.0, pos)

    def obtener_duracion(self):
        with self.lock:
            return self.duracion_seg

    def obtener_metadata(self):
        with self.lock:
            cambio = self.hubo_cambio_cancion
            self.hubo_cambio_cancion = False
            return self.titulo, self.artista, self.album, cambio

    def obtener_volumen(self):
        with self.lock:
            return self.volumen_pct, self.timestamp_volumen

    def obtener_letras(self):
        with self.lock:
            return list(self.letras_sync), self.letras_estado


# ==========================================
# 4. LECTOR DEL PIPE DE METADATA
# ==========================================

def hex_a_ascii(hex_str):
    try:
        return bytes.fromhex(hex_str.strip()).decode("ascii", errors="replace")
    except (ValueError, UnicodeDecodeError):
        return ""


def procesar_item(estado, xml_str):
    try:
        match_type = re.search(r"<type>([0-9a-fA-F]+)</type>", xml_str)
        match_code = re.search(r"<code>([0-9a-fA-F]+)</code>", xml_str)
        match_data = re.search(r"<data[^>]*>(.*?)</data>", xml_str, re.DOTALL)

        if not match_type or not match_code:
            return

        tipo = hex_a_ascii(match_type.group(1))
        code = hex_a_ascii(match_code.group(1))

        data = ""
        if match_data:
            b64 = match_data.group(1).strip()
            if b64:
                try:
                    data = base64.b64decode(b64).decode("utf-8", errors="replace")
                except Exception:
                    data = ""

        ahora = time.time()

        with estado.lock:
            if tipo == "core":
                if code == "minm":
                    if data != estado.titulo:
                        estado.titulo = data
                        estado.hubo_cambio_cancion = True
                elif code == "asar":
                    estado.artista = data
                elif code == "asal":
                    estado.album = data

            elif tipo == "ssnc":
                if code == "prgr":
                    partes = data.split("/")
                    if len(partes) == 3:
                        try:
                            rtp_start = int(partes[0])
                            rtp_current = int(partes[1])
                            rtp_end = int(partes[2])
                            estado.duracion_seg = max(0.0, (rtp_end - rtp_start) / SAMPLE_RATE)
                            estado.posicion_seg = max(0.0, (rtp_current - rtp_start) / SAMPLE_RATE)
                            estado.timestamp_posicion = ahora
                        except ValueError:
                            pass

                elif code == "pfls":
                    if not estado.esta_pausado and estado.posicion_seg is not None and estado.timestamp_posicion is not None:
                        transcurrido = ahora - estado.timestamp_posicion
                        estado.posicion_seg += transcurrido
                        estado.timestamp_posicion = ahora
                    estado.esta_pausado = True

                elif code == "prsm":
                    estado.esta_pausado = False
                    estado.timestamp_posicion = ahora

                elif code == "pend":
                    estado.esta_pausado = True

                elif code == "pbeg":
                    estado.esta_pausado = False

                elif code == "pvol":
                    try:
                        partes_vol = data.split(",")
                        airplay_vol = float(partes_vol[0])
                        if airplay_vol <= -144.0:
                            estado.volumen_pct = 0.0
                        else:
                            estado.volumen_pct = max(0.0, min(100.0, ((airplay_vol + 30.0) / 30.0) * 100.0))
                        estado.timestamp_volumen = ahora
                    except (ValueError, IndexError):
                        pass

    except Exception:
        pass


def hilo_lector_pipe(estado):
    print("📡 Conectando al pipe de metadata...")
    while True:
        try:
            with open(PIPE_PATH, "r") as pipe:
                print("📡 Pipe conectado.")
                buffer = ""
                for linea in pipe:
                    buffer += linea
                    while "</item>" in buffer:
                        idx = buffer.index("</item>") + len("</item>")
                        item_xml = buffer[:idx]
                        buffer = buffer[idx:]
                        procesar_item(estado, item_xml)
        except Exception as e:
            print(f"⚠️  Error en pipe: {e}. Reconectando en 2s...")
            time.sleep(2)


# ==========================================
# 5. LETRAS SINCRONIZADAS (LRCLIB)
# ==========================================

def parsear_lrc(lrc_text):
    lineas = []
    for linea in lrc_text.strip().split("\n"):
        match = re.match(r"\[(\d+):(\d+(?:\.\d+)?)\]\s*(.*)", linea)
        if match:
            mins = int(match.group(1))
            secs = float(match.group(2))
            texto = match.group(3).strip()
            if texto:
                lineas.append((mins * 60 + secs, texto))
    lineas.sort(key=lambda x: x[0])
    return lineas


def buscar_letras(estado, titulo, artista):
    with estado.lock:
        estado.letras_estado = "cargando"
        estado.letras_sync = []

    print(f"🔍 Buscando letras: {titulo} - {artista}")

    try:
        params = urllib.parse.urlencode({
            "artist_name": artista,
            "track_name": titulo,
        })
        url = f"{LRCLIB_URL}?{params}"
        req = urllib.request.Request(url, headers={"User-Agent": "RaspberryMusicPlayer/1.0"})

        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode())

        synced = data.get("syncedLyrics", "")
        plain = data.get("plainLyrics", "")

        if synced:
            parsed = parsear_lrc(synced)
            if parsed:
                with estado.lock:
                    estado.letras_sync = parsed
                    estado.letras_estado = "encontradas"
                print(f"✅ Letras sincronizadas ({len(parsed)} líneas)")
                return

        if plain:
            lineas = [l.strip() for l in plain.split("\n") if l.strip()]
            if lineas:
                with estado.lock:
                    estado.letras_sync = [(0, l) for l in lineas]
                    estado.letras_estado = "solo_texto"
                print(f"📝 Letras sin sync ({len(lineas)} líneas)")
                return

        with estado.lock:
            estado.letras_estado = "no_encontradas"
        print("❌ Letras no encontradas")

    except Exception as e:
        print(f"❌ Error buscando letras: {e}")
        with estado.lock:
            estado.letras_estado = "no_encontradas"


def iniciar_busqueda_letras(estado, titulo, artista):
    hilo = threading.Thread(target=buscar_letras, args=(estado, titulo, artista), daemon=True)
    hilo.start()


# ==========================================
# 6. BOTONES + TOUCH
# ==========================================
GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)

BTN_K1_PREV = 18
BTN_K2_PAUSA = 23
BTN_K3_NEXT = 24

GPIO.setup(BTN_K1_PREV, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.setup(BTN_K2_PAUSA, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.setup(BTN_K3_NEXT, GPIO.IN, pull_up_down=GPIO.PUD_UP)

# Variable global para el modo
modo_letras = False


def control_musica(canal):
    if canal == BTN_K1_PREV:
        os.system(
            "dbus-send --system --type=method_call "
            "--dest=org.mpris.MediaPlayer2.ShairportSync "
            "/org/mpris/MediaPlayer2 org.mpris.MediaPlayer2.Player.Previous &"
        )
    elif canal == BTN_K2_PAUSA:
        os.system(
            "dbus-send --system --type=method_call "
            "--dest=org.mpris.MediaPlayer2.ShairportSync "
            "/org/mpris/MediaPlayer2 org.mpris.MediaPlayer2.Player.PlayPause &"
        )
    elif canal == BTN_K3_NEXT:
        os.system(
            "dbus-send --system --type=method_call "
            "--dest=org.mpris.MediaPlayer2.ShairportSync "
            "/org/mpris/MediaPlayer2 org.mpris.MediaPlayer2.Player.Next &"
        )


GPIO.add_event_detect(BTN_K1_PREV, GPIO.FALLING, callback=control_musica, bouncetime=300)
GPIO.add_event_detect(BTN_K2_PAUSA, GPIO.FALLING, callback=control_musica, bouncetime=300)
GPIO.add_event_detect(BTN_K3_NEXT, GPIO.FALLING, callback=control_musica, bouncetime=300)


# ==========================================
# 7. PANTALLA SPI
# ==========================================
cs_pin = digitalio.DigitalInOut(board.CE0)
dc_pin = digitalio.DigitalInOut(board.D22)
reset_pin = digitalio.DigitalInOut(board.D27)
spi = board.SPI()

disp = ili9341.ILI9341(
    spi,
    rotation=0,
    cs=cs_pin,
    dc=dc_pin,
    rst=reset_pin,
    baudrate=40000000,
)

# Touch XPT2046 (usa el mismo bus SPI, se lee DESPUÉS de actualizar pantalla)
cs_touch = digitalio.DigitalInOut(board.CE1)
irq_touch = digitalio.DigitalInOut(board.D17)
touch = xpt2046_circuitpython.Touch(
    spi,
    cs=cs_touch,
    interrupt=irq_touch,
    force_baudrate=4000000,
)
print("👆 Touch configurado (lápiz táctil, CE1/D17)")

# Touch state
TOUCH_DEBOUNCE = 0.8
touch_previo = False
ultimo_touch = 0.0


# ==========================================
# 8. FUNCIONES DE DIBUJO
# ==========================================

def formato_tiempo(segundos):
    if segundos is None or segundos < 0:
        return "--:--"
    s = int(segundos)
    return f"{s // 60}:{s % 60:02d}"


def calcular_offset_scroll(texto_ancho, area_ancho, tiempo_transcurrido):
    exceso = texto_ancho - area_ancho
    if exceso <= 0:
        return 0
    velocidad = 30.0
    pausa = 2.0
    tiempo_scroll = exceso / velocidad
    ciclo = pausa + tiempo_scroll + pausa + tiempo_scroll
    t = tiempo_transcurrido % ciclo
    if t < pausa:
        return 0
    elif t < pausa + tiempo_scroll:
        return int(exceso * ((t - pausa) / tiempo_scroll))
    elif t < pausa + tiempo_scroll + pausa:
        return exceso
    else:
        return int(exceso * (1.0 - (t - pausa - tiempo_scroll - pausa) / tiempo_scroll))


def medir_texto(draw, texto, fuente):
    bbox = draw.textbbox((0, 0), texto, font=fuente)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def truncar_texto(draw, texto, fuente, ancho_max):
    """Trunca texto con '...' si excede el ancho máximo."""
    tw, _ = medir_texto(draw, texto, fuente)
    if tw <= ancho_max:
        return texto
    while len(texto) > 1:
        texto = texto[:-1]
        tw, _ = medir_texto(draw, texto.rstrip() + "…", fuente)
        if tw <= ancho_max:
            return texto.rstrip() + "…"
    return "…"


def dibujar_texto_scroll(lienzo, texto, fuente, color, y, tiempo_scroll, color_fondo_scroll=COLOR_FONDO):
    draw = ImageDraw.Draw(lienzo)
    area_ancho = ANCHO_PANTALLA - MARGEN * 2
    texto_ancho, texto_alto = medir_texto(draw, texto, fuente)
    if texto_ancho <= area_ancho:
        x = (ANCHO_PANTALLA - texto_ancho) // 2
        draw.text((x, y), texto, fill=color, font=fuente)
    else:
        offset = calcular_offset_scroll(texto_ancho, area_ancho, tiempo_scroll)
        img_texto = Image.new("RGB", (texto_ancho + 20, texto_alto + 4), color_fondo_scroll)
        draw_t = ImageDraw.Draw(img_texto)
        draw_t.text((0, 0), texto, fill=color, font=fuente)
        ventana = img_texto.crop((offset, 0, offset + area_ancho, texto_alto + 4))
        lienzo.paste(ventana, (MARGEN, y))


def dibujar_barra_progreso(lienzo, posicion_seg, duracion_seg, y_barra,
                           color_barra_bg=COLOR_FONDO_BARRA, color_barra_fg=COLOR_PROGRESO,
                           color_txt=COLOR_TIEMPO):
    draw = ImageDraw.Draw(lienzo)
    barra_h = 4
    barra_x0 = MARGEN
    barra_x1 = ANCHO_PANTALLA - MARGEN

    draw.rectangle([barra_x0, y_barra, barra_x1, y_barra + barra_h], fill=color_barra_bg)

    if duracion_seg and duracion_seg > 0 and posicion_seg is not None:
        progreso = max(0.0, min(1.0, posicion_seg / duracion_seg))
    else:
        progreso = 0.0

    largo = int((barra_x1 - barra_x0) * progreso)
    if largo > 0:
        draw.rectangle([barra_x0, y_barra, barra_x0 + largo, y_barra + barra_h], fill=color_barra_fg)

    tiempo_y = y_barra + barra_h + 3
    draw.text((barra_x0, tiempo_y), formato_tiempo(posicion_seg), fill=color_txt, font=fuente_tiempo)
    txt_total = formato_tiempo(duracion_seg)
    ancho_tt, _ = medir_texto(draw, txt_total, fuente_tiempo)
    draw.text((barra_x1 - ancho_tt, tiempo_y), txt_total, fill=color_txt, font=fuente_tiempo)


# --- MODO CARÁTULA ---

def dibujar_info_cover(lienzo, titulo, artista, album, posicion_seg, duracion_seg,
                       tiempo_scroll, colores, color_fondo_c):
    titulo_y = INFO_Y_START + 2
    dibujar_texto_scroll(lienzo, titulo if titulo else "Sin título",
                         fuente_titulo, colores["actual"], titulo_y, tiempo_scroll, color_fondo_c)

    artista_y = titulo_y + 18
    dibujar_texto_scroll(lienzo, artista if artista else "Artista desconocido",
                         fuente_artista, colores["cerca_1"], artista_y, tiempo_scroll, color_fondo_c)

    album_y = artista_y + 16
    if album:
        dibujar_texto_scroll(lienzo, album, fuente_album, colores["cerca_2"], album_y, tiempo_scroll, color_fondo_c)

    barra_y = album_y + 15
    dibujar_barra_progreso(lienzo, posicion_seg, duracion_seg, barra_y,
                           colores["barra_bg"], colores["barra_fg"], colores["tiempo"])


# --- MODO LETRAS ---

def encontrar_linea_actual(letras_sync, posicion_seg):
    idx = 0
    for i, (ts, _) in enumerate(letras_sync):
        if posicion_seg >= ts:
            idx = i
        else:
            break
    return idx


def dibujar_vista_letras(lienzo, titulo, artista, letras_sync, letras_estado,
                         posicion_seg, duracion_seg, color_fondo_l, colores, ahora):
    draw = ImageDraw.Draw(lienzo)
    area_ancho = ANCHO_PANTALLA - MARGEN * 2

    # --- Header compacto ---
    header_texto = titulo if titulo else "Sin título"
    if artista:
        header_texto += f"  •  {artista}"
    header_texto = truncar_texto(draw, header_texto, fuente_letra_info, area_ancho)
    hw, _ = medir_texto(draw, header_texto, fuente_letra_info)
    draw.text(((ANCHO_PANTALLA - hw) // 2, 6), header_texto,
              fill=colores["info"], font=fuente_letra_info)

    # Separador
    draw.line([(MARGEN, 22), (ANCHO_PANTALLA - MARGEN, 22)],
              fill=colores["separador"], width=1)

    # --- Zona de letras ---
    letras_y_inicio = 28
    letras_y_fin = 282
    letras_altura = letras_y_fin - letras_y_inicio

    if letras_estado == "cargando":
        msg = "Buscando letras..."
        mw, _ = medir_texto(draw, msg, fuente_artista)
        draw.text(((ANCHO_PANTALLA - mw) // 2, letras_y_inicio + letras_altura // 2 - 8),
                  msg, fill=colores["cerca_1"], font=fuente_artista)

    elif letras_estado == "no_encontradas":
        msg = "Letras no disponibles"
        mw, _ = medir_texto(draw, msg, fuente_artista)
        draw.text(((ANCHO_PANTALLA - mw) // 2, letras_y_inicio + letras_altura // 2 - 8),
                  msg, fill=colores["lejos"], font=fuente_artista)

    elif letras_estado == "solo_texto" and letras_sync:
        total = len(letras_sync)
        if duracion_seg and duracion_seg > 0 and posicion_seg is not None:
            idx_estimado = int((posicion_seg / duracion_seg) * total)
        else:
            idx_estimado = 0
        idx_estimado = max(0, min(total - 1, idx_estimado))
        _dibujar_lineas_letras(draw, lienzo, letras_sync, idx_estimado,
                               letras_y_inicio, letras_altura, color_fondo_l, colores, ahora)

    elif letras_estado == "encontradas" and letras_sync:
        idx_actual = encontrar_linea_actual(letras_sync, posicion_seg or 0)
        _dibujar_lineas_letras(draw, lienzo, letras_sync, idx_actual,
                               letras_y_inicio, letras_altura, color_fondo_l, colores, ahora)

    # Separador inferior
    draw.line([(MARGEN, letras_y_fin), (ANCHO_PANTALLA - MARGEN, letras_y_fin)],
              fill=colores["separador"], width=1)

    # --- Barra de progreso ---
    dibujar_barra_progreso(lienzo, posicion_seg, duracion_seg, letras_y_fin + 6,
                           colores["barra_bg"], colores["barra_fg"], colores["tiempo"])


def elegir_fuente(draw, texto, fuentes, ancho_max):
    """Elige la fuente más grande que haga caber el texto en ancho_max."""
    for fuente in fuentes:
        tw, _ = medir_texto(draw, texto, fuente)
        if tw <= ancho_max:
            return fuente, tw
    # Ninguna cabe: usar la más pequeña
    tw, _ = medir_texto(draw, texto, fuentes[-1])
    return fuentes[-1], tw


def _dibujar_lineas_letras(draw, lienzo, letras_sync, idx_actual,
                           y_inicio, altura_total, color_fondo_l, colores, ahora):
    """
    Dibuja líneas de letras con gradiente.
    El tamaño de fuente se ajusta automáticamente para que el texto quepa.
    """
    total = len(letras_sync)
    centro_y = y_inicio + altura_total // 2
    mitad = LETRAS_VISIBLES // 2
    area_ancho = ANCHO_PANTALLA - MARGEN * 2

    color_map = {
        0: colores["actual"],
        1: colores["cerca_1"],
        2: colores["cerca_2"],
    }

    for offset in range(-mitad, mitad + 1):
        idx = idx_actual + offset
        if idx < 0 or idx >= total:
            continue

        _, texto = letras_sync[idx]
        distancia = abs(offset)

        if distancia <= 2:
            color = color_map.get(distancia, colores["lejos"])
        elif distancia <= 4:
            color = colores["lejos"]
        else:
            color = colores["muy_lejos"]

        y = centro_y + offset * LINEA_ALTO - LINEA_ALTO // 2

        if y < y_inicio - 2 or y + LINEA_ALTO > y_inicio + altura_total + 2:
            continue

        # Elegir fuente que haga caber el texto
        if offset == 0:
            fuente, tw = elegir_fuente(draw, texto, FUENTES_LETRA_BOLD, area_ancho)
        else:
            fuente, tw = elegir_fuente(draw, texto, FUENTES_LETRA_NORMAL, area_ancho)

        # Centrar horizontalmente
        tx = (ANCHO_PANTALLA - tw) // 2
        draw.text((tx, y), texto, fill=color, font=fuente)


# --- VOLUMEN OVERLAY ---

def dibujar_volumen(lienzo, volumen_pct, tiempo_desde_cambio):
    if tiempo_desde_cambio > VOL_TOTAL_SEG:
        return

    if tiempo_desde_cambio <= VOL_VISIBLE_SEG:
        opacidad = 1.0
    else:
        opacidad = 1.0 - (tiempo_desde_cambio - VOL_VISIBLE_SEG) / VOL_FADEOUT_SEG

    ov_ancho = 180
    ov_alto = 36
    ov_x = (ANCHO_PANTALLA - ov_ancho) // 2
    ov_y = 100

    overlay = Image.new("RGB", (ov_ancho, ov_alto), (30, 30, 30))
    draw_ov = ImageDraw.Draw(overlay)

    vol_texto = f"Vol  {int(volumen_pct)}%"
    tw, _ = medir_texto(draw_ov, vol_texto, fuente_volumen)
    draw_ov.text(((ov_ancho - tw) // 2, 2), vol_texto, fill=(255, 255, 255), font=fuente_volumen)

    barra_m = 12
    barra_y = 22
    barra_h = 6
    barra_x1 = ov_ancho - barra_m

    draw_ov.rectangle([barra_m, barra_y, barra_x1, barra_y + barra_h], fill=(80, 80, 80))
    fill_w = int((barra_x1 - barra_m) * volumen_pct / 100.0)
    if fill_w > 0:
        draw_ov.rectangle([barra_m, barra_y, barra_m + fill_w, barra_y + barra_h], fill=COLOR_PROGRESO)

    region = lienzo.crop((ov_x, ov_y, ov_x + ov_ancho, ov_y + ov_alto))
    mezclado = Image.blend(region, overlay, opacidad * 0.85)
    lienzo.paste(mezclado, (ov_x, ov_y))


# ==========================================
# 9. BUCLE PRINCIPAL
# ==========================================

estado = EstadoReproductor()

hilo_pipe = threading.Thread(target=hilo_lector_pipe, args=(estado,), daemon=True)
hilo_pipe.start()

# Estado pantalla
last_modified = 0
imagen_caratula = None
imagen_caratula_nueva = None
imagen_caratula_vieja = None
slide_inicio = None

tiempo_inicio_scroll = time.time()
titulo_mostrado = ""
artista_mostrado = ""
album_mostrado = ""

ultimo_track_letras = ""

# Color dominante de la carátula para fondo de letras
color_fondo_letras = (20, 20, 20)
colores_letras = generar_colores_letras(color_fondo_letras)

print("🚀 Sistema Listo. Esperando música desde AirPlay...")
print("👆 Toca la pantalla para alternar entre carátula y letras")

try:
    while True:
        ahora = time.time()

        # --- Verificar nueva carátula ---
        caratula_cambio = False
        if os.path.exists(COVER_DIR):
            archivos = [
                os.path.join(COVER_DIR, f)
                for f in os.listdir(COVER_DIR)
                if os.path.isfile(os.path.join(COVER_DIR, f))
            ]
            if archivos:
                archivo_mas_reciente = max(archivos, key=os.path.getmtime)
                tiempo_modificacion = os.path.getmtime(archivo_mas_reciente)

                if tiempo_modificacion > last_modified:
                    try:
                        image = Image.open(archivo_mas_reciente)
                        if image.mode != "RGB":
                            image = image.convert("RGB")
                        nueva_cover = image.resize((COVER_SIZE, COVER_SIZE))
                        last_modified = tiempo_modificacion
                        caratula_cambio = True

                        # Extraer color dominante
                        color_fondo_letras = extraer_color_dominante(nueva_cover)
                        colores_letras = generar_colores_letras(color_fondo_letras)

                        if imagen_caratula is not None:
                            imagen_caratula_vieja = imagen_caratula.copy()
                            imagen_caratula_nueva = nueva_cover
                            slide_inicio = ahora
                        else:
                            imagen_caratula = nueva_cover

                    except Exception as e:
                        print(f"Error al abrir carátula: {e}")

                    for f in archivos:
                        if f != archivo_mas_reciente:
                            try:
                                os.remove(f)
                            except OSError:
                                pass

        # --- Slide ---
        hay_slide = False
        if slide_inicio is not None:
            t_slide = (ahora - slide_inicio) / SLIDE_DURACION
            if t_slide >= 1.0:
                imagen_caratula = imagen_caratula_nueva
                imagen_caratula_vieja = None
                imagen_caratula_nueva = None
                slide_inicio = None
            else:
                hay_slide = True

        # --- Metadata ---
        titulo, artista, album, cambio_cancion = estado.obtener_metadata()

        if cambio_cancion or caratula_cambio:
            tiempo_inicio_scroll = ahora
            if titulo:
                titulo_mostrado = titulo
            if artista:
                artista_mostrado = artista
            if album:
                album_mostrado = album
            print(f"🎵 {titulo_mostrado} - {artista_mostrado} ({album_mostrado})")

            track_key = f"{titulo_mostrado}|{artista_mostrado}"
            if track_key != ultimo_track_letras and titulo_mostrado:
                ultimo_track_letras = track_key
                iniciar_busqueda_letras(estado, titulo_mostrado, artista_mostrado)
        else:
            if titulo and titulo != titulo_mostrado:
                titulo_mostrado = titulo
                tiempo_inicio_scroll = ahora
            if artista and artista != artista_mostrado:
                artista_mostrado = artista
            if album and album != album_mostrado:
                album_mostrado = album

        # --- Posición y duración ---
        posicion_seg = estado.obtener_posicion_actual()
        duracion_seg = estado.obtener_duracion()

        # --- Volumen ---
        volumen_pct, ts_vol = estado.obtener_volumen()
        tiempo_desde_vol = ahora - ts_vol

        # --- Construir frame ---
        if imagen_caratula is not None or modo_letras:

            if modo_letras:
                # === MODO LETRAS ===
                lienzo = Image.new("RGB", (ANCHO_PANTALLA, ALTO_PANTALLA), color_fondo_letras)

                letras_sync, letras_estado = estado.obtener_letras()
                dibujar_vista_letras(
                    lienzo,
                    titulo_mostrado, artista_mostrado,
                    letras_sync, letras_estado,
                    posicion_seg, duracion_seg,
                    color_fondo_letras, colores_letras, ahora,
                )
            else:
                # === MODO CARÁTULA: fondo con color dominante ===
                lienzo = Image.new("RGB", (ANCHO_PANTALLA, ALTO_PANTALLA), color_fondo_letras)

                if hay_slide:
                    t_slide = (ahora - slide_inicio) / SLIDE_DURACION
                    t_ease = 1.0 - (1.0 - t_slide) ** 2
                    offset = int(COVER_SIZE * t_ease)

                    if imagen_caratula_vieja is not None:
                        vieja_x = -offset
                        if vieja_x > -COVER_SIZE:
                            lienzo.paste(imagen_caratula_vieja, (vieja_x, 0))

                    if imagen_caratula_nueva is not None:
                        nueva_x = COVER_SIZE - offset
                        if nueva_x < COVER_SIZE:
                            lienzo.paste(imagen_caratula_nueva, (nueva_x, 0))
                else:
                    if imagen_caratula is not None:
                        lienzo.paste(imagen_caratula, (0, 0))

                tiempo_scroll = ahora - tiempo_inicio_scroll
                dibujar_info_cover(
                    lienzo,
                    titulo_mostrado, artista_mostrado, album_mostrado,
                    posicion_seg, duracion_seg,
                    tiempo_scroll, colores_letras, color_fondo_letras,
                )

            # Volumen overlay
            if tiempo_desde_vol <= VOL_TOTAL_SEG:
                dibujar_volumen(lienzo, volumen_pct, tiempo_desde_vol)

            disp.image(lienzo.convert("RGB"))

            # --- Touch: leer DESPUÉS de actualizar pantalla (SPI libre) ---
            # Leer coordenadas reales valida que es un toque del lápiz,
            # no ruido del SPI del display.
            try:
                tocado = touch.is_pressed()
                if tocado and not touch_previo:
                    # Flanco: no tocado → tocado
                    # Intentar leer coordenadas para confirmar toque real
                    try:
                        x_t, y_t = touch.get_coordinates()
                        # Si get_coordinates funciona sin error, es toque real
                        if ahora - ultimo_touch > TOUCH_DEBOUNCE:
                            modo_letras = not modo_letras
                            ultimo_touch = ahora
                            print(f"👆 Modo: {'Letras' if modo_letras else 'Carátula'} (x={x_t}, y={y_t})")
                    except Exception:
                        pass  # Coordenadas inválidas = no es toque real
                touch_previo = tocado
            except Exception:
                touch_previo = False

        time.sleep(0.25)

except KeyboardInterrupt:
    print("\nApagando sistema y liberando pines...")
    GPIO.cleanup()
