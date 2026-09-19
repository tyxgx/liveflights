-- Airflow's metadata DB lives on this same Postgres instance (own database,
-- own user) rather than a separate container — Airflow's metadata store is
-- small and this machine already runs 7+ containers (see docker-compose.yml)
-- so adding another Postgres just for Airflow wasn't worth the RAM.
CREATE USER airflow WITH PASSWORD 'airflow';
CREATE DATABASE airflow OWNER airflow;
GRANT ALL PRIVILEGES ON DATABASE airflow TO airflow;
