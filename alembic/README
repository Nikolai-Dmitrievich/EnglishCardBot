# Alembic Database Migrations

This directory contains the migration history for the project's PostgreSQL database.

## Common Commands

To generate a new migration automatically based on changes in `src/db/models.py`:
```bash
uv run alembic revision --autogenerate -m "describe_your_changes_here"
```

To apply pending migrations to the database:
```bash
uv run alembic upgrade head
```

To rollback the last applied migration:
```bash
uv run alembic downgrade -1
```
