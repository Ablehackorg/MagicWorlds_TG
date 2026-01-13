import logging
from contextlib import contextmanager
from sqlalchemy.orm import joinedload

from db import SessionLocal
from models import BotSession, EntityPostTask

log = logging.getLogger(__name__)


@contextmanager
def get_session():
    s = SessionLocal()
    try:
        yield s
    except Exception as e:
        s.rollback()
        log.error("DB error: %s", e)
        raise
    finally:
        s.close()


def get_active_bots(session):
    """Возвращает все активные сессии ботов"""
    return session.query(BotSession).filter_by(is_active=True).all()


def get_tasks(session):
    """
    Возвращает все глобально активные задачи публикаций (EntityPostTask)
    с предзагрузкой связанных объектов.
    """
    return (
        session.query(EntityPostTask)
        .options(
            joinedload(EntityPostTask.bot),
            joinedload(EntityPostTask.source),
            joinedload(EntityPostTask.target),
            joinedload(EntityPostTask.times),
        )
        .filter_by(is_global_active=True)
        .all()
    )
