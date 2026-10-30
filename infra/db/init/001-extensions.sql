-- ---------------------------------------------------------------
-- Runs once, the first time the database starts (empty data volume).
-- The TimescaleDB image normally enables the extension already; this
-- makes it explicit, so the database is correct even if that changes.
-- Tables are created later with Alembic migrations (M3), not here.
-- ---------------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS timescaledb;
