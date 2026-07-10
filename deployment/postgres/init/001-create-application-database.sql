CREATE ROLE agents LOGIN PASSWORD 'local-development-only';
CREATE DATABASE agents OWNER agents;

\connect agents
CREATE EXTENSION IF NOT EXISTS vector;
