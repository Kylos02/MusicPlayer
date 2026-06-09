import os
import io
import re
import ssl
import time
import base64
import json
import subprocess
import threading
import urllib.request
import urllib.parse
import digitalio
import board
from PIL import Image, ImageDraw, ImageFont
import adafruit_rgb_display.ili9341 as ili9341
import RPi.GPIO as GPIO

# Contexto SSL sin verificación (para Pis con certificados desactualizados)
SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE
import xpt2046_circuitpython

# ==========================================
# 1. CONFIGURACIÓN GENERAL
# ==========================================
PIPE_PATH = "/tmp/shairport-sync-metadata"
COVER_DIR = "/tmp/shairport-sync/.cache/coverart"
SPOTIFY_COVER_PATH = "/tmp/spotify-cover.jpg"
SAMPLE_RATE = 44100.0

ANCHO_PANTALLA = 240
ALTO_PANTALLA = 320
COVER_SIZE = 240
INFO_Y_START = COVER_SIZE
MARGEN = 10

COLOR_FONDO = (0, 0, 0)
COLOR_FONDO_BARRA = (60, 60, 60)
COLOR_PROGRESO = (255, 255, 255)

# Transición slide
SLIDE_DURACION = 0.5

# Volumen overlay
VOL_VISIBLE_SEG = 1.5
VOL_FADEOUT_SEG = 0.5
VOL_TOTAL_SEG = VOL_VISIBLE_SEG + VOL_FADEOUT_SEG

# Touch
TOUCH_DEBOUNCE = 0.8

# Letras
LRCLIB_URL = "https://lrclib.net/api/get"

# Fuentes
try:
    fuente_titulo = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 14)
    fuente_artista = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 12)
    fuente_album = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 10)
    fuente_tiempo = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 10)
    fuente_volumen = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 13)
    fuente_letra_info = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 10)
    fuente_letra_activa = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 14)
    fuente_letra_inactiva = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 12)
except IOError:
    _def = ImageFont.load_default()
    fuente_titulo = _def
    fuente_artista = _def
    fuente_album = _def
    fuente_tiempo = _def
    fuente_volumen = _def
    fuente_letra_info = _def
    fuente_letra_activa = _def
    fuente_letra_inactiva = _def


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


def generar_colores(color_fondo):
    lum = luminancia(color_fondo)
    fr, fg, fb = color_fondo
    if lum > 0.45:
        return {
            "titulo": (0, 0, 0), "artista": (40, 40, 40), "album": (70, 70, 70),
            "letra_activa": (0, 0, 0),
            "letra_previa": mezclar_color((fr, fg, fb), (40, 40, 40), 0.6),
            "letra_siguiente": mezclar_color((fr, fg, fb), (60, 60, 60), 0.5),
            "info": mezclar_color((fr, fg, fb), (50, 50, 50), 0.5),
            "separador": mezclar_color((fr, fg, fb), (0, 0, 0), 0.3),
            "barra_bg": mezclar_color((fr, fg, fb), (0, 0, 0), 0.25),
            "barra_fg": (0, 0, 0), "tiempo": (50, 50, 50),
            "msg": (80, 80, 80),
        }
    else:
        return {
            "titulo": (255, 255, 255), "artista": (200, 200, 200), "album": (150, 150, 150),
            "letra_activa": (255, 255, 255),
            "letra_previa": mezclar_color((fr, fg, fb), (120, 120, 120), 0.5),
            "letra_siguiente": mezclar_color((fr, fg, fb), (200, 200, 200), 0.45),
            "info": mezclar_color((fr, fg, fb), (200, 200, 200), 0.4),
            "separador": mezclar_color((fr, fg, fb), (255, 255, 255), 0.15),
            "barra_bg": mezclar_color((fr, fg, fb), (255, 255, 255), 0.2),
            "barra_fg": (255, 255, 255),
            "tiempo": mezclar_color((fr, fg, fb), (220, 220, 220), 0.4),
            "msg": mezclar_color((fr, fg, fb), (180, 180, 180), 0.5),
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
        self.letras_mensaje = ""
        self.hubo_cambio_cancion = False
        # Fuente activa: "airplay" o "spotify"
        self.fuente = ""
        # Carátula de Spotify (PIL Image)
        self.cover_spotify = None
        self.cover_spotify_cambio = False

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
            return list(self.letras_sync), self.letras_estado, self.letras_mensaje

    def obtener_cover_spotify(self):
        with self.lock:
            cambio = self.cover_spotify_cambio
            self.cover_spotify_cambio = False
            return self.cover_spotify, cambio


# ==========================================
# 4. LECTOR DEL PIPE DE METADATA (AirPlay)
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
                        estado.fuente = "airplay"
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
                            estado.fuente = "airplay"
                        except ValueError:
                            pass

                elif code == "pfls" or code == "pend":
                    if not estado.esta_pausado and estado.posicion_seg is not None and estado.timestamp_posicion is not None:
                        estado.posicion_seg += (ahora - estado.timestamp_posicion)
                    estado.esta_pausado = True
                    estado.timestamp_posicion = ahora

                elif code == "prsm" or code == "pbeg":
                    estado.esta_pausado = False
                    estado.timestamp_posicion = ahora

                elif code == "pvol":
                    try:
                        airplay_vol = float(data.split(",")[0])
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
    print("📡 [AirPlay] Conectando al pipe de metadata...")
    while True:
        try:
            with open(PIPE_PATH, "r") as pipe:
                print("📡 [AirPlay] Pipe conectado.")
                buffer = ""
                for linea in pipe:
                    buffer += linea
                    while "</item>" in buffer:
                        idx = buffer.index("</item>") + len("</item>")
                        procesar_item(estado, buffer[:idx])
                        buffer = buffer[idx:]
        except Exception as e:
            time.sleep(2)


# ==========================================
# 5. LECTOR DE EVENTOS SPOTIFY (archivos + oEmbed)
# ==========================================

SPOTIFY_EVENTS_DIR = "/tmp/spotify-events"


def descargar_caratula_spotify(url):
    """Descarga carátula de Spotify/oEmbed y retorna PIL Image."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "RaspberryMusicPlayer/2.0"})
        with urllib.request.urlopen(req, timeout=5, context=SSL_CTX) as resp:
            data = resp.read()
            img = Image.open(io.BytesIO(data))
            if img.mode != "RGB":
                img = img.convert("RGB")
            return img.resize((COVER_SIZE, COVER_SIZE))
    except Exception as e:
        print(f"⚠️  Error descargando carátula: {e}")
        return None


def obtener_thumbnail_spotify(track_id):
    """Obtiene la URL del thumbnail de Spotify via oEmbed."""
    try:
        url = f"https://open.spotify.com/oembed?url=spotify:track:{track_id}"
        req = urllib.request.Request(url, headers={"User-Agent": "RaspberryMusicPlayer/2.0"})
        with urllib.request.urlopen(req, timeout=5, context=SSL_CTX) as resp:
            data = json.loads(resp.read().decode())
        return data.get("thumbnail_url", "")
    except Exception as e:
        print(f"⚠️  Error oEmbed: {e}")
        return ""


def obtener_info_logs():
    """Lee título y duración del track actual de los logs de librespot."""
    titulo = ""
    duracion_seg = None
    try:
        resultado = subprocess.run(
            ["journalctl", "-u", "spotify-connect", "-n", "50", "--no-pager", "-o", "cat"],
            capture_output=True, text=True, timeout=3,
        )
        for linea in reversed(resultado.stdout.split("\n")):
            # Buscar duración: <Title> (273173 ms) loaded
            if not duracion_seg:
                m = re.search(r'<(.+?)>\s*\((\d+)\s*ms\)\s*loaded', linea)
                if m:
                    titulo = m.group(1)
                    duracion_seg = int(m.group(2)) / 1000.0
                    break
            # Buscar título: Loading <Title> with Spotify URI
            if not titulo:
                m = re.search(r'Loading\s+<(.+?)>\s+with\s+Spotify\s+URI', linea)
                if m:
                    titulo = m.group(1)
    except Exception:
        pass
    return titulo, duracion_seg


def leer_archivo_evento(nombre):
    """Lee un archivo del directorio de eventos de Spotify."""
    try:
        ruta = os.path.join(SPOTIFY_EVENTS_DIR, nombre)
        with open(ruta, "r") as f:
            return f.read().strip()
    except Exception:
        return ""


def hilo_spotify_eventos(estado):
    """Hilo que monitorea los archivos de eventos de librespot."""
    print("🎵 [Spotify] Iniciando monitor de eventos...")
    ultimo_track_id = ""
    ultimo_timestamp = ""
    ultimo_art_url = ""

    while True:
        try:
            # Leer timestamp del evento más reciente
            ts = leer_archivo_evento("timestamp")
            if not ts or ts == ultimo_timestamp:
                time.sleep(0.5)
                continue

            ultimo_timestamp = ts
            evento = leer_archivo_evento("event")
            track_id = leer_archivo_evento("track_id")
            position_ms = leer_archivo_evento("position_ms")

            if not track_id:
                time.sleep(0.5)
                continue

            ahora = time.time()
            evento_ts = float(ts) / 1000.0 if len(ts) > 10 else float(ts)

            # Detectar cambio de canción
            cancion_cambio = (track_id != ultimo_track_id)

            if cancion_cambio:
                ultimo_track_id = track_id
                print(f"🎵 [Spotify] Nueva canción: track_id={track_id}")

                # Esperar un momento para que los logs se escriban
                time.sleep(1)

                # Obtener título y duración de los logs de librespot
                titulo, duracion_seg = obtener_info_logs()

                if titulo:
                    with estado.lock:
                        estado.titulo = titulo
                        estado.artista = ""  # Se llenará cuando lrclib responda
                        estado.album = ""    # Se llenará cuando lrclib responda
                        estado.hubo_cambio_cancion = True
                        estado.fuente = "spotify"
                        if duracion_seg:
                            estado.duracion_seg = duracion_seg

                    print(f"🎵 [Spotify] {titulo} ({duracion_seg:.0f}s)" if duracion_seg else f"🎵 [Spotify] {titulo}")

                # Obtener carátula via oEmbed
                thumbnail_url = obtener_thumbnail_spotify(track_id)
                if thumbnail_url and thumbnail_url != ultimo_art_url:
                    ultimo_art_url = thumbnail_url
                    cover = descargar_caratula_spotify(thumbnail_url)
                    if cover:
                        with estado.lock:
                            estado.cover_spotify = cover
                            estado.cover_spotify_cambio = True
                        print(f"🎨 [Spotify] Carátula descargada")

            # Actualizar posición y estado de reproducción
            with estado.lock:
                if estado.fuente == "spotify":
                    if evento == "playing":
                        pos_ms = int(position_ms) if position_ms else 0
                        estado.posicion_seg = pos_ms / 1000.0
                        estado.timestamp_posicion = evento_ts
                        estado.esta_pausado = False
                    elif evento == "paused":
                        pos_ms = int(position_ms) if position_ms else 0
                        estado.posicion_seg = pos_ms / 1000.0
                        estado.timestamp_posicion = ahora
                        estado.esta_pausado = True
                    elif evento == "stopped":
                        estado.esta_pausado = True
                    elif evento in ("changed", "preloading", "started"):
                        estado.esta_pausado = False

        except Exception as e:
            print(f"⚠️  [Spotify] Error: {e}")

        time.sleep(0.5)


# ==========================================
# 6. LETRAS SINCRONIZADAS (LRCLIB)
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
        estado.letras_mensaje = "Buscando letras..."
        duracion_actual = estado.duracion_seg

    print(f"🔍 Buscando letras: {titulo} - {artista}")
    time.sleep(0.5)

    try:
        # Limpiar título: quitar "(Remaster)", "[Deluxe]", etc.
        t_limpio = re.sub(r'\s*[\(\[].*?[\)\]]\s*', '', titulo).strip()
        # Quitar " - XXXX Remaster" o similar
        t_limpio = re.sub(r'\s*[-–]\s*\d{4}\s*Remaster.*', '', t_limpio, flags=re.IGNORECASE).strip()
        a_limpio = re.sub(r'\s*[\(\[].*?[\)\]]\s*', '', artista).strip() if artista else ""

        query = urllib.parse.quote(f"{a_limpio} {t_limpio}".strip())
        url = f"https://lrclib.net/api/search?q={query}"
        req = urllib.request.Request(url, headers={"User-Agent": "RaspberryMusicPlayer/2.0"})

        with urllib.request.urlopen(req, timeout=5, context=SSL_CTX) as resp:
            resultados = json.loads(resp.read().decode())

        if resultados and isinstance(resultados, list) and len(resultados) > 0:
            mejor = resultados[0]

            # Extraer artista y álbum de la respuesta de lrclib
            artista_lrclib = mejor.get("artistName", "")
            album_lrclib = mejor.get("albumName", "")

            # Actualizar artista/album en el estado si estaban vacíos
            with estado.lock:
                if artista_lrclib and not estado.artista:
                    estado.artista = artista_lrclib
                    estado.hubo_cambio_cancion = True
                    print(f"🎤 Artista encontrado via letras: {artista_lrclib}")
                if album_lrclib and not estado.album:
                    estado.album = album_lrclib
                    estado.hubo_cambio_cancion = True
                    print(f"💿 Álbum encontrado via letras: {album_lrclib}")

            if mejor.get("syncedLyrics"):
                parsed = parsear_lrc(mejor["syncedLyrics"])
                if parsed:
                    with estado.lock:
                        estado.letras_sync = parsed
                        estado.letras_estado = "encontradas"
                        estado.letras_mensaje = ""
                    print(f"✅ Letras sincronizadas ({len(parsed)} líneas)")
                    return

            if mejor.get("plainLyrics"):
                lineas_texto = [l.strip() for l in mejor["plainLyrics"].split("\n") if l.strip()]
                if lineas_texto:
                    dur = duracion_actual if duracion_actual and duracion_actual > 0 else 180.0
                    tiempo_por_linea = dur / (len(lineas_texto) + 1)
                    lineas = [(i * tiempo_por_linea, txt) for i, txt in enumerate(lineas_texto)]
                    with estado.lock:
                        estado.letras_sync = lineas
                        estado.letras_estado = "solo_texto"
                        estado.letras_mensaje = "Auto-Scroll"
                    print(f"📝 Letras sin sync ({len(lineas)} líneas)")
                    return

            with estado.lock:
                estado.letras_estado = "no_encontradas"
                estado.letras_mensaje = "Instrumental / Sin letra"
        else:
            with estado.lock:
                estado.letras_estado = "no_encontradas"
                estado.letras_mensaje = "Letra no encontrada"

    except Exception as e:
        print(f"❌ Error buscando letras: {e}")
        with estado.lock:
            estado.letras_estado = "no_encontradas"
            estado.letras_mensaje = "Error de conexión"


def iniciar_busqueda_letras(estado, titulo, artista):
    hilo = threading.Thread(target=buscar_letras, args=(estado, titulo, artista), daemon=True)
    hilo.start()


# ==========================================
# 7. BOTONES
# ==========================================
GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)

BTN_K1_PREV = 25
BTN_K2_PAUSA = 23
BTN_K3_NEXT = 24

GPIO.setup(BTN_K1_PREV, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.setup(BTN_K2_PAUSA, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.setup(BTN_K3_NEXT, GPIO.IN, pull_up_down=GPIO.PUD_UP)

modo_letras = False


def control_musica(canal):
    """Envía comandos via D-Bus a ShairportSync y librespot."""
    if canal == BTN_K1_PREV:
        cmd = "Previous"
    elif canal == BTN_K2_PAUSA:
        cmd = "PlayPause"
    elif canal == BTN_K3_NEXT:
        cmd = "Next"
    else:
        return

    # Enviar a ShairportSync (AirPlay)
    os.system(
        f"dbus-send --system --type=method_call "
        f"--dest=org.mpris.MediaPlayer2.ShairportSync "
        f"/org/mpris/MediaPlayer2 "
        f"org.mpris.MediaPlayer2.Player.{cmd} 2>/dev/null &"
    )
    # Enviar a librespot (Spotify Connect)
    os.system(
        f"dbus-send --system --type=method_call "
        f"--dest=org.mpris.MediaPlayer2.Librespot "
        f"/org/mpris/MediaPlayer2 "
        f"org.mpris.MediaPlayer2.Player.{cmd} 2>/dev/null &"
    )


GPIO.add_event_detect(BTN_K1_PREV, GPIO.FALLING, callback=control_musica, bouncetime=300)
GPIO.add_event_detect(BTN_K2_PAUSA, GPIO.FALLING, callback=control_musica, bouncetime=300)
GPIO.add_event_detect(BTN_K3_NEXT, GPIO.FALLING, callback=control_musica, bouncetime=300)

# ==========================================
# 8. PANTALLA SPI + TOUCH
# ==========================================
cs_pin = digitalio.DigitalInOut(board.CE0)
dc_pin = digitalio.DigitalInOut(board.D22)
reset_pin = digitalio.DigitalInOut(board.D27)
spi = board.SPI()

disp = ili9341.ILI9341(
    spi, rotation=0, cs=cs_pin, dc=dc_pin, rst=reset_pin, baudrate=40000000,
)

cs_touch = digitalio.DigitalInOut(board.CE1)
irq_touch = digitalio.DigitalInOut(board.D17)
touch = xpt2046_circuitpython.Touch(
    spi, cs=cs_touch, interrupt=irq_touch, force_baudrate=4000000,
)
print("👆 Touch configurado")

touch_previo = False
ultimo_touch = 0.0


# ==========================================
# 9. FUNCIONES DE DIBUJO
# ==========================================

def formato_tiempo(segundos):
    if segundos is None or segundos < 0:
        return "--:--"
    s = int(segundos)
    return f"{s // 60}:{s % 60:02d}"


def medir_texto(draw, texto, fuente):
    bbox = draw.textbbox((0, 0), texto, font=fuente)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def medir_texto_multilinea(draw, texto, fuente):
    lineas = texto.split("\n")
    max_w = 0
    total_h = 0
    for linea in lineas:
        tw, th = medir_texto(draw, linea, fuente)
        if tw > max_w:
            max_w = tw
        total_h += th + 4
    return max_w, total_h


def envolver_texto(draw, texto, fuente, max_ancho):
    lineas = []
    for parrafo in texto.split("\n"):
        palabras = parrafo.split()
        linea_actual = ""
        for palabra in palabras:
            prueba = linea_actual + palabra + " "
            tw, _ = medir_texto(draw, prueba, fuente)
            if tw <= max_ancho:
                linea_actual = prueba
            else:
                if linea_actual:
                    lineas.append(linea_actual.strip())
                linea_actual = palabra + " "
        if linea_actual:
            lineas.append(linea_actual.strip())
    return "\n".join(lineas)


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


def dibujar_barra_progreso(lienzo, posicion_seg, duracion_seg, y_barra, colores):
    draw = ImageDraw.Draw(lienzo)
    barra_h = 4
    barra_x0 = MARGEN
    barra_x1 = ANCHO_PANTALLA - MARGEN
    draw.rectangle([barra_x0, y_barra, barra_x1, y_barra + barra_h], fill=colores["barra_bg"])
    if duracion_seg and duracion_seg > 0 and posicion_seg is not None:
        progreso = max(0.0, min(1.0, posicion_seg / duracion_seg))
    else:
        progreso = 0.0
    largo = int((barra_x1 - barra_x0) * progreso)
    if largo > 0:
        draw.rectangle([barra_x0, y_barra, barra_x0 + largo, y_barra + barra_h], fill=colores["barra_fg"])
    tiempo_y = y_barra + barra_h + 3
    draw.text((barra_x0, tiempo_y), formato_tiempo(posicion_seg), fill=colores["tiempo"], font=fuente_tiempo)
    txt_total = formato_tiempo(duracion_seg)
    ancho_tt, _ = medir_texto(draw, txt_total, fuente_tiempo)
    draw.text((barra_x1 - ancho_tt, tiempo_y), txt_total, fill=colores["tiempo"], font=fuente_tiempo)


def dibujar_info_cover(lienzo, titulo, artista, album, posicion_seg, duracion_seg,
                       tiempo_scroll, colores, color_fondo_c):
    titulo_y = INFO_Y_START + 2
    dibujar_texto_scroll(lienzo, titulo if titulo else "Sin título",
                         fuente_titulo, colores["titulo"], titulo_y, tiempo_scroll, color_fondo_c)
    artista_y = titulo_y + 18
    dibujar_texto_scroll(lienzo, artista if artista else "Artista desconocido",
                         fuente_artista, colores["artista"], artista_y, tiempo_scroll, color_fondo_c)
    album_y = artista_y + 16
    if album:
        dibujar_texto_scroll(lienzo, album, fuente_album, colores["album"], album_y, tiempo_scroll, color_fondo_c)
    barra_y = album_y + 15
    dibujar_barra_progreso(lienzo, posicion_seg, duracion_seg, barra_y, colores)


def encontrar_linea_actual(letras_sync, posicion_seg):
    idx = 0
    for i, (ts, _) in enumerate(letras_sync):
        if (posicion_seg or 0) >= ts:
            idx = i
        else:
            break
    return idx


def dibujar_vista_letras(lienzo, titulo, artista, letras_sync, letras_estado,
                         letras_mensaje, posicion_seg, duracion_seg, colores):
    draw = ImageDraw.Draw(lienzo)
    max_ancho_texto = ANCHO_PANTALLA - 20

    header = titulo if titulo else "Sin título"
    if artista:
        header += f"  •  {artista}"
    while len(header) > 3:
        hw, _ = medir_texto(draw, header, fuente_letra_info)
        if hw <= ANCHO_PANTALLA - 20:
            break
        header = header[:-4] + "…"
    hw, _ = medir_texto(draw, header, fuente_letra_info)
    draw.text(((ANCHO_PANTALLA - hw) // 2, 6), header, fill=colores["info"], font=fuente_letra_info)
    draw.line([(MARGEN, 22), (ANCHO_PANTALLA - MARGEN, 22)], fill=colores["separador"], width=1)

    zona_y_inicio = 28
    zona_y_fin = 282

    if not letras_sync:
        msg = letras_mensaje if letras_mensaje else "Esperando canción..."
        msg_wrap = envolver_texto(draw, msg, fuente_artista, max_ancho_texto)
        mw, mh = medir_texto_multilinea(draw, msg_wrap, fuente_artista)
        y_msg = zona_y_inicio + (zona_y_fin - zona_y_inicio) // 2 - mh // 2
        draw.multiline_text(((ANCHO_PANTALLA - mw) // 2, y_msg), msg_wrap,
                            fill=colores["msg"], font=fuente_artista, align="center")
    else:
        idx_actual = encontrar_linea_actual(letras_sync, posicion_seg or 0)
        y_centro = zona_y_inicio + (zona_y_fin - zona_y_inicio) // 2
        espaciado = 12

        if idx_actual < len(letras_sync):
            txt_wrap = envolver_texto(draw, letras_sync[idx_actual][1], fuente_letra_activa, max_ancho_texto)
            tw, th = medir_texto_multilinea(draw, txt_wrap, fuente_letra_activa)
            y_dibujo = y_centro - th // 2
            draw.multiline_text(((ANCHO_PANTALLA - tw) // 2, y_dibujo), txt_wrap,
                                fill=colores["letra_activa"], font=fuente_letra_activa, align="center")
            y_arriba = y_dibujo - espaciado
            y_abajo = y_dibujo + th + espaciado
        else:
            y_arriba = y_centro - espaciado
            y_abajo = y_centro + espaciado

        for i in range(idx_actual - 1, max(-1, idx_actual - 5), -1):
            txt_wrap = envolver_texto(draw, letras_sync[i][1], fuente_letra_inactiva, max_ancho_texto)
            tw, th = medir_texto_multilinea(draw, txt_wrap, fuente_letra_inactiva)
            y_dibujo = y_arriba - th
            if y_dibujo < zona_y_inicio - 5:
                break
            draw.multiline_text(((ANCHO_PANTALLA - tw) // 2, y_dibujo), txt_wrap,
                                fill=colores["letra_previa"], font=fuente_letra_inactiva, align="center")
            y_arriba = y_dibujo - espaciado

        for i in range(idx_actual + 1, min(len(letras_sync), idx_actual + 5)):
            txt_wrap = envolver_texto(draw, letras_sync[i][1], fuente_letra_inactiva, max_ancho_texto)
            tw, th = medir_texto_multilinea(draw, txt_wrap, fuente_letra_inactiva)
            if y_abajo + th > zona_y_fin + 5:
                break
            draw.multiline_text(((ANCHO_PANTALLA - tw) // 2, y_abajo), txt_wrap,
                                fill=colores["letra_siguiente"], font=fuente_letra_inactiva, align="center")
            y_abajo += th + espaciado

    draw.line([(MARGEN, zona_y_fin), (ANCHO_PANTALLA - MARGEN, zona_y_fin)], fill=colores["separador"], width=1)
    dibujar_barra_progreso(lienzo, posicion_seg, duracion_seg, zona_y_fin + 6, colores)


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
        draw_ov.rectangle([barra_m, barra_y, barra_m + fill_w, barra_y + barra_h], fill=(255, 255, 255))
    region = lienzo.crop((ov_x, ov_y, ov_x + ov_ancho, ov_y + ov_alto))
    mezclado = Image.blend(region, overlay, opacidad * 0.85)
    lienzo.paste(mezclado, (ov_x, ov_y))


# ==========================================
# 10. BUCLE PRINCIPAL
# ==========================================

estado = EstadoReproductor()

# Hilos de metadata
threading.Thread(target=hilo_lector_pipe, args=(estado,), daemon=True).start()
threading.Thread(target=hilo_spotify_eventos, args=(estado,), daemon=True).start()

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

# Color dominante
color_fondo_dom = (20, 20, 20)
colores = generar_colores(color_fondo_dom)

print("🚀 Sistema Listo. Esperando música desde AirPlay o Spotify Connect...")
print("👆 Toca la pantalla con el lápiz para alternar entre carátula y letras")

try:
    while True:
        ahora = time.time()

        # --- Verificar nueva carátula (AirPlay) ---
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

                        color_fondo_dom = extraer_color_dominante(nueva_cover)
                        colores = generar_colores(color_fondo_dom)

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

        # --- Verificar carátula de Spotify ---
        cover_spotify, cover_spotify_cambio = estado.obtener_cover_spotify()
        if cover_spotify_cambio and cover_spotify is not None:
            nueva_cover = cover_spotify
            caratula_cambio = True

            color_fondo_dom = extraer_color_dominante(nueva_cover)
            colores = generar_colores(color_fondo_dom)

            if imagen_caratula is not None:
                imagen_caratula_vieja = imagen_caratula.copy()
                imagen_caratula_nueva = nueva_cover
                slide_inicio = ahora
            else:
                imagen_caratula = nueva_cover

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
            lienzo = Image.new("RGB", (ANCHO_PANTALLA, ALTO_PANTALLA), color_fondo_dom)

            if modo_letras:
                letras_sync, letras_estado, letras_mensaje = estado.obtener_letras()
                dibujar_vista_letras(
                    lienzo, titulo_mostrado, artista_mostrado,
                    letras_sync, letras_estado, letras_mensaje,
                    posicion_seg, duracion_seg, colores,
                )
            else:
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
                    lienzo, titulo_mostrado, artista_mostrado, album_mostrado,
                    posicion_seg, duracion_seg,
                    tiempo_scroll, colores, color_fondo_dom,
                )

            if tiempo_desde_vol <= VOL_TOTAL_SEG:
                dibujar_volumen(lienzo, volumen_pct, tiempo_desde_vol)

            disp.image(lienzo.convert("RGB"))

            # --- Touch ---
            try:
                tocado = touch.is_pressed()
                if tocado and not touch_previo:
                    try:
                        x_t, y_t = touch.get_coordinates()
                        if ahora - ultimo_touch > TOUCH_DEBOUNCE:
                            modo_letras = not modo_letras
                            ultimo_touch = ahora
                            print(f"👆 Modo: {'Letras' if modo_letras else 'Carátula'}")
                    except Exception:
                        pass
                touch_previo = tocado
            except Exception:
                touch_previo = False

        time.sleep(0.25)

except KeyboardInterrupt:
    print("\nApagando sistema y liberando pines...")
    GPIO.cleanup()
