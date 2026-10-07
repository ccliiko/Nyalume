CREATE TABLE IF NOT EXISTS web_sessions (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title TEXT NOT NULL DEFAULT '新的聊天',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS web_sessions_owner ON web_sessions(user_id, created_at);
CREATE TABLE IF NOT EXISTS web_messages (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    session_id UUID NOT NULL REFERENCES web_sessions(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS web_messages_session ON web_messages(session_id, id);
CREATE TABLE IF NOT EXISTS web_memory (
    user_id UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    content TEXT NOT NULL DEFAULT ''
);

ALTER TABLE web_sessions ADD COLUMN IF NOT EXISTS mode TEXT NOT NULL DEFAULT 'chat';
ALTER TABLE web_sessions ADD COLUMN IF NOT EXISTS premise TEXT NOT NULL DEFAULT '';
ALTER TABLE web_sessions ADD COLUMN IF NOT EXISTS story JSONB NOT NULL DEFAULT '{}'::jsonb;
CREATE TABLE IF NOT EXISTS web_daily (
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    day DATE NOT NULL,
    score SMALLINT NOT NULL CHECK (score BETWEEN 0 AND 100),
    profile TEXT NOT NULL,
    blessing TEXT NOT NULL,
    liked BOOLEAN NOT NULL DEFAULT false,
    PRIMARY KEY(user_id,day)
);

ALTER TABLE web_sessions ADD COLUMN IF NOT EXISTS persona TEXT NOT NULL DEFAULT '';
