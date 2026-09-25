def normalize_database_url(url: str) -> str:
    """Force the psycopg3 driver.

    Managed Postgres providers (e.g. Railway) inject a plain `postgresql://`
    DATABASE_URL, but this project's driver is psycopg3 (`psycopg[binary]`),
    not the SQLAlchemy default of psycopg2 -- which isn't installed. Rewriting
    the scheme here means no manual URL surgery is needed in deploy config.
    """
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url
