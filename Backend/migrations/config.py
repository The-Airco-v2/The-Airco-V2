from airco.config import Settings


def build_sqlalchemy_url() -> str:
    return Settings().database_url_sync
