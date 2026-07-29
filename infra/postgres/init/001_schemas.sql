-- Bootstrap schemas. Tables themselves are created by the streaming Postgres
-- sink (gold mirror) and by dbt (staging/intermediate/marts) in later phases.
CREATE SCHEMA IF NOT EXISTS gold;
CREATE SCHEMA IF NOT EXISTS staging;
CREATE SCHEMA IF NOT EXISTS marts;
