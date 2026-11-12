"""SQLAlchemy model and request session setup for Dodo applications."""
from pathlib import Path
import re

import sqlalchemy as sa
from sqlalchemy.engine import make_url
from sqlalchemy.orm import declared_attr, declarative_base, relationship, scoped_session, sessionmaker


class Database:
    relationship = staticmethod(relationship)

    def __init__(self, app=None):
        self.session = scoped_session(sessionmaker())
        session = self.session

        class Model:
            query = session.query_property()

            @declared_attr
            def __tablename__(cls):
                return re.sub(r"(?<!^)(?=[A-Z])", "_", cls.__name__).lower()

        self.Model = declarative_base(cls=Model)
        if app is not None:
            self.init_app(app)

    def __getattr__(self, name):
        return getattr(sa, name)

    def init_app(self, app):
        url = make_url(app.config["SQLALCHEMY_DATABASE_URI"])
        if url.drivername.startswith("sqlite") and url.database not in (None, "", ":memory:"):
            path = Path(app.instance_path) / url.database
            path.parent.mkdir(parents=True, exist_ok=True)
            url = url.set(database=str(path))
        self.engine = sa.create_engine(url, **app.config.get("SQLALCHEMY_ENGINE_OPTIONS", {}))
        self.session.configure(bind=self.engine)
        app.teardown.append(self.session.remove)

    def create_all(self):
        self.Model.metadata.create_all(self.engine)

    def Table(self, name, *columns, **kwargs):
        return sa.Table(name, self.Model.metadata, *columns, **kwargs)


class Profiles:
    """Associate local records with shared accounts without trusting matching usernames."""
    def __init__(self, db, model, create):
        from threading import Lock

        self.db, self.model, self.create = db, model, create
        self.lock = Lock()
        self.links = db.Table(
            "dodo_profile_links",
            sa.Column("account_id", sa.Integer, primary_key=True),
            sa.Column("profile_id", sa.ForeignKey(f"{model.__tablename__}.id", ondelete="SET NULL"), unique=True),
        )

    def __call__(self, account):
        with self.lock:
            link = self.db.session.execute(sa.select(self.links.c.profile_id).where(
                self.links.c.account_id == account["id"],
            )).first()
            if link is not None:
                return self.db.session.get(self.model, link.profile_id) if link.profile_id else None
            profile = self.create(account)
            if profile is None:
                return None
            self.db.session.add(profile)
            self.db.session.flush()
            self.db.session.execute(self.links.insert().values(
                account_id=account["id"], profile_id=profile.id,
            ))
            self.db.session.commit()
            return profile

    def username(self, account):
        name = account["username"]
        while self.model.query.filter(sa.func.lower(self.model.username) == name.lower()).first():
            from secrets import token_hex
            name = f'{account["username"]}_{token_hex(3)}'
        return name
