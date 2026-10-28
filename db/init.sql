CREATE TABLE IF NOT EXISTS registro (
    id                      SERIAL PRIMARY KEY,
    fecha_registro          DATE DEFAULT CURRENT_DATE,
    nombre_completo         VARCHAR(100) NOT NULL,
    fecha_nacimiento        DATE,
    edad                    INTEGER,
    genero                  VARCHAR(30),
    estado_civil            VARCHAR(30),
    nacionalidad            VARCHAR(60),
    procedencia             VARCHAR(150),
    curp                    VARCHAR(18),
    rfc                     VARCHAR(13),
    nss                     VARCHAR(20),
    tel_movil               VARCHAR(20),
    correo                  VARCHAR(255),
    codigo_postal           VARCHAR(10),
    colonia                 VARCHAR(150),
    calle                   VARCHAR(150),
    num_ext                 VARCHAR(20),
    num_int                 VARCHAR(20),
    municipio               VARCHAR(150),
    estado                  VARCHAR(100),
    nombre_emergencia       VARCHAR(255),
    parentesco_emergencia   VARCHAR(100),
    telefono_emergencia     VARCHAR(20),
    firma_archivo           TEXT,
    fotografia_base64       TEXT,
    documentos_papeleria    TEXT
);

CREATE TABLE IF NOT EXISTS encuesta_reclutamiento (
    id                  SERIAL PRIMARY KEY,
    fuente              VARCHAR(50),
    fecha_registro      DATE DEFAULT CURRENT_DATE,
    nombre_reclutador   VARCHAR(50),
    nombre_empleado     VARCHAR(200),
    detalle             TEXT,
    registro_id         INTEGER,
    CONSTRAINT encuesta_reclutamiento_registro_id_fkey
        FOREIGN KEY (registro_id) REFERENCES registro(id) ON DELETE SET NULL
);

ALTER TABLE encuesta_reclutamiento
    ALTER COLUMN registro_id DROP NOT NULL;
ALTER TABLE encuesta_reclutamiento
    DROP CONSTRAINT IF EXISTS encuesta_reclutamiento_registro_id_fkey;
ALTER TABLE encuesta_reclutamiento
    ADD CONSTRAINT encuesta_reclutamiento_registro_id_fkey
        FOREIGN KEY (registro_id) REFERENCES registro(id) ON DELETE SET NULL;

CREATE TABLE IF NOT EXISTS ingresos_puesto (
    id                  SERIAL PRIMARY KEY,
    nombre_candidato    VARCHAR(100),
    empresa             VARCHAR(150),
    no_empleado         VARCHAR(50),
    personal            VARCHAR(100),
    puesto              VARCHAR(150),
    area                VARCHAR(150),
    turno               VARCHAR(50),
    salario             VARCHAR(50),
    pago                VARCHAR(50),
    registro_id         INTEGER REFERENCES registro(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS chatbot_credenciales (
    registro_id         INTEGER PRIMARY KEY REFERENCES registro(id) ON DELETE CASCADE,
    contrasena          TEXT NOT NULL,
    updated_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS incidencias_empleado (
    id                              BIGSERIAL PRIMARY KEY,
    registro_id                     INTEGER NOT NULL REFERENCES registro(id) ON DELETE CASCADE,
    no_empleado                     VARCHAR(50) NOT NULL,
    tipo                            VARCHAR(40) NOT NULL,
    motivo                          TEXT NOT NULL,
    fecha_inicio                    DATE,
    fecha_reingreso                 DATE,
    dias                            SMALLINT CHECK (dias BETWEEN 1 AND 999),
    opcion                          VARCHAR(20),
    hora                            TIME,
    motivos                         JSONB NOT NULL DEFAULT '[]'::jsonb,
    otro_motivo                     TEXT,
    archivo_nombre                  TEXT,
    archivo_mime                    VARCHAR(100),
    archivo_contenido               BYTEA,
    created_at                      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_incidencias_empleado_registro_created
    ON incidencias_empleado (registro_id, created_at, id);

CREATE TABLE IF NOT EXISTS usuarios_acceso (
    id                      SERIAL PRIMARY KEY,
    usuario                 VARCHAR(100) NOT NULL UNIQUE,
    password_hash           TEXT NOT NULL,
    permisos                JSONB NOT NULL DEFAULT '[]'::jsonb,
    area_responsable        VARCHAR(100),
    nombre_notificacion     VARCHAR(150),
    telefono_notificacion   VARCHAR(30),
    created_at              TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at              TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS personal_reclutamiento (
    id              SERIAL PRIMARY KEY,
    nombre          VARCHAR(100) NOT NULL UNIQUE,
    team            INT DEFAULT 1,
    fecha_registro  DATE DEFAULT CURRENT_DATE,
    telefono        VARCHAR(50)
);

ALTER TABLE personal_reclutamiento
    ADD COLUMN IF NOT EXISTS telefono VARCHAR(50);
ALTER TABLE personal_reclutamiento
    ALTER COLUMN telefono TYPE VARCHAR(50);
