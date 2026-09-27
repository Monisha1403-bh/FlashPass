-- FlashPass ticket table
--
-- This wasn't in the uploaded repo (main.py assumes it already exists),
-- so committing it is what makes the project runnable from a clean clone.
--
-- The UNIQUE constraint on request_id is the important part: it's what
-- turns "check if we've seen this request_id, then act" into something
-- the database itself will refuse to violate, even if two requests with
-- the same request_id are being handled at the exact same instant on
-- two different app processes.

CREATE TABLE IF NOT EXISTS tickets (
    ticket_number   INTEGER PRIMARY KEY,
    user_id         TEXT,
    request_id      TEXT,
    sold            BOOLEAN NOT NULL DEFAULT FALSE
);

-- Postgres treats NULL as "distinct from every other NULL", so a plain
-- UNIQUE constraint on request_id is fine even though unsold tickets
-- have request_id = NULL — those NULLs don't collide with each other.
--
-- Postgres has no "ADD CONSTRAINT IF NOT EXISTS" (that only exists for
-- indexes), so this wraps it in a DO block that just ignores the error
-- if the constraint is already there — safe to run this file more than
-- once.
DO $$
BEGIN
    ALTER TABLE tickets
        ADD CONSTRAINT tickets_request_id_key UNIQUE (request_id);
EXCEPTION
    WHEN duplicate_object THEN
        NULL;
END $$;

CREATE INDEX IF NOT EXISTS idx_tickets_sold ON tickets (sold) WHERE sold = FALSE;