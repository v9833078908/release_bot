import json
import os
from datetime import datetime, timezone

from sqlalchemy import (
    Column, Integer, MetaData, Table, Text, create_engine, func, insert, select, update,
)

metadata = MetaData()

publish_state = Table(
    "publish_state", metadata,
    Column("id", Integer, primary_key=True),
    Column("last_published_sha", Text),
    Column("last_published_at", Text),
    Column("last_seen_prod_sha", Text),
    Column("updated_at", Text),
)

drafts = Table(
    "drafts", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("status", Text, nullable=False),
    Column("trigger", Text, nullable=False),
    Column("from_sha", Text),
    Column("to_sha", Text),
    Column("commit_count", Integer),
    Column("feature_count", Integer),
    Column("raw_commits", Text),
    Column("draft_text", Text),
    Column("admin_msg_id", Integer),
    Column("channel_msg_id", Integer),
    Column("release_no", Integer),
    Column("created_at", Text),
    Column("updated_at", Text),
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, db_path: str, initial_marker_sha: str):
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self.engine = create_engine(f"sqlite:///{db_path}", future=True, connect_args={"timeout": 30})
        metadata.create_all(self.engine)
        with self.engine.begin() as conn:
            cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(drafts)")}
            if "release_no" not in cols:
                conn.exec_driver_sql("ALTER TABLE drafts ADD COLUMN release_no INTEGER")
        with self.engine.begin() as conn:
            pcols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(publish_state)")}
            if "last_seen_prod_sha" not in pcols:
                conn.exec_driver_sql("ALTER TABLE publish_state ADD COLUMN last_seen_prod_sha TEXT")
        with self.engine.begin() as conn:
            if conn.execute(select(publish_state.c.id).where(publish_state.c.id == 1)).first() is None:
                conn.execute(insert(publish_state).values(
                    id=1, last_published_sha=initial_marker_sha,
                    last_published_at=_now(), updated_at=_now()))

    def get_marker(self) -> str:
        with self.engine.begin() as conn:
            return conn.execute(select(publish_state.c.last_published_sha)
                                .where(publish_state.c.id == 1)).scalar_one()

    def get_last_published_at(self) -> str:
        with self.engine.begin() as conn:
            return conn.execute(select(publish_state.c.last_published_at)
                                .where(publish_state.c.id == 1)).scalar_one()

    def get_last_seen_prod_sha(self) -> str | None:
        with self.engine.begin() as conn:
            return conn.execute(select(publish_state.c.last_seen_prod_sha)
                                .where(publish_state.c.id == 1)).scalar_one()

    def set_last_seen_prod_sha(self, sha: str) -> None:
        with self.engine.begin() as conn:
            conn.execute(update(publish_state)
                         .where(publish_state.c.id == 1)
                         .values(last_seen_prod_sha=sha, updated_at=_now()))

    def has_pending(self) -> bool:
        with self.engine.begin() as conn:
            return conn.execute(select(drafts.c.id)
                                .where(drafts.c.status.in_(("pending", "publishing")))).first() is not None

    def get_pending(self) -> dict | None:
        """The single draft awaiting review or mid-publish (pending/publishing), if any."""
        with self.engine.begin() as conn:
            row = conn.execute(select(drafts)
                               .where(drafts.c.status.in_(("pending", "publishing")))
                               .order_by(drafts.c.id.desc())).first()
            return dict(row._mapping) if row else None

    def next_release_no(self) -> int:
        with self.engine.begin() as conn:
            m = conn.execute(select(func.max(drafts.c.release_no))
                             .where(drafts.c.status.in_(("published", "publishing")))).scalar()
            return (m or 0) + 1

    def claim_for_publish(self, draft_id: int) -> int | None:
        """Atomically move pending -> publishing and reserve a globally unique
        release number. Refuses if another draft is already mid-publish."""
        with self.engine.begin() as conn:
            other = conn.execute(select(drafts.c.id)
                                 .where(drafts.c.status == "publishing",
                                        drafts.c.id != draft_id)).first()
            if other is not None:
                return None
            m = conn.execute(select(func.max(drafts.c.release_no))
                             .where(drafts.c.status.in_(("published", "publishing")))).scalar()
            release_no = (m or 0) + 1
            res = conn.execute(update(drafts)
                               .where(drafts.c.id == draft_id, drafts.c.status == "pending")
                               .values(status="publishing", release_no=release_no, updated_at=_now()))
            return release_no if res.rowcount else None

    def unclaim(self, draft_id: int) -> None:
        with self.engine.begin() as conn:
            conn.execute(update(drafts)
                         .where(drafts.c.id == draft_id, drafts.c.status == "publishing")
                         .values(status="pending", updated_at=_now()))

    def create_draft(self, *, status, trigger, from_sha, to_sha, commit_count,
                     feature_count, raw_commits, draft_text) -> int:
        with self.engine.begin() as conn:
            res = conn.execute(insert(drafts).values(
                status=status, trigger=trigger, from_sha=from_sha, to_sha=to_sha,
                commit_count=commit_count, feature_count=feature_count,
                raw_commits=json.dumps(raw_commits), draft_text=draft_text,
                created_at=_now(), updated_at=_now()))
            return res.inserted_primary_key[0]

    def get_draft(self, draft_id: int) -> dict | None:
        with self.engine.begin() as conn:
            row = conn.execute(select(drafts).where(drafts.c.id == draft_id)).first()
            return dict(row._mapping) if row else None

    def set_admin_msg(self, draft_id: int, msg_id: int) -> None:
        self._patch(draft_id, admin_msg_id=msg_id)

    def set_draft_text(self, draft_id: int, text: str) -> None:
        self._patch(draft_id, draft_text=text)

    def cancel(self, draft_id: int) -> bool:
        with self.engine.begin() as conn:
            res = conn.execute(update(drafts)
                               .where(drafts.c.id == draft_id, drafts.c.status == "pending")
                               .values(status="cancelled", updated_at=_now()))
            return res.rowcount > 0

    def _patch(self, draft_id: int, **values) -> None:
        values["updated_at"] = _now()
        with self.engine.begin() as conn:
            conn.execute(update(drafts).where(drafts.c.id == draft_id).values(**values))

    def publish(self, draft_id: int, *, to_sha: str, channel_msg_id: int) -> bool:
        with self.engine.begin() as conn:
            res = conn.execute(update(drafts)
                               .where(drafts.c.id == draft_id, drafts.c.status.in_(("pending", "publishing")))
                               .values(status="published", channel_msg_id=channel_msg_id,
                                       updated_at=_now()))
            if res.rowcount == 0:
                return False
            conn.execute(update(publish_state).where(publish_state.c.id == 1).values(
                last_published_sha=to_sha, last_published_at=_now(), updated_at=_now()))
            return True
