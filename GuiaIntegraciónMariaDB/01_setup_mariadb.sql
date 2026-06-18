-- ============================================================================
-- SETUP DE MARIADB 
-- ============================================================================

-- 1) Base de datos con soporte completo UTF-8 (emojis, acentos, japonés, etc.)
CREATE DATABASE IF NOT EXISTS reproductor
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

USE reproductor;

-- 2) Tabla principal de reproducciones
CREATE TABLE IF NOT EXISTS reproducciones (
    id              BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    titulo          VARCHAR(255) NOT NULL,
    artista         VARCHAR(255) NOT NULL DEFAULT '',
    album           VARCHAR(255) NOT NULL DEFAULT '',
    duracion_seg    FLOAT NULL,
    fuente          ENUM('airplay', 'spotify', 'local') NOT NULL,
    reproducido_en  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    -- Índices para consultas típicas (historial, top artistas, filtros por fuente)
    INDEX idx_reproducido_en (reproducido_en DESC),
    INDEX idx_fuente         (fuente),
    INDEX idx_artista        (artista),
    INDEX idx_titulo_artista (titulo, artista)
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;

-- 3) Usuario remoto para Node-RED
CREATE USER IF NOT EXISTS 'nodered'@'192.168.1.176'
    IDENTIFIED BY 'equipo5';

-- Permisos mínimos: solo insertar y leer de la tabla reproducciones
GRANT SELECT, INSERT ON reproductor.reproducciones TO 'nodered'@'192.168.1.176';


FLUSH PRIVILEGES;

-- 4) Verificación
SELECT 'Setup completado. Filas en tabla:' AS info;
SELECT COUNT(*) AS total FROM reproducciones;
SELECT user, host FROM mysql.user WHERE user = 'nodered';
