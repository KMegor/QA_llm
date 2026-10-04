CREATE EXTENSION IF NOT EXISTS vector;

-- ===================== PROJECTS =====================
CREATE TABLE projects (
    project_id          VARCHAR(128) PRIMARY KEY,
    name                VARCHAR(256) NOT NULL,
    testops_url         VARCHAR(512),
    testops_project_id  INTEGER,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ===================== USER =====================
CREATE TABLE users (
    user_id     SERIAL PRIMARY KEY,
    login       VARCHAR(64)  NOT NULL UNIQUE,
    password    VARCHAR(256) NOT NULL,
    role        VARCHAR(16)  NOT NULL DEFAULT 'user' CHECK (role IN ('admin', 'user')),
    api_key     VARCHAR(256),                       -- API-токен TestOps пользователя
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE TABLE user_projects (
    user_id     INTEGER      NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    project_id  VARCHAR(128) NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, project_id)
);

CREATE TABLE auth_tokens (
    token         VARCHAR(64) PRIMARY KEY,
    user_id       INTEGER     NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at    TIMESTAMPTZ NOT NULL
);
CREATE INDEX idx_auth_tokens_user_id ON auth_tokens(user_id);

-- ===================== KNOWLEDGE BASE =====================
CREATE TABLE testops_folders (
    project_id  VARCHAR(128) NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    path        VARCHAR(64)  NOT NULL,              -- '1', '1.2', '1.2.3'
    title       TEXT         NOT NULL,
    PRIMARY KEY (project_id, path)
);

CREATE TABLE testops_test_cases (
    test_case_id     UUID PRIMARY KEY,
    project_id       VARCHAR(128) NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    testops_id       INTEGER      NOT NULL,
    case_num         VARCHAR(64)  NOT NULL,         -- X.Y.Z.W, всегда 4 числовые части
    section_x        INTEGER GENERATED ALWAYS AS (split_part(case_num, '.', 1)::int) STORED,
    test_type_z      INTEGER GENERATED ALWAYS AS (split_part(case_num, '.', 3)::int) STORED,
    title            TEXT  NOT NULL DEFAULT '',
    description      TEXT  NOT NULL DEFAULT '',
    steps            JSONB NOT NULL DEFAULT '[]'::jsonb,
    expected_result  TEXT  NOT NULL DEFAULT '',
    -- Скрытые поля: только для поиска, пользователю не показываются, проектом не меняются.
    -- Заполняются только синхронизацией из TestOps.
    expected_result_block TEXT NOT NULL DEFAULT '',  -- блок «Ожидаемый результат» карточки (expectedResult)
    parameters       JSONB NOT NULL DEFAULT '{"names": [], "combinations": []}'::jsonb,  -- «Комбинации параметров»
    tags             TEXT[] NOT NULL DEFAULT '{}',
    workflow         VARCHAR(128) NOT NULL DEFAULT '',
    status           VARCHAR(128) NOT NULL DEFAULT '',
    -- Версия кейса в TestOps (lastModifiedDate, мс от 1970) и её автор на момент синхронизации
    -- или нашего экспорта. По ней синхронизация решает, скачивать ли кейс заново.
    testops_modified_at  BIGINT,
    testops_modified_by  VARCHAR(128),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (project_id, testops_id)
);
CREATE INDEX idx_ttc_filter ON testops_test_cases(project_id, section_x, test_type_z);
CREATE INDEX idx_ttc_case_num ON testops_test_cases(project_id, case_num);

CREATE TABLE testops_shared_steps (
    shared_step_id  UUID PRIMARY KEY,
    project_id      VARCHAR(128) NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    testops_id      INTEGER NOT NULL,
    name            TEXT NOT NULL DEFAULT '',
    content         TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (project_id, testops_id)
);

CREATE TABLE documents (
    document_id     UUID PRIMARY KEY,
    project_id      VARCHAR(128) NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    filename        VARCHAR(512) NOT NULL,
    content_type    VARCHAR(128) NOT NULL DEFAULT 'application/octet-stream',
    size_bytes      BIGINT   NOT NULL,
    sha256          CHAR(64) NOT NULL,
    extracted_text  TEXT     NOT NULL,
    file_data       BYTEA    NOT NULL,
    uploaded_by     INTEGER REFERENCES users(user_id) ON DELETE SET NULL,
    index_status    VARCHAR(16) NOT NULL DEFAULT 'pending'
                    CHECK (index_status IN ('pending', 'indexing', 'indexed', 'failed')),
    index_error     TEXT,
    indexed_at      TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (project_id, filename)
);

CREATE TABLE embeddings (
    embedding_id    BIGSERIAL PRIMARY KEY,
    project_id      VARCHAR(128) NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    test_case_id    UUID REFERENCES testops_test_cases(test_case_id) ON DELETE CASCADE,
    shared_step_id  UUID REFERENCES testops_shared_steps(shared_step_id) ON DELETE CASCADE,
    document_id     UUID REFERENCES documents(document_id) ON DELETE CASCADE,
    content_hash    CHAR(64)     NOT NULL,          -- sha256 текста, поданного в модель
    model           VARCHAR(128) NOT NULL,          -- EMBEDDING_MODEL_NAME на момент индексации
    embedding       vector(2560) NOT NULL,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CHECK (num_nonnulls(test_case_id, shared_step_id, document_id) = 1)
);
CREATE UNIQUE INDEX uq_emb_test_case   ON embeddings(test_case_id)   WHERE test_case_id   IS NOT NULL;
CREATE UNIQUE INDEX uq_emb_shared_step ON embeddings(shared_step_id) WHERE shared_step_id IS NOT NULL;
CREATE UNIQUE INDEX uq_emb_document    ON embeddings(document_id)    WHERE document_id    IS NOT NULL;
CREATE INDEX idx_emb_project ON embeddings(project_id);

-- ===================== CHAT =====================
CREATE TABLE chats (
    chat_id     UUID PRIMARY KEY,
    user_id     INTEGER      NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    project_id  VARCHAR(128) NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    title       VARCHAR(200),
    mode        VARCHAR(16)  NOT NULL DEFAULT 'chat' CHECK (mode IN ('chat', 'generate', 'search')),
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX idx_chats_user ON chats(user_id, updated_at DESC);

CREATE TABLE messages (
    message_id  BIGSERIAL PRIMARY KEY,
    chat_id     UUID NOT NULL REFERENCES chats(chat_id) ON DELETE CASCADE,
    role        VARCHAR(16) NOT NULL CHECK (role IN ('user', 'assistant')),
    kind        VARCHAR(32) NOT NULL DEFAULT 'text' CHECK (kind IN ('text', 'reuse_offer')),
    payload     JSONB,
    content     TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_messages_chat ON messages(chat_id, created_at, message_id);

CREATE TABLE message_images (
    id          BIGSERIAL PRIMARY KEY,
    message_id  BIGINT NOT NULL REFERENCES messages(message_id) ON DELETE CASCADE,
    position    INTEGER NOT NULL DEFAULT 0,
    mime_type   VARCHAR(64) NOT NULL DEFAULT 'image/png',
    data        BYTEA NOT NULL,
    description TEXT,                               -- текстовое описание скриншота для RAG (NULL — ещё не описан)
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_message_images_message ON message_images(message_id, position);

CREATE TABLE test_cases (
    test_case_id         UUID PRIMARY KEY,
    chat_id              UUID NOT NULL REFERENCES chats(chat_id) ON DELETE CASCADE,
    project_id           VARCHAR(128) NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    source_test_case_id  UUID REFERENCES testops_test_cases(test_case_id) ON DELETE SET NULL,
    testops_id           INTEGER,
    case_num             VARCHAR(64),
    title                TEXT  NOT NULL DEFAULT '',
    description          TEXT  NOT NULL DEFAULT '',
    steps                JSONB NOT NULL DEFAULT '[]'::jsonb,
    expected_result      TEXT  NOT NULL DEFAULT '',
    tags                 TEXT[] NOT NULL DEFAULT '{}',
    workflow             VARCHAR(128) NOT NULL DEFAULT '',
    status               VARCHAR(128) NOT NULL DEFAULT '',
    origin_type          VARCHAR(16) NOT NULL DEFAULT 'generated' CHECK (origin_type IN ('generated', 'search')),
    score                DOUBLE PRECISION,
    selected             BOOLEAN NOT NULL DEFAULT FALSE,
    edited_since_search  BOOLEAN NOT NULL DEFAULT FALSE,
    needs_export         BOOLEAN NOT NULL DEFAULT TRUE,
    -- lastModifiedDate версии TestOps, от которой пользователь начал правку (копируется из
    -- базы знаний, когда кейс попадает в чат; обновляется после нашего экспорта).
    -- Перед экспортом сравнивается с текущей датой в TestOps: не совпала — конфликт.
    base_modified_at     BIGINT,
    position             INTEGER NOT NULL DEFAULT 0,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_test_cases_chat ON test_cases(chat_id, position);
CREATE INDEX idx_test_cases_export ON test_cases(chat_id) WHERE needs_export;

-- Заложено на будущее (общие шаги, созданные в чате). В коде пока НЕ используется.
CREATE TABLE chat_shared_steps (
    chat_shared_step_id  UUID PRIMARY KEY,
    chat_id              UUID NOT NULL REFERENCES chats(chat_id) ON DELETE CASCADE,
    project_id           VARCHAR(128) NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    testops_id           INTEGER,                   -- NULL, пока шаг не выгружен в TestOps
    name                 TEXT NOT NULL DEFAULT '',
    content              TEXT NOT NULL,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ===================== SEED =====================
-- project_id и name — из конфигурации проекта по умолчанию (прежний DEFAULT_CONFIG).
-- Настройки TestOps в исходном проекте брались из переменных окружения, в архиве их нет:
-- впишите значения вместо NULL (см. плейсхолдеры в комментариях) до запуска
-- или позже через UPDATE.
INSERT INTO projects (project_id, name, testops_url, testops_project_id)
VALUES ('project_a', '', '', 0);

INSERT INTO users (login, password, role, api_key) VALUES
    ('admin', 'admin',    'admin', ''),
    ('login', 'password', 'user',  '');

INSERT INTO user_projects (user_id, project_id)
SELECT user_id, 'project_a' FROM users;
