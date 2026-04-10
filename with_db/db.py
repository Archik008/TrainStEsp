import asyncio
from sqlalchemy import Column, Integer, String, Boolean
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy import select, delete

DATABASE_URL = "sqlite+aiosqlite:///routes.db"

engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
Base = declarative_base()


# ===================== МОДЕЛЬ =====================

class RouteLog(Base):
    __tablename__ = "routes_log"

    id         = Column(Integer, primary_key=True, autoincrement=True)
    route_name = Column(String, nullable=False)   # "CHM2"
    route_type = Column(String, nullable=False)   # "maneuver" | "train"
    passed     = Column(Boolean, default=False, nullable=False)


# ===================== ИНИЦИАЛИЗАЦИЯ =====================

async def init_db():
    """Создаёт таблицы если их нет. Вызывать один раз при старте."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


# ===================== CRUD =====================

async def db_add_route(route_name: str, route_type: str) -> int:
    """
    Добавляет маршрут в БД.
    Возвращает id записи (можно хранить в active_routes для дальнейшего обновления).
    """
    async with AsyncSessionLocal() as session:
        entry = RouteLog(route_name=route_name, route_type=route_type, passed=False)
        session.add(entry)
        await session.commit()
        await session.refresh(entry)
        return entry.id


async def db_set_passed(db_id: int, passed: bool = True):
    """Обновляет флаг passed (поезд прошёл маршрут)."""
    async with AsyncSessionLocal() as session:
        result = await session.get(RouteLog, db_id)
        if result:
            result.passed = passed
            await session.commit()


async def db_delete_route(db_id: int):
    """Удаляет запись маршрута из БД (при сбросе маршрута)."""
    async with AsyncSessionLocal() as session:
        await session.execute(delete(RouteLog).where(RouteLog.id == db_id))
        await session.commit()


async def db_get_all_routes() -> list[RouteLog]:
    """Возвращает все активные записи (для отладки / будущего журнала)."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(RouteLog))
        return result.scalars().all()


# ===================== СИНХРОННЫЕ ОБЁРТКИ ДЛЯ TKINTER =====================
# Tkinter однопоточный, asyncio — отдельный event loop в фоновом потоке.
# Используем run_coroutine_threadsafe для вызова из GUI-потока.

import threading

_loop: asyncio.AbstractEventLoop | None = None
_loop_thread: threading.Thread | None = None


def _start_loop(loop: asyncio.AbstractEventLoop):
    asyncio.set_event_loop(loop)
    loop.run_forever()


def get_or_create_loop() -> asyncio.AbstractEventLoop:
    """Возвращает фоновый asyncio loop, создавая его при первом вызове."""
    global _loop, _loop_thread
    if _loop is None or not _loop.is_running():
        _loop = asyncio.new_event_loop()
        _loop_thread = threading.Thread(target=_start_loop, args=(_loop,), daemon=True)
        _loop_thread.start()
        # Инициализируем БД в этом же loop
        asyncio.run_coroutine_threadsafe(init_db(), _loop).result(timeout=5)
    return _loop


def sync_add_route(route_name: str, route_type: str) -> int:
    """Синхронный вызов из tkinter: добавить маршрут, вернуть db_id."""
    loop = get_or_create_loop()
    future = asyncio.run_coroutine_threadsafe(
        db_add_route(route_name, route_type), loop
    )
    return future.result(timeout=5)


def sync_set_passed(db_id: int, passed: bool = True):
    """Синхронный вызов: пометить маршрут как пройденный."""
    loop = get_or_create_loop()
    asyncio.run_coroutine_threadsafe(
        db_set_passed(db_id, passed), loop
    ).result(timeout=5)


def sync_delete_route(db_id: int):
    """Синхронный вызов: удалить маршрут из БД."""
    loop = get_or_create_loop()
    asyncio.run_coroutine_threadsafe(
        db_delete_route(db_id), loop
    ).result(timeout=5)


# Автозапуск loop при импорте
get_or_create_loop()
