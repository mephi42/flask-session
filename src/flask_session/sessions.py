from copy import deepcopy
import sys
import time
from datetime import datetime
from uuid import uuid4

try:
    import cPickle as pickle
except ImportError:
    import pickle

from flask.sessions import SessionInterface as FlaskSessionInterface
from flask.sessions import SessionMixin
from werkzeug.datastructures import CallbackDict
from itsdangerous import Signer, BadSignature, want_bytes


PY2 = sys.version_info[0] == 2
if not PY2:
    text_type = str
else:
    text_type = unicode


def total_seconds(td):
    return td.days * 60 * 60 * 24 + td.seconds


class ServerSideSession(CallbackDict, SessionMixin):
    """Baseclass for server-side based sessions."""

    def __init__(self, initial=None, sid=None, permanent=None):
        def on_update(self):
            self.modified = True

        CallbackDict.__init__(self, initial, on_update)
        self.sid = sid
        if permanent:
            self.permanent = permanent
        self.modified = False
        self.initial = {} if initial is None else deepcopy(initial)


class RedisSession(ServerSideSession):
    pass


class MemcachedSession(ServerSideSession):
    pass


class FileSystemSession(ServerSideSession):
    pass


class MongoDBSession(ServerSideSession):
    pass


class SqlAlchemySession(ServerSideSession):
    pass


class SessionInterface(FlaskSessionInterface):
    def _generate_sid(self):
        return str(uuid4())

    def _get_signer(self, app):
        if not hasattr(app, "secret_key") or not app.secret_key:
            raise KeyError("SECRET_KEY must be set when SESSION_USE_SIGNER=True")
        return Signer(app.secret_key, salt="flask-session", key_derivation="hmac")

    def _unsign(self, app, sid):
        signer = self._get_signer(app)
        sid_as_bytes = signer.unsign(sid)
        sid = sid_as_bytes.decode()
        return sid

    def _sign(self, app, sid):
        signer = self._get_signer(app)
        sid_as_bytes = want_bytes(sid)
        return signer.sign(sid_as_bytes).decode("utf-8")


class NullSessionInterface(SessionInterface):
    """Used to open a :class:`flask.sessions.NullSession` instance."""

    def open_session(self, app, request):
        return None


class RedisSessionInterface(SessionInterface):
    """Uses the Redis key-value store as a session backend.

    .. versionadded:: 0.2
        The `use_signer` parameter was added.

    :param redis: A ``redis.Redis`` instance.
    :param key_prefix: A prefix that is added to all Redis store keys.
    :param use_signer: Whether to sign the session id cookie or not.
    :param permanent: Whether to use permanent session or not.
    """

    serializer = pickle
    session_class = RedisSession

    def __init__(self, redis, key_prefix, use_signer=False, permanent=True):
        if redis is None:
            from redis import Redis

            redis = Redis()
        self.redis = redis
        self.key_prefix = key_prefix
        self.use_signer = use_signer
        self.permanent = permanent
        self.has_same_site_capability = hasattr(self, "get_cookie_samesite")

    def open_session(self, app, request):
        sid = request.cookies.get(app.config["SESSION_COOKIE_NAME"])
        if not sid:
            sid = self._generate_sid()
            return self.session_class(sid=sid, permanent=self.permanent)
        if self.use_signer:
            try:
                sid = self._unsign(app, sid)
            except BadSignature:
                sid = self._generate_sid()
                return self.session_class(sid=sid, permanent=self.permanent)

        if not PY2 and not isinstance(sid, text_type):
            sid = sid.decode("utf-8", "strict")
        val = self.redis.get(self.key_prefix + sid)
        if val is not None:
            try:
                data = self.serializer.loads(val)
                return self.session_class(data, sid=sid)
            except:
                return self.session_class(sid=sid, permanent=self.permanent)
        return self.session_class(sid=sid, permanent=self.permanent)

    def save_session(self, app, session, response):
        domain = self.get_cookie_domain(app)
        path = self.get_cookie_path(app)
        if not session:
            if session.modified:
                self.redis.delete(self.key_prefix + session.sid)
                response.delete_cookie(
                    app.config["SESSION_COOKIE_NAME"], domain=domain, path=path
                )
            return

        # Modification case.  There are upsides and downsides to
        # emitting a set-cookie header each request.  The behavior
        # is controlled by the :meth:`should_set_cookie` method
        # which performs a quick check to figure out if the cookie
        # should be set or not.  This is controlled by the
        # SESSION_REFRESH_EACH_REQUEST config flag as well as
        # the permanent flag on the session itself.
        # if not self.should_set_cookie(app, session):
        #    return
        conditional_cookie_kwargs = {}
        httponly = self.get_cookie_httponly(app)
        secure = self.get_cookie_secure(app)
        if self.has_same_site_capability:
            conditional_cookie_kwargs["samesite"] = self.get_cookie_samesite(app)
        expires = self.get_expiration_time(app, session)
        val = self.serializer.dumps(dict(session))
        self.redis.setex(
            name=self.key_prefix + session.sid,
            value=val,
            time=total_seconds(app.permanent_session_lifetime),
        )
        if self.use_signer:
            session_id = self._sign(app, session.sid)
        else:
            session_id = session.sid
        response.set_cookie(
            app.config["SESSION_COOKIE_NAME"],
            session_id,
            expires=expires,
            httponly=httponly,
            domain=domain,
            path=path,
            secure=secure,
            **conditional_cookie_kwargs,
        )


class MemcachedSessionInterface(SessionInterface):
    """A Session interface that uses memcached as backend.

    .. versionadded:: 0.2
        The `use_signer` parameter was added.

    :param client: A ``memcache.Client`` instance.
    :param key_prefix: A prefix that is added to all Memcached store keys.
    :param use_signer: Whether to sign the session id cookie or not.
    :param permanent: Whether to use permanent session or not.
    """

    serializer = pickle
    session_class = MemcachedSession

    def __init__(self, client, key_prefix, use_signer=False, permanent=True):
        if client is None:
            client = self._get_preferred_memcache_client()
            if client is None:
                raise RuntimeError("no memcache module found")
        self.client = client
        self.key_prefix = key_prefix
        self.use_signer = use_signer
        self.permanent = permanent
        self.has_same_site_capability = hasattr(self, "get_cookie_samesite")

    def _get_preferred_memcache_client(self):
        servers = ["127.0.0.1:11211"]
        try:
            import pylibmc
        except ImportError:
            pass
        else:
            return pylibmc.Client(servers)

        try:
            import memcache
        except ImportError:
            pass
        else:
            return memcache.Client(servers)

    def _get_memcache_timeout(self, timeout):
        """
        Memcached deals with long (> 30 days) timeouts in a special
        way. Call this function to obtain a safe value for your timeout.
        """
        if timeout > 2592000:  # 60*60*24*30, 30 days
            # See http://code.google.com/p/memcached/wiki/FAQ
            # "You can set expire times up to 30 days in the future. After that
            # memcached interprets it as a date, and will expire the item after
            # said date. This is a simple (but obscure) mechanic."
            #
            # This means that we have to switch to absolute timestamps.
            timeout += int(time.time())
        return timeout

    def open_session(self, app, request):
        sid = request.cookies.get(app.config["SESSION_COOKIE_NAME"])
        if not sid:
            sid = self._generate_sid()
            return self.session_class(sid=sid, permanent=self.permanent)
        if self.use_signer:
            try:
                sid = self._unsign(app, sid)
            except BadSignature:
                sid = self._generate_sid()
                return self.session_class(sid=sid, permanent=self.permanent)

        full_session_key = self.key_prefix + sid
        if PY2 and isinstance(full_session_key, unicode):
            full_session_key = full_session_key.encode("utf-8")
        val = self.client.get(full_session_key)
        if val is not None:
            try:
                if not PY2:
                    val = want_bytes(val)
                data = self.serializer.loads(val)
                return self.session_class(data, sid=sid)
            except:
                return self.session_class(sid=sid, permanent=self.permanent)
        return self.session_class(sid=sid, permanent=self.permanent)

    def save_session(self, app, session, response):
        domain = self.get_cookie_domain(app)
        path = self.get_cookie_path(app)
        full_session_key = self.key_prefix + session.sid
        if PY2 and isinstance(full_session_key, unicode):
            full_session_key = full_session_key.encode("utf-8")
        if not session:
            if session.modified:
                self.client.delete(full_session_key)
                response.delete_cookie(
                    app.config["SESSION_COOKIE_NAME"], domain=domain, path=path
                )
            return

        conditional_cookie_kwargs = {}
        httponly = self.get_cookie_httponly(app)
        secure = self.get_cookie_secure(app)
        if self.has_same_site_capability:
            conditional_cookie_kwargs["samesite"] = self.get_cookie_samesite(app)
        expires = self.get_expiration_time(app, session)
        if not PY2:
            val = self.serializer.dumps(dict(session), 0)
        else:
            val = self.serializer.dumps(dict(session))
        self.client.set(
            full_session_key,
            val,
            self._get_memcache_timeout(total_seconds(app.permanent_session_lifetime)),
        )
        if self.use_signer:
            session_id = self._sign(app, session.sid)
        else:
            session_id = session.sid
        response.set_cookie(
            app.config["SESSION_COOKIE_NAME"],
            session_id,
            expires=expires,
            httponly=httponly,
            domain=domain,
            path=path,
            secure=secure,
            **conditional_cookie_kwargs,
        )


class FileSystemSessionInterface(SessionInterface):
    """Uses the :class:`cachelib.file.FileSystemCache` as a session backend.

    .. versionadded:: 0.2
        The `use_signer` parameter was added.

    :param cache_dir: the directory where session files are stored.
    :param threshold: the maximum number of items the session stores before it
                      starts deleting some.
    :param mode: the file mode wanted for the session files, default 0600
    :param key_prefix: A prefix that is added to FileSystemCache store keys.
    :param use_signer: Whether to sign the session id cookie or not.
    :param permanent: Whether to use permanent session or not.
    """

    session_class = FileSystemSession

    def __init__(
        self, cache_dir, threshold, mode, key_prefix, use_signer=False, permanent=True
    ):
        from cachelib.file import FileSystemCache

        self.cache = FileSystemCache(cache_dir, threshold=threshold, mode=mode)
        self.key_prefix = key_prefix
        self.use_signer = use_signer
        self.permanent = permanent
        self.has_same_site_capability = hasattr(self, "get_cookie_samesite")

    def open_session(self, app, request):
        sid = request.cookies.get(app.config["SESSION_COOKIE_NAME"])
        if not sid:
            sid = self._generate_sid()
            return self.session_class(sid=sid, permanent=self.permanent)
        if self.use_signer:
            try:
                sid = self._unsign(app, sid)
            except BadSignature:
                sid = self._generate_sid()
                return self.session_class(sid=sid, permanent=self.permanent)

        data = self.cache.get(self.key_prefix + sid)
        if data is not None:
            return self.session_class(data, sid=sid)
        return self.session_class(sid=sid, permanent=self.permanent)

    def save_session(self, app, session, response):
        domain = self.get_cookie_domain(app)
        path = self.get_cookie_path(app)
        if not session:
            if session.modified:
                self.cache.delete(self.key_prefix + session.sid)
                response.delete_cookie(
                    app.config["SESSION_COOKIE_NAME"], domain=domain, path=path
                )
            return

        conditional_cookie_kwargs = {}
        httponly = self.get_cookie_httponly(app)
        secure = self.get_cookie_secure(app)
        if self.has_same_site_capability:
            conditional_cookie_kwargs["samesite"] = self.get_cookie_samesite(app)
        expires = self.get_expiration_time(app, session)
        data = dict(session)
        self.cache.set(
            self.key_prefix + session.sid,
            data,
            total_seconds(app.permanent_session_lifetime),
        )
        if self.use_signer:
            session_id = self._sign(app, session.sid)
        else:
            session_id = session.sid
        response.set_cookie(
            app.config["SESSION_COOKIE_NAME"],
            session_id,
            expires=expires,
            httponly=httponly,
            domain=domain,
            path=path,
            secure=secure,
            **conditional_cookie_kwargs,
        )


class MongoDBSessionInterface(SessionInterface):
    """A Session interface that uses mongodb as backend.

    .. versionadded:: 0.2
        The `use_signer` parameter was added.

    :param client: A ``pymongo.MongoClient`` instance.
    :param db: The database you want to use.
    :param collection: The collection you want to use.
    :param key_prefix: A prefix that is added to all MongoDB store keys.
    :param use_signer: Whether to sign the session id cookie or not.
    :param permanent: Whether to use permanent session or not.
    """

    serializer = pickle
    session_class = MongoDBSession

    def __init__(
        self, client, db, collection, key_prefix, use_signer=False, permanent=True
    ):
        if client is None:
            from pymongo import MongoClient

            client = MongoClient()
        self.client = client
        self.store = client[db][collection]
        self.key_prefix = key_prefix
        self.use_signer = use_signer
        self.permanent = permanent
        self.has_same_site_capability = hasattr(self, "get_cookie_samesite")

    def open_session(self, app, request):
        sid = request.cookies.get(app.config["SESSION_COOKIE_NAME"])
        if not sid:
            sid = self._generate_sid()
            return self.session_class(sid=sid, permanent=self.permanent)
        if self.use_signer:
            try:
                sid = self._unsign(app, sid)
            except BadSignature:
                sid = self._generate_sid()
                return self.session_class(sid=sid, permanent=self.permanent)

        store_id = self.key_prefix + sid
        document = self.store.find_one({"id": store_id})
        if document and document.get("expiration") <= datetime.utcnow():
            # Delete expired session
            self.store.remove({"id": store_id})
            document = None
        if document is not None:
            try:
                val = document["val"]
                data = self.serializer.loads(want_bytes(val))
                return self.session_class(data, sid=sid)
            except:
                return self.session_class(sid=sid, permanent=self.permanent)
        return self.session_class(sid=sid, permanent=self.permanent)

    def save_session(self, app, session, response):
        domain = self.get_cookie_domain(app)
        path = self.get_cookie_path(app)
        store_id = self.key_prefix + session.sid
        if not session:
            if session.modified:
                self.store.remove({"id": store_id})
                response.delete_cookie(
                    app.config["SESSION_COOKIE_NAME"], domain=domain, path=path
                )
            return

        conditional_cookie_kwargs = {}
        httponly = self.get_cookie_httponly(app)
        secure = self.get_cookie_secure(app)
        if self.has_same_site_capability:
            conditional_cookie_kwargs["samesite"] = self.get_cookie_samesite(app)
        expires = self.get_expiration_time(app, session)
        val = self.serializer.dumps(dict(session))
        self.store.update(
            {"id": store_id}, {"id": store_id, "val": val, "expiration": expires}, True
        )
        if self.use_signer:
            session_id = self._sign(app, session.sid)
        else:
            session_id = session.sid
        response.set_cookie(
            app.config["SESSION_COOKIE_NAME"],
            session_id,
            expires=expires,
            httponly=httponly,
            domain=domain,
            path=path,
            secure=secure,
            **conditional_cookie_kwargs,
        )


class SqlAlchemySessionInterface(SessionInterface):
    """Uses the Flask-SQLAlchemy from a flask app as a session backend.

    .. versionadded:: 0.2

    :param app: A Flask app instance.
    :param db: A Flask-SQLAlchemy instance.
    :param table: The table name you want to use.
    :param key_prefix: A prefix that is added to all store keys.
    :param use_signer: Whether to sign the session id cookie or not.
    :param permanent: Whether to use permanent session or not.
    """

    serializer = pickle
    session_class = SqlAlchemySession

    def __init__(self, app, db, table, key_prefix, use_signer=False, permanent=True):
        if db is None:
            from flask_sqlalchemy import SQLAlchemy

            db = SQLAlchemy(app)
        self.db = db
        self.key_prefix = key_prefix
        self.use_signer = use_signer
        self.permanent = permanent
        self.has_same_site_capability = hasattr(self, "get_cookie_samesite")

        class Session(self.db.Model):
            __tablename__ = table

            id = self.db.Column(self.db.Integer, primary_key=True)
            session_id = self.db.Column(self.db.String(255), unique=True)
            expiry = self.db.Column(self.db.DateTime)

        class SessionData(self.db.Model):
            __tablename__ = table + "_data"

            id = self.db.Column(self.db.Integer,
                                self.db.ForeignKey(table + ".id",
                                                   ondelete='cascade'),
                                primary_key=True)
            key = self.db.Column(self.db.String(255), primary_key=True)
            value = self.db.Column(self.db.LargeBinary)

        # self.db.create_all()
        self.sql_session_model = Session
        self.sql_session_data_model = SessionData

    def open_session(self, app, request):
        sid = request.cookies.get(app.config["SESSION_COOKIE_NAME"])
        if not sid:
            sid = self._generate_sid()
            return self.session_class(sid=sid, permanent=self.permanent)
        if self.use_signer:
            try:
                sid = self._unsign(app, sid)
            except BadSignature:
                sid = self._generate_sid()
                return self.session_class(sid=sid, permanent=self.permanent)

        store_id = self.key_prefix + sid
        saved_session = self.sql_session_model.query.filter_by(
            session_id=store_id
        ).first()
        if saved_session and \
                saved_session.expiry and \
                saved_session.expiry <= datetime.utcnow():
            # Delete expired session
            self.db.session.delete(saved_session)
            self.db.session.commit()
            saved_session = None
        if saved_session:
            try:
                data = {
                    row.key: self.serializer.loads(want_bytes(row.value))
                    for row in self.sql_session_data_model.query.filter_by(
                        id=saved_session.id)
                }
                return self.session_class(data, sid=sid)
            except:
                return self.session_class(sid=sid, permanent=self.permanent)
        return self.session_class(sid=sid, permanent=self.permanent)

    def save_session(self, app, session, response):
        domain = self.get_cookie_domain(app)
        path = self.get_cookie_path(app)
        store_id = self.key_prefix + session.sid
        saved_session = self.sql_session_model.query.filter_by(
            session_id=store_id
        ).first()
        if not session:
            if session.modified:
                if saved_session:
                    self.db.session.delete(saved_session)
                    self.db.session.commit()
                response.delete_cookie(
                    app.config["SESSION_COOKIE_NAME"], domain=domain, path=path
                )
            return

        conditional_cookie_kwargs = {}
        httponly = self.get_cookie_httponly(app)
        secure = self.get_cookie_secure(app)
        if self.has_same_site_capability:
            conditional_cookie_kwargs["samesite"] = self.get_cookie_samesite(app)
        expires = self.get_expiration_time(app, session)
        if saved_session:
            saved_session.expiry = expires
        else:
            saved_session = self.sql_session_model(session_id=store_id,
                                                   expiry=expires)
            self.db.session.add(saved_session)
            self.db.session.flush()
        self.db.session.query(self.sql_session_data_model).filter(
            self.sql_session_data_model.id == saved_session.id,
            self.sql_session_data_model.key.in_(
                session.initial.keys() - session.keys())).delete()
        for key, value in session.items():
            if session.initial.get(key) == value:
                continue
            self.db.session.merge(
                self.sql_session_data_model(id=saved_session.id,
                                            key=key,
                                            value=self.serializer.dumps(value)))
        self.db.session.commit()
        if self.use_signer:
            session_id = self._sign(app, session.sid)
        else:
            session_id = session.sid
        response.set_cookie(
            app.config["SESSION_COOKIE_NAME"],
            session_id,
            expires=expires,
            httponly=httponly,
            domain=domain,
            path=path,
            secure=secure,
            **conditional_cookie_kwargs,
        )
