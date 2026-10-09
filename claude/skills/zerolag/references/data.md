# Data layer: Prisma, PostgreSQL, Neon, shared databases

Verified against official docs on 2026-10-09 (Prisma 7.10 stable, Prisma 8 release candidate, PostgreSQL 18, Neon). Database access follows the host repository's rules: fixture or isolated databases by default; production reads or writes only with explicit approval for the named operation.

## Contents
1. Detect the stack first
2. Query shapes
3. Transactions
4. Connection pools and Neon
5. PostgreSQL plans, indexes, locks
6. Shared database across apps
7. Read-only diagnostic SQL
8. Neon MCP safety

## 1. Detect the stack first

| Prisma | Signals | Notes |
|---|---|---|
| 6 | `@prisma/client` 6.x, `prisma-client-js`, URL params like `connection_limit` | Security fixes only until 2026-11-19 |
| 7 | `@prisma/client` 7.x, generator `prisma-client` with `output`, a driver adapter (`@prisma/adapter-pg`, `@prisma/adapter-neon`), `prisma.config.ts` | Pool belongs to the driver adapter; use ≥ 7.10 (pool leak fixes in 7.9 and 7.10) |
| 8 (RC) | `@prisma/orm-postgres`, `contract.prisma` | Different API; no `migrate dev` or `generate` |

- Never run an unpinned `npx prisma` or `npm i prisma`: the `latest` tag now installs the Prisma 8 CLI, which ignores `schema.prisma`. Use the project's local binary (`./node_modules/.bin/prisma` or `npx --no prisma`) ([release status](https://www.prisma.io/docs/orm/release-status)).
- Record the database driver path: Prisma adapter, `@neondatabase/serverless` (HTTP or WebSocket), plain `pg`, or another ORM.
- Prisma Optimize no longer exists; Query Insights only works with Prisma Postgres, not Neon. On Neon use `pg_stat_statements` and Neon Monitoring ([Query Insights](https://www.prisma.io/docs/query-insights)).

## 2. Query shapes

- Count queries per journey before optimizing them: Prisma query events (`log: [{ emit: 'event', level: 'query' }]`, redact parameters) in a disposable branch, `pg_stat_statements` deltas on a fixture database, or the `prisma:engine:db_query` OpenTelemetry spans. Prisma 7.1+ can tag queries with SQL comments ([logging](https://www.prisma.io/docs/orm/v7/prisma-client/observability-and-logging/logging), [SQL comments](https://www.prisma.io/docs/orm/v7/prisma-client/observability-and-logging/sql-comments)).
- N+1: a query inside a loop or per row/component. Batch with one `findMany({ where: { id: { in: ids } } })` or a nested read; keep the authorization filter in the batched query.
- Select only the fields the screen renders; bound every `findMany` (`take`); avoid deep `include` trees that multiply rows and payload.
- `relationLoadStrategy: 'join'` needs the `relationJoins` Preview feature (still Preview in 7.10). Enabling the flag makes `join` the default for every relation read: treat it as a global change and measure both strategies ([relation load strategies](https://www.prisma.io/docs/orm/v7/prisma-client/queries/relation-queries#relation-load-strategies-preview)).
- Pagination: cursor-based (`cursor` + `skip: 1` + `orderBy` on a unique column) for deep lists; large `OFFSET` still computes the skipped rows ([pagination](https://www.prisma.io/docs/orm/v7/prisma-client/queries/pagination), [LIMIT/OFFSET](https://www.postgresql.org/docs/current/queries-limit.html)).
- Bulk writes: `createMany`, `createManyAndReturn` (5.14+), `updateManyAndReturn` (6.2+, PostgreSQL); batch large imports (for example 1,000 rows per call); preserve validation, uniqueness and permission checks.
- Counts and aggregates: compute in SQL (`count`, `groupBy`, `_sum`) instead of loading rows into JavaScript; avoid an extra `count()` on every page view when the UI does not need an exact total.

## 3. Transactions

- Interactive transaction defaults: `maxWait` 2,000 ms, `timeout` 5,000 ms. Keep them short: no network calls, emails or slow queries inside ([transactions](https://www.prisma.io/docs/orm/v7/prisma-client/queries/transactions)).
- `$transaction([...])` runs a batch sequentially in one transaction; it does not parallelize.
- Long transactions hold locks and pool connections: on a shared database they slow every app.

## 4. Connection pools and Neon

**Prisma pools**
- Prisma 6 (Rust engine): `connection_limit` defaults to CPUs × 2 + 1, `pool_timeout` 10 s, `connect_timeout` 5 s ([v6 pool](https://www.prisma.io/docs/orm/v6/prisma-client/setup-and-configuration/databases-connections/connection-pool)).
- Prisma 7: the driver adapter owns the pool; URL parameters like `connection_limit` are ignored. With `pg`: `max` 10, `connectionTimeoutMillis` 0 = wait forever, `idleTimeoutMillis` 10 s. Set an explicit connection timeout so exhaustion fails fast ([v7 pool](https://www.prisma.io/docs/orm/v7/prisma-client/setup-and-configuration/databases-connections/connection-pool), [pg Pool](https://node-postgres.com/apis/pool)).
- Vercel Fluid compute: one module-scope `pg.Pool`, `attachDatabasePool(pool)` from `@vercel/functions`, then `new PrismaClient({ adapter: new PrismaPg(pool) })` ([Prisma on Vercel](https://www.prisma.io/docs/orm/v7/prisma-client/deployment/serverless/deploy-to-vercel)).
- One client per process (the `globalThis` singleton in development); never construct a client or pool per request.
- Pool size guidance differs between vendors (Vercel: not 1; Neon: small per instance; node-postgres: 10 behind a pooler): measure pool wait instead of guessing.

**Neon** ([pooling](https://neon.com/docs/connect/connection-pooling), [compatibility](https://neon.com/docs/reference/compatibility))
- Pooled URLs contain `-pooler` (PgBouncer, transaction mode, `max_client_conn` 10,000, `default_pool_size` = 0.9 × `max_connections` per role and database, `query_wait_timeout` 120 s).
- Not supported through the pooler: `SET`/`RESET` (session), `LISTEN`/`NOTIFY`, `WITH HOLD` cursors, SQL `PREPARE`/`DEALLOCATE`, session advisory locks, `LOAD`. Protocol-level prepared statements work; do not add `pgbouncer=true` ([Prisma + PgBouncer](https://www.prisma.io/docs/orm/v7/prisma-client/setup-and-configuration/databases-connections/pgbouncer)).
- Migrations and the Prisma CLI use the direct (non-pooler) URL: the schema engine needs one session and advisory locks.
- `max_connections` ≈ 104 at 0.25 CU, 209 at 0.5, 419 at 1, 839 at 2, 1,678 at 4, 3,357 at 8, capped at 4,000; autoscaling uses `min(max CU, 8 × min CU)`.
- Driver choice: with Vercel Fluid, Neon recommends TCP `pg` with a pool and `attachDatabasePool`; the HTTP driver (`neon()`) suits one-shot queries in classic serverless or edge; WebSocket `Pool`/`Client` for interactive transactions ([Vercel connection methods](https://neon.com/docs/guides/vercel-connection-methods), [serverless driver](https://neon.com/docs/serverless/serverless-driver)).
- Cold starts: computes scale to zero after 5 minutes idle by default; activation takes a few hundred milliseconds and caches start cold. Compare first-request-after-idle with warm runs. Changing suspend timeouts or compute size changes cost: propose, never apply ([scale to zero](https://neon.com/docs/introduction/scale-to-zero), [compute lifecycle](https://neon.com/docs/introduction/compute-lifecycle)).
- Regions: Neon runs on AWS (`us-east-1`, `us-east-2`, `us-west-2`, `eu-central-1`, `eu-west-2`, `ap-southeast-1`, `ap-southeast-2`, `sa-east-1`); the region is fixed at project creation. Same-region Vercel pairs: `iad1`↔us-east-1, `cle1`↔us-east-2, `pdx1`↔us-west-2, `fra1`↔eu-central-1, `lhr1`↔eu-west-2, `sin1`↔ap-southeast-1, `syd1`↔ap-southeast-2, `gru1`↔sa-east-1. New Vercel projects default to `iad1` ([Neon regions](https://neon.com/docs/introduction/regions), [Vercel regions](https://vercel.com/docs/regions)).

## 5. PostgreSQL plans, indexes, locks

- `EXPLAIN (ANALYZE, BUFFERS)` executes the statement: SELECT only, on a fixture or authorized database. PostgreSQL 18 includes BUFFERS with ANALYZE automatically. For data-modifying statements use `BEGIN; EXPLAIN ANALYZE …; ROLLBACK;` on a fixture database only (sequences still advance, locks are taken) ([EXPLAIN](https://www.postgresql.org/docs/current/sql-explain.html)).
- `EXPLAIN (GENERIC_PLAN)` (PG16+) plans `$1`-style SQL, as logged by Prisma, without executing it; it cannot be combined with ANALYZE. `SERIALIZE` (PG17+) shows output conversion cost. On Neon, `EXPLAIN (ANALYZE, BUFFERS, PREFETCH, FILECACHE)` shows cache hits ([Neon extensions](https://neon.com/docs/extensions/neon)).
- Read plans for: estimated vs actual rows, sequential scans on large tables, sorts or hashes spilling to disk, nested loops with many iterations, rows removed by filter. Plans on toy data mislead: use representative row counts.
- Indexes: match equality filters, then range filters and `ORDER BY ... LIMIT`; consider partial and covering indexes. PG18 can skip-scan a multicolumn index whose leading column has few distinct values: check existing indexes first. Index creation is a schema change needing approval; on live tables use `CREATE INDEX CONCURRENTLY` (not inside a transaction; a failure leaves an INVALID index to drop). Prisma Migrate supports it from 7.4.0, in its own migration ([CREATE INDEX](https://www.postgresql.org/docs/current/sql-createindex.html), [multicolumn indexes](https://www.postgresql.org/docs/current/indexes-multicolumn.html)).
- Statistics windows: Neon resets `pg_stat_statements` and all cumulative statistics whenever the compute suspends or restarts. Check the window (query 1 below) before trusting `idx_scan = 0` or call counts ([statistics collection](https://neon.com/docs/reference/compatibility#statistics-collection)).
- Locks and idle transactions: find blockers with `pg_blocking_pids()`; long `idle in transaction` sessions hold locks and connections. Neon sets `idle_in_transaction_session_timeout` to 5 minutes. Guardrails (`statement_timeout`, `lock_timeout`, `transaction_timeout` on PG17+) belong per role or per session, not server-wide ([client settings](https://www.postgresql.org/docs/current/runtime-config-client.html)).

## 6. Shared database across apps

Audit each app alone, then the shared database once:
1. **Connection budget**: for each app, instances × pool `max` (plus cron jobs, scripts, Studio, MCP sessions). Pooled connections share `default_pool_size` per role and database; direct connections count against `max_connections`.
2. **Attribution**: one database role and a distinct `application_name` per app make `pg_stat_activity` and `pg_stat_statements` attributable (creating roles is an approved change).
3. **Bound every wait**: pool acquire timeout, Prisma `maxWait`/`timeout`, per-role `statement_timeout`/`lock_timeout`, so one app's spike fails fast instead of queuing everyone.
4. **Migrations**: one app owns the schema; run migrations over the direct URL with `lock_timeout`; an `ACCESS EXCLUSIVE` lock blocks every app.
5. **Heavy readers**: exports, reports and dashboards can move to a Neon read replica (separate compute, asynchronous: never read your own writes there).
6. **Versions and regions**: different Prisma majors have different pool defaults; every Vercel project must run in the database's region.

## 7. Read-only diagnostic SQL

Run against a fixture or authorized database, inside a read-only transaction with timeouts. Show only aggregates and the first characters of query text; never store raw query text (it can contain literals).

```sql
BEGIN TRANSACTION READ ONLY;
SET LOCAL statement_timeout = '5s';
SET LOCAL lock_timeout = '1s';
-- one query below
ROLLBACK;
```

Seeing other roles' sessions and statements needs `pg_read_all_stats` (part of `pg_monitor`). `pg_stat_statements` must already be installed: `CREATE EXTENSION` is a schema change.

```sql
-- 1. Statistics window (on Neon: since the compute last started)
SELECT now() - pg_postmaster_start_time() AS stats_window, d.stats_reset, d.deadlocks, d.temp_bytes
FROM pg_stat_database d WHERE d.datname = current_database();

-- 2. Statements by total execution time (PG13+, pg_stat_statements)
SELECT r.rolname, s.calls, round(s.total_exec_time) AS total_ms, round(s.mean_exec_time::numeric, 1) AS mean_ms,
       s.rows / NULLIF(s.calls, 0) AS rows_per_call, s.shared_blks_read, s.temp_blks_written,
       left(regexp_replace(s.query, '\s+', ' ', 'g'), 100) AS sql_head
FROM pg_stat_statements s JOIN pg_roles r ON r.oid = s.userid
WHERE s.dbid = (SELECT oid FROM pg_database WHERE datname = current_database())
ORDER BY s.total_exec_time DESC LIMIT 20;

-- 3. Database time per role (= per app when each app has its own role)
SELECT r.rolname, sum(s.calls) AS calls, round(sum(s.total_exec_time)) AS total_ms
FROM pg_stat_statements s JOIN pg_roles r ON r.oid = s.userid
GROUP BY r.rolname ORDER BY total_ms DESC;

-- 4. Connections by role, application and state, against the budget
SELECT usename, application_name, state, count(*) AS conns, max(now() - state_change) AS oldest_in_state
FROM pg_stat_activity WHERE backend_type = 'client backend' GROUP BY 1, 2, 3 ORDER BY conns DESC;
SELECT current_setting('max_connections')::int - current_setting('superuser_reserved_connections')::int AS usable,
       count(*) FILTER (WHERE backend_type = 'client backend') AS client_backends
FROM pg_stat_activity;

-- 5. Lock waits and their blockers (PG12+)
WITH waiting AS MATERIALIZED (
  SELECT pid, usename, application_name, wait_event, query_start FROM pg_stat_activity WHERE wait_event_type = 'Lock')
SELECT w.pid, w.usename, w.application_name, w.wait_event, now() - w.query_start AS waiting_for,
       b.pid AS blocking_pid, b.usename AS blocking_role, b.application_name AS blocking_app, b.state AS blocking_state,
       now() - b.xact_start AS blocking_xact_age
FROM waiting w CROSS JOIN LATERAL unnest(pg_blocking_pids(w.pid)) AS bp(pid)
LEFT JOIN pg_stat_activity b ON b.pid = bp.pid
ORDER BY waiting_for DESC;

-- 6. Idle-in-transaction sessions and old transactions
SELECT pid, usename, application_name, state, now() - xact_start AS xact_age, now() - state_change AS in_state_for
FROM pg_stat_activity
WHERE backend_type = 'client backend' AND xact_start IS NOT NULL
  AND (state LIKE 'idle in transaction%' OR now() - xact_start > interval '30 seconds')
ORDER BY xact_start LIMIT 20;

-- 7. Indexes never scanned in this window (never drop on this alone: stats resets, replicas, FK indexes)
SELECT s.relname AS table_name, s.indexrelname AS index_name, s.idx_scan,
       pg_size_pretty(pg_relation_size(s.indexrelid)) AS index_size
FROM pg_stat_user_indexes s JOIN pg_index i ON i.indexrelid = s.indexrelid
WHERE s.idx_scan = 0 AND NOT i.indisunique
  AND NOT EXISTS (SELECT 1 FROM pg_constraint c WHERE c.conindid = s.indexrelid)
ORDER BY pg_relation_size(s.indexrelid) DESC LIMIT 20;

-- 8. Tables read mostly by sequential scans
SELECT relname, seq_scan, seq_tup_read, round(seq_tup_read::numeric / NULLIF(seq_scan, 0)) AS rows_per_seq_scan,
       idx_scan, n_live_tup
FROM pg_stat_user_tables WHERE seq_scan > 0 ORDER BY seq_tup_read DESC LIMIT 20;

-- 9. Dead tuples and stale statistics
SELECT relname, n_live_tup, n_dead_tup, round(100.0 * n_dead_tup / NULLIF(n_live_tup + n_dead_tup, 0), 1) AS dead_pct,
       n_mod_since_analyze, last_autovacuum, last_autoanalyze
FROM pg_stat_user_tables ORDER BY n_dead_tup DESC LIMIT 20;
```

Sources: [pg_stat_statements](https://www.postgresql.org/docs/current/pgstatstatements.html), [statistics views](https://www.postgresql.org/docs/current/monitoring-stats.html), [pg_blocking_pids](https://www.postgresql.org/docs/current/functions-info.html).

## 8. Neon MCP safety

- Connect in read-only mode (`https://mcp.neon.tech/mcp?readonly=true`, or choose read-only at OAuth). Neon positions its MCP server for development and testing, not production databases ([Neon MCP](https://neon.com/docs/ai/neon-mcp-server)).
- `explain_sql_statement` executes the statement when `analyze` is true: SELECT only.
- `prepare_query_tuning` creates a temporary branch: a full copy of the data, including any personal data. Ask before using it.
- Never run `complete_query_tuning` with `apply_changes`, `run_sql` writes, branch resets or compute changes without explicit approval of that exact operation.
- Neon Console → Monitoring → Query performance shows top queries without installing anything; the Pooler graphs show client WAITING counts and max wait ([monitoring](https://neon.com/docs/introduction/monitor-query-performance)).
