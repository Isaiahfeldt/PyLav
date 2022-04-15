from __future__ import annotations

import asyncio
import pathlib

import ujson
from sqlalchemy import event, insert, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from pylav._config import CONFIG_DIR
from pylav.client import Client
from pylav.player_state.models import Base, PlayerEntry


class PlayerStateManager:
    def __init__(self, client: Client, config_folder: pathlib.Path = CONFIG_DIR, sql_connection_string: str = None):
        __database_folder: pathlib.Path = config_folder
        __default_db_name: pathlib.Path = __database_folder / "players.db"
        if not sql_connection_string or "sqlite+aiosqlite:///" in sql_connection_string:
            sql_connection_string = f"sqlite+aiosqlite:///{__default_db_name}"
        self._engine = create_async_engine(
            sql_connection_string, json_deserializer=ujson.loads, json_serializer=ujson.dumps
        )
        self._session = sessionmaker(self._engine, expire_on_commit=False, class_=AsyncSession)
        self._client = client

        event.listen(self._engine.sync_engine, "connect", self.on_db_connect)

    @staticmethod
    def on_db_connect(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA temp_store=2")
        cursor.execute("PRAGMA read_uncommitted=1")
        cursor.execute("PRAGMA optimize")
        cursor.close()

    async def init(self):
        await self.create_tables()

    @property
    def client(self) -> Client:
        return self._client

    @property
    def engine(self) -> AsyncEngine:
        return self._engine

    @property
    def session(self) -> AsyncSession:
        return self._session()

    async def close(self):
        self._engine.dispose()

    async def create_tables(self):
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.commit()

    async def upsert_players(self, players: list[dict[str, float | str | int | None]]):
        async with self.session as session:
            async with session.begin():
                insert_op = await asyncio.to_thread(insert(PlayerEntry).values, players)
                upsert_op = insert_op.on_conflict_do_update(
                    index_elements=["guild_id"],
                    set_=dict(
                        channel_id=insert_op.excluded.channel_id,
                        current=insert_op.excluded.current,
                        paused=insert_op.excluded.paused,
                        repeat_current=insert_op.excluded.repeat_current,
                        repeat_queue=insert_op.excluded.repeat_queue,
                        shuffle=insert_op.excluded.shuffle,
                        auto_playing=insert_op.excluded.auto_playing,
                        playing=insert_op.excluded.playing,
                        position=insert_op.excluded.position,
                        effect_enabled=insert_op.excluded.effect_enabled,
                        volume=insert_op.excluded.volume,
                        queue=insert_op.excluded.queue,
                        metadata=insert_op.excluded.metadata,
                        effects=insert_op.excluded.effects,
                        extras=insert_op.excluded.extras,
                    ),
                )
                await session.execute(upsert_op)

    async def get_player(
        self,
        guild_id: int | None = None,
    ) -> list[dict]:
        if guild_id:
            query = select(PlayerEntry).where(PlayerEntry.guild_id == guild_id)
        else:
            query = select(PlayerEntry)
        async with self.session as session:
            result = await session.execute(query)
            if guild_id:
                result = result.scalars().first()
            else:
                result = result.scalars().all()
            if result:
                return [row.as_dict() for row in result]
        return []