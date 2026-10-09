-- Slice 4 — the worker's job queue: Procrastinate 3.10.0, in its own `procrastinate` schema.
--
-- docs/ARCHITECTURE.md §8 (owner decision D2, 2026-10-06): the mail-triage worker takes its jobs
-- from a PostgreSQL-backed Procrastinate queue in this database instead of `pgmq`. One durable
-- database still: no broker, no second store. The queue is infrastructure, like Supabase's own
-- `pgmq` schema — outside the seven application schemas and outside the DOMAIN.md §7 inventory.
--
-- What it holds: jobs (`triage_message`, one per captured message), their events, periodic-defer
-- bookkeeping and worker heartbeats. Nothing in it is a commercial fact: a job row may be deleted
-- at any time and the sweep re-enqueues whatever is still untriaged.
--
-- Who may touch it: origenlab_worker only. Every Procrastinate function is SECURITY INVOKER (the
-- worker's own connection probe refuses an executable SECURITY DEFINER function anywhere) and
-- pins `search_path = procrastinate, pg_catalog`. anon, authenticated, service_role, PUBLIC and
-- origenlab_api hold nothing here. RLS is enabled on the four tables with one named policy per
-- command for the worker.
--
-- Vendored verbatim from `procrastinate schema --read` (3.10.0) from "-- Enums" to the end. The
-- generated preamble that runs `CREATE EXTENSION plpgsql` is left out: plpgsql is always present
-- and origenlab_owner may not create extensions. apps/worker/tests/test_queue_schema.py fails if
-- the pinned library's schema ever differs from the text below — an upgrade of Procrastinate is
-- a new migration (its own `sql/migrations/*.sql`), never an edit of this file.

-- The owner creates the schema, so it needs CREATE on this database for exactly this statement;
-- 20260906190714 revoked it after the seven schemas and the end of this file revokes it again.
do $$
begin
  execute format('grant create on database %I to origenlab_owner', current_database());
end
$$;

set role origenlab_owner;

create schema procrastinate;
comment on schema procrastinate is
  'OrigenLab V2 — the worker''s Procrastinate job queue (ARCHITECTURE.md §8, D2). Infrastructure, not an application schema: no commercial fact lives here.';

set search_path = procrastinate, pg_catalog;

-- ───────────────────────── vendored: procrastinate 3.10.0 schema ─────────────────────────
-- Enums

CREATE TYPE procrastinate_job_status AS ENUM (
    'todo',  -- The job is queued
    'doing',  -- The job has been fetched by a worker
    'succeeded',  -- The job ended successfully
    'failed',  -- The job ended with an error
    'cancelled', -- The job was cancelled
    'aborting',  -- legacy, not used anymore since v3.0.0
    'aborted'  -- The job was aborted
);

CREATE TYPE procrastinate_job_event_type AS ENUM (
    'deferred',  -- Job created, in todo
    'started',  -- todo -> doing
    'deferred_for_retry',  -- doing -> todo
    'failed',  -- doing -> failed
    'succeeded',  -- doing -> succeeded
    'cancelled', -- todo -> cancelled
    'abort_requested', -- not a state transition, but set in a separate field
    'aborted', -- doing -> aborted (only allowed when abort_requested field is set)
    'scheduled', -- not a state transition, but recording when a task is scheduled for
    'retried' -- Manually retried failed job
);

-- Composite Types

CREATE TYPE procrastinate_job_to_defer_v1 AS (
    queue_name character varying,
    task_name character varying,
    priority integer,
    lock text,
    queueing_lock text,
    args jsonb,
    scheduled_at timestamp with time zone
);

-- Tables

CREATE TABLE procrastinate_workers(
    id bigint PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
    last_heartbeat timestamp with time zone NOT NULL DEFAULT NOW()
);

CREATE TABLE procrastinate_jobs (
    id bigserial PRIMARY KEY,
    queue_name character varying(128) NOT NULL,
    task_name character varying(128) NOT NULL,
    priority integer DEFAULT 0 NOT NULL,
    lock text,
    queueing_lock text,
    args jsonb DEFAULT '{}' NOT NULL,
    status procrastinate_job_status DEFAULT 'todo'::procrastinate_job_status NOT NULL,
    scheduled_at timestamp with time zone NULL,
    attempts integer DEFAULT 0 NOT NULL,
    abort_requested boolean DEFAULT false NOT NULL,
    worker_id bigint REFERENCES procrastinate_workers(id) ON DELETE SET NULL,
    CONSTRAINT check_not_todo_abort_requested CHECK (NOT (status = 'todo' AND abort_requested = true))
);

CREATE TABLE procrastinate_periodic_defers (
    id bigserial PRIMARY KEY,
    task_name character varying(128) NOT NULL,
    defer_timestamp bigint,
    job_id bigint REFERENCES procrastinate_jobs(id) NULL,
    periodic_id character varying(128) NOT NULL DEFAULT '',
    CONSTRAINT procrastinate_periodic_defers_unique UNIQUE (task_name, periodic_id, defer_timestamp)
);

CREATE TABLE procrastinate_events (
    id bigserial PRIMARY KEY,
    job_id bigint NOT NULL REFERENCES procrastinate_jobs ON DELETE CASCADE,
    type procrastinate_job_event_type,
    at timestamp with time zone DEFAULT NOW() NULL
);

-- Constraints & Indices

-- this prevents from having several jobs with the same queueing lock in the "todo" state
CREATE UNIQUE INDEX procrastinate_jobs_queueing_lock_idx_v1 ON procrastinate_jobs (queueing_lock) WHERE status = 'todo';
-- this prevents from having several jobs with the same lock in the "doing" state
CREATE UNIQUE INDEX procrastinate_jobs_lock_idx_v1 ON procrastinate_jobs (lock) WHERE status = 'doing';

-- Index for select_stalled_jobs_by_heartbeat query
CREATE INDEX idx_procrastinate_jobs_worker_not_null ON procrastinate_jobs(worker_id) WHERE worker_id IS NOT NULL AND status = 'doing'::procrastinate_job_status;

CREATE INDEX procrastinate_jobs_queue_name_idx_v1 ON procrastinate_jobs(queue_name);
CREATE INDEX procrastinate_jobs_id_lock_idx_v1 ON procrastinate_jobs (id, lock) WHERE status = ANY (ARRAY['todo'::procrastinate_job_status, 'doing'::procrastinate_job_status]);
CREATE INDEX procrastinate_jobs_priority_idx_v1 ON procrastinate_jobs(priority desc, id asc) WHERE (status = 'todo'::procrastinate_job_status);

CREATE INDEX procrastinate_events_job_id_fkey_v1 ON procrastinate_events(job_id);

CREATE INDEX procrastinate_periodic_defers_job_id_fkey_v1 ON procrastinate_periodic_defers(job_id);

CREATE INDEX idx_procrastinate_workers_last_heartbeat ON procrastinate_workers(last_heartbeat);

-- Functions
CREATE FUNCTION procrastinate_defer_jobs_v1(
    jobs procrastinate_job_to_defer_v1[]
)
    RETURNS bigint[]
    LANGUAGE plpgsql
AS $$
DECLARE
    job_ids bigint[];
BEGIN
    WITH inserted_jobs AS (
        INSERT INTO procrastinate_jobs (queue_name, task_name, priority, lock, queueing_lock, args, scheduled_at)
        SELECT (job).queue_name,
               (job).task_name,
               (job).priority,
               (job).lock,
               (job).queueing_lock,
               (job).args,
               (job).scheduled_at
        FROM unnest(jobs) AS job
        RETURNING id
    )
    SELECT array_agg(id) FROM inserted_jobs INTO job_ids;

    RETURN job_ids;
END;
$$;

CREATE FUNCTION procrastinate_defer_periodic_job_v2(
    _queue_name character varying,
    _lock character varying,
    _queueing_lock character varying,
    _task_name character varying,
    _priority integer,
    _periodic_id character varying,
    _defer_timestamp bigint,
    _args jsonb
)
    RETURNS bigint
    LANGUAGE plpgsql
AS $$
DECLARE
	_job_id bigint;
	_defer_id bigint;
BEGIN
    INSERT
        INTO procrastinate_periodic_defers (task_name, periodic_id, defer_timestamp)
        VALUES (_task_name, _periodic_id, _defer_timestamp)
        ON CONFLICT DO NOTHING
        RETURNING id into _defer_id;

    IF _defer_id IS NULL THEN
        RETURN NULL;
    END IF;

    UPDATE procrastinate_periodic_defers
        SET job_id = (
            SELECT COALESCE((
                SELECT unnest(procrastinate_defer_jobs_v1(
                    ARRAY[
                        ROW(
                            _queue_name,
                            _task_name,
                            _priority,
                            _lock,
                            _queueing_lock,
                            _args,
                            NULL::timestamptz
                        )
                    ]::procrastinate_job_to_defer_v1[]
                ))
            ), NULL)
        )
        WHERE id = _defer_id
        RETURNING job_id INTO _job_id;

    DELETE
        FROM procrastinate_periodic_defers
        USING (
            SELECT id
            FROM procrastinate_periodic_defers
            WHERE procrastinate_periodic_defers.task_name = _task_name
            AND procrastinate_periodic_defers.periodic_id = _periodic_id
            AND procrastinate_periodic_defers.defer_timestamp < _defer_timestamp
            ORDER BY id
            FOR UPDATE
        ) to_delete
        WHERE procrastinate_periodic_defers.id = to_delete.id;

    RETURN _job_id;
END;
$$;

CREATE FUNCTION procrastinate_fetch_job_v2(
    target_queue_names character varying[],
    p_worker_id bigint
)
    RETURNS procrastinate_jobs
    LANGUAGE plpgsql
AS $$
DECLARE
	found_jobs procrastinate_jobs;
BEGIN
    WITH candidate AS (
        SELECT jobs.*
            FROM procrastinate_jobs AS jobs
            WHERE
                -- reject the job if its lock has earlier or higher priority jobs
                NOT EXISTS (
                    SELECT 1
                        FROM procrastinate_jobs AS other_jobs
                        WHERE
                            jobs.lock IS NOT NULL
                            AND other_jobs.lock = jobs.lock
                            AND (
                                -- job with same lock is already running
                                other_jobs.status = 'doing'
                                OR
                                -- job with same lock is waiting and has higher priority (or same priority but was queued first)
                                (
                                    other_jobs.status = 'todo'
                                    AND (
                                        other_jobs.priority > jobs.priority
                                        OR (
                                        other_jobs.priority = jobs.priority
                                        AND other_jobs.id < jobs.id
                                        )
                                    )
                                )
                            )
                )
                AND jobs.status = 'todo'
                AND (target_queue_names IS NULL OR jobs.queue_name = ANY( target_queue_names ))
                AND (jobs.scheduled_at IS NULL OR jobs.scheduled_at <= now())
            ORDER BY jobs.priority DESC, jobs.id ASC LIMIT 1
            FOR UPDATE OF jobs SKIP LOCKED
    )
    UPDATE procrastinate_jobs
        SET status = 'doing', worker_id = p_worker_id
        FROM candidate
        WHERE procrastinate_jobs.id = candidate.id
        RETURNING procrastinate_jobs.* INTO found_jobs;

 RETURN found_jobs;
END;
$$;

CREATE FUNCTION procrastinate_finish_job_v1(job_id bigint, end_status procrastinate_job_status, delete_job boolean)
    RETURNS void
    LANGUAGE plpgsql
AS $$
DECLARE
    _job_id bigint;
BEGIN
    IF end_status NOT IN ('succeeded', 'failed', 'aborted') THEN
        RAISE 'End status should be either "succeeded", "failed" or "aborted" (job id: %)', job_id;
    END IF;
    IF delete_job THEN
        DELETE FROM procrastinate_jobs
        WHERE id = job_id AND status IN ('todo', 'doing')
        RETURNING id INTO _job_id;
    ELSE
        UPDATE procrastinate_jobs
        SET status = end_status,
            abort_requested = false,
            attempts = CASE status
                WHEN 'doing' THEN attempts + 1 ELSE attempts
            END
        WHERE id = job_id AND status IN ('todo', 'doing')
        RETURNING id INTO _job_id;
    END IF;
    IF _job_id IS NULL THEN
        RAISE 'Job was not found or not in "doing" or "todo" status (job id: %)', job_id;
    END IF;
END;
$$;

CREATE FUNCTION procrastinate_cancel_job_v1(job_id bigint, abort boolean, delete_job boolean)
    RETURNS bigint
    LANGUAGE plpgsql
AS $$
DECLARE
    _job_id bigint;
BEGIN
    IF delete_job THEN
        DELETE FROM procrastinate_jobs
        WHERE id = job_id AND status = 'todo'
        RETURNING id INTO _job_id;
    END IF;
    IF _job_id IS NULL THEN
        IF abort THEN
            UPDATE procrastinate_jobs
            SET abort_requested = true,
                status = CASE status
                    WHEN 'todo' THEN 'cancelled'::procrastinate_job_status ELSE status
                END
            WHERE id = job_id AND status IN ('todo', 'doing')
            RETURNING id INTO _job_id;
        ELSE
            UPDATE procrastinate_jobs
            SET status = 'cancelled'::procrastinate_job_status
            WHERE id = job_id AND status = 'todo'
            RETURNING id INTO _job_id;
        END IF;
    END IF;
    RETURN _job_id;
END;
$$;

CREATE FUNCTION procrastinate_retry_job_v1(
    job_id bigint,
    retry_at timestamp with time zone,
    new_priority integer,
    new_queue_name character varying,
    new_lock character varying
) RETURNS void LANGUAGE plpgsql AS $$
DECLARE
    _job_id bigint;
    _abort_requested boolean;
BEGIN
    SELECT abort_requested FROM procrastinate_jobs
    WHERE id = job_id AND status = 'doing'
    FOR UPDATE
    INTO _abort_requested;
    IF _abort_requested THEN
        UPDATE procrastinate_jobs
        SET status = 'failed'::procrastinate_job_status
        WHERE id = job_id AND status = 'doing'
        RETURNING id INTO _job_id;
    ELSE
        UPDATE procrastinate_jobs
        SET status = 'todo'::procrastinate_job_status,
            attempts = attempts + 1,
            scheduled_at = retry_at,
            priority = COALESCE(new_priority, priority),
            queue_name = COALESCE(new_queue_name, queue_name),
            lock = COALESCE(new_lock, lock)
        WHERE id = job_id AND status = 'doing'
        RETURNING id INTO _job_id;
    END IF;

    IF _job_id IS NULL THEN
        RAISE 'Job was not found or not in "doing" status (job id: %)', job_id;
    END IF;
END;
$$;

CREATE FUNCTION procrastinate_retry_job_v2(
    job_id bigint,
    retry_at timestamp with time zone,
    new_priority integer,
    new_queue_name character varying,
    new_lock character varying
) RETURNS void LANGUAGE plpgsql AS $$
DECLARE
    _job_id bigint;
    _abort_requested boolean;
    _current_status procrastinate_job_status;
BEGIN
    SELECT status, abort_requested FROM procrastinate_jobs
    WHERE id = job_id AND status IN ('doing', 'failed')
    FOR UPDATE
    INTO _current_status, _abort_requested;
    IF _current_status = 'doing' AND _abort_requested THEN
        UPDATE procrastinate_jobs
        SET status = 'failed'::procrastinate_job_status
        WHERE id = job_id AND status = 'doing'
        RETURNING id INTO _job_id;
    ELSE
        UPDATE procrastinate_jobs
        SET status = 'todo'::procrastinate_job_status,
            attempts = attempts + 1,
            scheduled_at = retry_at,
            priority = COALESCE(new_priority, priority),
            queue_name = COALESCE(new_queue_name, queue_name),
            lock = COALESCE(new_lock, lock)
        WHERE id = job_id AND status IN ('doing', 'failed')
        RETURNING id INTO _job_id;
    END IF;

    IF _job_id IS NULL THEN
        RAISE 'Job was not found or has an invalid status to retry (job id: %)', job_id;
    END IF;

END;
$$;

CREATE FUNCTION procrastinate_notify_queue_job_inserted_v1()
    RETURNS trigger
    LANGUAGE plpgsql
AS $$
DECLARE
    payload TEXT;
BEGIN
    SELECT json_build_object('type', 'job_inserted', 'job_id', NEW.id)::text INTO payload;
	PERFORM pg_notify('procrastinate_queue_v1#' || NEW.queue_name, payload);
	PERFORM pg_notify('procrastinate_any_queue_v1', payload);
	RETURN NEW;
END;
$$;

CREATE FUNCTION procrastinate_notify_queue_abort_job_v1()
RETURNS trigger
    LANGUAGE plpgsql
AS $$
DECLARE
    payload TEXT;
BEGIN
    SELECT json_build_object('type', 'abort_job_requested', 'job_id', NEW.id)::text INTO payload;
	PERFORM pg_notify('procrastinate_queue_v1#' || NEW.queue_name, payload);
	PERFORM pg_notify('procrastinate_any_queue_v1', payload);
	RETURN NEW;
END;
$$;

CREATE FUNCTION procrastinate_trigger_function_status_events_insert_v1()
    RETURNS trigger
    LANGUAGE plpgsql
AS $$
BEGIN
    INSERT INTO procrastinate_events(job_id, type)
        VALUES (NEW.id, 'deferred'::procrastinate_job_event_type);
	RETURN NEW;
END;
$$;

CREATE FUNCTION procrastinate_trigger_function_status_events_update_v1()
    RETURNS trigger
    LANGUAGE plpgsql
AS $$
BEGIN
    WITH t AS (
        SELECT CASE
            WHEN OLD.status = 'todo'::procrastinate_job_status
                AND NEW.status = 'doing'::procrastinate_job_status
                THEN 'started'::procrastinate_job_event_type
            WHEN OLD.status = 'doing'::procrastinate_job_status
                AND NEW.status = 'todo'::procrastinate_job_status
                THEN 'deferred_for_retry'::procrastinate_job_event_type
            WHEN OLD.status = 'doing'::procrastinate_job_status
                AND NEW.status = 'failed'::procrastinate_job_status
                THEN 'failed'::procrastinate_job_event_type
            WHEN OLD.status = 'doing'::procrastinate_job_status
                AND NEW.status = 'succeeded'::procrastinate_job_status
                THEN 'succeeded'::procrastinate_job_event_type
            WHEN OLD.status = 'todo'::procrastinate_job_status
                AND (
                    NEW.status = 'cancelled'::procrastinate_job_status
                    OR NEW.status = 'failed'::procrastinate_job_status
                    OR NEW.status = 'succeeded'::procrastinate_job_status
                )
                THEN 'cancelled'::procrastinate_job_event_type
            WHEN OLD.status = 'doing'::procrastinate_job_status
                AND NEW.status = 'aborted'::procrastinate_job_status
                THEN 'aborted'::procrastinate_job_event_type
            WHEN OLD.status = 'failed'::procrastinate_job_status
                AND NEW.status = 'todo'::procrastinate_job_status
                THEN 'retried'::procrastinate_job_event_type
            ELSE NULL
        END as event_type
    )
    INSERT INTO procrastinate_events(job_id, type)
        SELECT NEW.id, t.event_type
        FROM t
        WHERE t.event_type IS NOT NULL;
	RETURN NEW;
END;
$$;

CREATE FUNCTION procrastinate_trigger_function_scheduled_events_v1()
    RETURNS trigger
    LANGUAGE plpgsql
AS $$
BEGIN
    INSERT INTO procrastinate_events(job_id, type, at)
        VALUES (NEW.id, 'scheduled'::procrastinate_job_event_type, NEW.scheduled_at);

	RETURN NEW;
END;
$$;

CREATE FUNCTION procrastinate_trigger_abort_requested_events_procedure_v1()
    RETURNS trigger
    LANGUAGE plpgsql
AS $$
BEGIN
    INSERT INTO procrastinate_events(job_id, type)
        VALUES (NEW.id, 'abort_requested'::procrastinate_job_event_type);
    RETURN NEW;
END;
$$;

CREATE FUNCTION procrastinate_unlink_periodic_defers_v1()
    RETURNS trigger
    LANGUAGE plpgsql
AS $$
BEGIN
    UPDATE procrastinate_periodic_defers
    SET job_id = NULL
    WHERE job_id = OLD.id;
    RETURN OLD;
END;
$$;

CREATE FUNCTION procrastinate_register_worker_v1()
    RETURNS TABLE(worker_id bigint)
    LANGUAGE plpgsql
AS $$
BEGIN
    RETURN QUERY
    INSERT INTO procrastinate_workers DEFAULT VALUES
    RETURNING procrastinate_workers.id;
END;
$$;

CREATE FUNCTION procrastinate_unregister_worker_v1(worker_id bigint)
    RETURNS void
    LANGUAGE plpgsql
AS $$
BEGIN
    DELETE FROM procrastinate_workers
    WHERE id = worker_id;
END;
$$;

CREATE FUNCTION procrastinate_update_heartbeat_v1(worker_id bigint)
    RETURNS void
    LANGUAGE plpgsql
AS $$
BEGIN
    UPDATE procrastinate_workers
    SET last_heartbeat = NOW()
    WHERE id = worker_id;
END;
$$;

CREATE FUNCTION procrastinate_prune_stalled_workers_v1(seconds_since_heartbeat float)
    RETURNS TABLE(worker_id bigint)
    LANGUAGE plpgsql
AS $$
BEGIN
    RETURN QUERY
    DELETE FROM procrastinate_workers
    WHERE last_heartbeat < NOW() - (seconds_since_heartbeat || 'SECOND')::INTERVAL
    RETURNING procrastinate_workers.id;
END;
$$;

-- Triggers

CREATE TRIGGER procrastinate_jobs_notify_queue_job_inserted_v1
    AFTER INSERT ON procrastinate_jobs
    FOR EACH ROW WHEN ((new.status = 'todo'::procrastinate_job_status))
    EXECUTE PROCEDURE procrastinate_notify_queue_job_inserted_v1();

CREATE TRIGGER procrastinate_jobs_notify_queue_job_aborted_v1
    AFTER UPDATE OF abort_requested ON procrastinate_jobs
    FOR EACH ROW WHEN ((old.abort_requested = false AND new.abort_requested = true AND new.status = 'doing'::procrastinate_job_status))
    EXECUTE PROCEDURE procrastinate_notify_queue_abort_job_v1();

CREATE TRIGGER procrastinate_trigger_status_events_update_v1
    AFTER UPDATE OF status ON procrastinate_jobs
    FOR EACH ROW
    EXECUTE PROCEDURE procrastinate_trigger_function_status_events_update_v1();

CREATE TRIGGER procrastinate_trigger_status_events_insert_v1
    AFTER INSERT ON procrastinate_jobs
    FOR EACH ROW WHEN ((new.status = 'todo'::procrastinate_job_status))
    EXECUTE PROCEDURE procrastinate_trigger_function_status_events_insert_v1();

CREATE TRIGGER procrastinate_trigger_scheduled_events_v1
    AFTER UPDATE OR INSERT ON procrastinate_jobs
    FOR EACH ROW WHEN ((new.scheduled_at IS NOT NULL AND new.status = 'todo'::procrastinate_job_status))
    EXECUTE PROCEDURE procrastinate_trigger_function_scheduled_events_v1();

CREATE TRIGGER procrastinate_trigger_abort_requested_events_v1
    AFTER UPDATE OF abort_requested ON procrastinate_jobs
    FOR EACH ROW WHEN ((new.abort_requested = true))
    EXECUTE PROCEDURE procrastinate_trigger_abort_requested_events_procedure_v1();

CREATE TRIGGER procrastinate_trigger_delete_jobs_v1
    BEFORE DELETE ON procrastinate_jobs
    FOR EACH ROW EXECUTE PROCEDURE procrastinate_unlink_periodic_defers_v1();
-- ───────────────────────── end of vendored schema ─────────────────────────

-- Pin every function's search_path: the body's unqualified names resolve in this schema whatever
-- the caller's path is, and a caller cannot shadow them.
do $$
declare
  f regprocedure;
begin
  for f in select p.oid::regprocedure from pg_proc p join pg_namespace n on n.oid = p.pronamespace
            where n.nspname = 'procrastinate'
  loop
    execute format('alter function %s set search_path = procrastinate, pg_catalog', f);
  end loop;
end
$$;

-- The worker runs the queue; nobody else touches it. origenlab_owner's global default privileges
-- (20260905230800 §5) already withhold EXECUTE on functions and USAGE on types from PUBLIC.
revoke all on schema procrastinate from public;
grant usage on schema procrastinate to origenlab_worker;
grant select, insert, update, delete on all tables in schema procrastinate to origenlab_worker;
grant usage, select on all sequences in schema procrastinate to origenlab_worker;
grant execute on all functions in schema procrastinate to origenlab_worker;
grant usage on type procrastinate_job_status, procrastinate_job_event_type, procrastinate_job_to_defer_v1
  to origenlab_worker;

alter table procrastinate_jobs enable row level security;
alter table procrastinate_events enable row level security;
alter table procrastinate_periodic_defers enable row level security;
alter table procrastinate_workers enable row level security;

create policy origenlab_worker_select on procrastinate_jobs for select to origenlab_worker using (true);
create policy origenlab_worker_insert on procrastinate_jobs for insert to origenlab_worker with check (true);
create policy origenlab_worker_update on procrastinate_jobs for update to origenlab_worker using (true) with check (true);
create policy origenlab_worker_delete on procrastinate_jobs for delete to origenlab_worker using (true);
create policy origenlab_worker_select on procrastinate_events for select to origenlab_worker using (true);
create policy origenlab_worker_insert on procrastinate_events for insert to origenlab_worker with check (true);
create policy origenlab_worker_update on procrastinate_events for update to origenlab_worker using (true) with check (true);
create policy origenlab_worker_delete on procrastinate_events for delete to origenlab_worker using (true);
create policy origenlab_worker_select on procrastinate_periodic_defers for select to origenlab_worker using (true);
create policy origenlab_worker_insert on procrastinate_periodic_defers for insert to origenlab_worker with check (true);
create policy origenlab_worker_update on procrastinate_periodic_defers for update to origenlab_worker using (true) with check (true);
create policy origenlab_worker_delete on procrastinate_periodic_defers for delete to origenlab_worker using (true);
create policy origenlab_worker_select on procrastinate_workers for select to origenlab_worker using (true);
create policy origenlab_worker_insert on procrastinate_workers for insert to origenlab_worker with check (true);
create policy origenlab_worker_update on procrastinate_workers for update to origenlab_worker using (true) with check (true);
create policy origenlab_worker_delete on procrastinate_workers for delete to origenlab_worker using (true);

reset search_path;
reset role;

do $$
begin
  execute format('revoke create on database %I from origenlab_owner', current_database());
  if has_database_privilege('origenlab_owner', current_database(), 'CREATE') then
    raise exception 'origenlab_owner still holds CREATE on database %', current_database();
  end if;
end
$$;
