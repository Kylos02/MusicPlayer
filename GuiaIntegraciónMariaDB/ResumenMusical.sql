-- ============================================================================
-- CONSULTAS DE ESTADÍSTICAS - REPRODUCTOR DE MÚSICA
-- ============================================================================
-- Selecciona la base de datos para no tener que escribir "reproductor." cada vez
USE reproductor;


-- ────────────────────────────────────────────────────────────────────────
-- 1) TOP 10 ARTISTAS MÁS ESCUCHADOS (por número de reproducciones)
-- ────────────────────────────────────────────────────────────────────────
SELECT artista                                AS 'Artista',
       COUNT(*)                               AS 'Veces escuchado',
       COUNT(DISTINCT titulo)                 AS 'Canciones distintas',
       ROUND(SUM(duracion_seg)/60, 1)         AS 'Minutos totales'
FROM reproducciones
WHERE artista <> ''
GROUP BY artista
ORDER BY `Veces escuchado` DESC
LIMIT 10;


-- ────────────────────────────────────────────────────────────────────────
-- 2) TOP 10 CANCIONES MÁS ESCUCHADAS
-- ────────────────────────────────────────────────────────────────────────
SELECT titulo                                 AS 'Canción',
       artista                                AS 'Artista',
       album                                  AS 'Álbum',
       COUNT(*)                               AS 'Reproducciones',
       MAX(reproducido_en)                    AS 'Última vez'
FROM reproducciones
WHERE titulo <> ''
GROUP BY titulo, artista, album
ORDER BY `Reproducciones` DESC, `Última vez` DESC
LIMIT 10;


-- ────────────────────────────────────────────────────────────────────────
-- 3) TOP 10 ÁLBUMES MÁS ESCUCHADOS
-- ────────────────────────────────────────────────────────────────────────
SELECT album                                  AS 'Álbum',
       artista                                AS 'Artista',
       COUNT(*)                               AS 'Reproducciones',
       COUNT(DISTINCT titulo)                 AS 'Canciones distintas'
FROM reproducciones
WHERE album <> '' AND artista <> ''
GROUP BY album, artista
ORDER BY `Reproducciones` DESC
LIMIT 10;


-- ────────────────────────────────────────────────────────────────────────
-- 4) DISTRIBUCIÓN POR FUENTE (Airplay / Spotify / Local)
-- ────────────────────────────────────────────────────────────────────────
SELECT fuente                                                  AS 'Fuente',
       COUNT(*)                                                AS 'Reproducciones',
       ROUND(COUNT(*) * 100.0 / (SELECT COUNT(*) FROM reproducciones), 1)
                                                               AS '% del total',
       ROUND(SUM(duracion_seg)/60, 1)                          AS 'Minutos totales'
FROM reproducciones
GROUP BY fuente
ORDER BY `Reproducciones` DESC;


-- ────────────────────────────────────────────────────────────────────────
-- 5) RESUMEN GENERAL (estilo "Spotify Wrapped" mini)
-- ────────────────────────────────────────────────────────────────────────
SELECT COUNT(*)                                AS 'Reproducciones totales',
       COUNT(DISTINCT titulo)                  AS 'Canciones únicas',
       COUNT(DISTINCT artista)                 AS 'Artistas únicos',
       COUNT(DISTINCT album)                   AS 'Álbumes únicos',
       ROUND(SUM(duracion_seg)/3600, 2)        AS 'Horas escuchadas',
       MIN(reproducido_en)                     AS 'Primera reproducción',
       MAX(reproducido_en)                     AS 'Última reproducción'
FROM reproducciones;


-- ────────────────────────────────────────────────────────────────────────
-- 6) ACTIVIDAD POR DÍA (últimos 30 días)
-- ────────────────────────────────────────────────────────────────────────
SELECT DATE(reproducido_en)                   AS 'Día',
       DAYNAME(reproducido_en)                AS 'Día semana',
       COUNT(*)                               AS 'Canciones',
       ROUND(SUM(duracion_seg)/60, 1)         AS 'Minutos'
FROM reproducciones
WHERE reproducido_en >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
GROUP BY DATE(reproducido_en), DAYNAME(reproducido_en)
ORDER BY `Día` DESC;


-- ────────────────────────────────────────────────────────────────────────
-- 7) ¿A QUÉ HORA DEL DÍA ESCUCHO MÁS MÚSICA?
-- ────────────────────────────────────────────────────────────────────────
SELECT HOUR(reproducido_en)                   AS 'Hora',
       COUNT(*)                               AS 'Reproducciones',
       REPEAT('█', COUNT(*))                  AS 'Gráfica'
FROM reproducciones
GROUP BY HOUR(reproducido_en)
ORDER BY `Hora` ASC;


-- ────────────────────────────────────────────────────────────────────────
-- 8) ¿QUÉ DÍA DE LA SEMANA ESCUCHO MÁS?
-- ────────────────────────────────────────────────────────────────────────
SELECT DAYNAME(reproducido_en)                AS 'Día de la semana',
       COUNT(*)                               AS 'Reproducciones',
       ROUND(SUM(duracion_seg)/60, 1)         AS 'Minutos'
FROM reproducciones
GROUP BY DAYOFWEEK(reproducido_en), DAYNAME(reproducido_en)
ORDER BY DAYOFWEEK(reproducido_en);


-- ────────────────────────────────────────────────────────────────────────
-- 9) HISTORIAL RECIENTE (últimas 25 canciones, formato bonito)
-- ────────────────────────────────────────────────────────────────────────
SELECT DATE_FORMAT(reproducido_en, '%d/%m %H:%i')         AS 'Cuándo',
       titulo                                             AS 'Canción',
       artista                                            AS 'Artista',
       fuente                                             AS 'Fuente',
       CONCAT(FLOOR(duracion_seg/60), ':',
              LPAD(FLOOR(duracion_seg%60), 2, '0'))       AS 'Duración'
FROM reproducciones
ORDER BY reproducido_en DESC
LIMIT 25;


-- ────────────────────────────────────────────────────────────────────────
-- 10) ARTISTAS QUE SOLO ESCUCHASTE UNA VEZ (descubrimientos)
-- ────────────────────────────────────────────────────────────────────────
SELECT artista                                AS 'Artista',
       titulo                                 AS 'Única canción escuchada',
       reproducido_en                         AS 'Cuándo'
FROM reproducciones r1
WHERE artista <> ''
  AND (SELECT COUNT(*) FROM reproducciones r2 WHERE r2.artista = r1.artista) = 1
ORDER BY reproducido_en DESC;


-- ────────────────────────────────────────────────────────────────────────
-- 11) CANCIONES "OBSESIÓN": las que has repetido el mismo día
-- ────────────────────────────────────────────────────────────────────────
SELECT titulo                                 AS 'Canción',
       artista                                AS 'Artista',
       DATE(reproducido_en)                   AS 'Día',
       COUNT(*)                               AS 'Veces ese día'
FROM reproducciones
WHERE titulo <> ''
GROUP BY titulo, artista, DATE(reproducido_en)
HAVING COUNT(*) >= 3
ORDER BY `Veces ese día` DESC, `Día` DESC;


-- ────────────────────────────────────────────────────────────────────────
-- 12) TOP ARTISTAS POR FUENTE (¿qué escucho más en Spotify vs Airplay?)
-- ────────────────────────────────────────────────────────────────────────
SELECT fuente                                 AS 'Fuente',
       artista                                AS 'Artista',
       COUNT(*)                               AS 'Veces'
FROM reproducciones
WHERE artista <> ''
GROUP BY fuente, artista
HAVING COUNT(*) >= 2
ORDER BY fuente, `Veces` DESC;


-- ────────────────────────────────────────────────────────────────────────
-- 13) ESTA SEMANA vs LA SEMANA PASADA
-- ────────────────────────────────────────────────────────────────────────
SELECT
    CASE
        WHEN reproducido_en >= DATE_SUB(CURDATE(), INTERVAL 7 DAY) THEN 'Esta semana'
        ELSE 'Semana anterior'
    END                                       AS 'Periodo',
    COUNT(*)                                  AS 'Canciones',
    COUNT(DISTINCT artista)                   AS 'Artistas distintos',
    ROUND(SUM(duracion_seg)/60, 1)            AS 'Minutos'
FROM reproducciones
WHERE reproducido_en >= DATE_SUB(CURDATE(), INTERVAL 14 DAY)
GROUP BY `Periodo`;