-- LOCAL DEVELOPMENT ONLY. The passwords below are throwaway values for a database that lives on
-- your own computer. A deployed database gets its roles, passwords and extensions from the
-- infrastructure code and Key Vault, never from this file.
--
-- energyops_owner owns the tables and is used only to run migrations and the seed script.
-- energyops_app is what the running application uses. It owns nothing and cannot change the
-- schema, and row level security applies to it, which is what keeps one organisation's rows
-- away from another's.

create role energyops_owner login password 'energyops_owner';
create role energyops_app login password 'energyops_app';

create database energyops_dev owner energyops_owner;
create database energyops_test owner energyops_owner;

-- pgvector needs a privileged role to be created. The migrations only check that it is there.
\connect energyops_dev
create extension if not exists vector;
\connect energyops_test
create extension if not exists vector;
