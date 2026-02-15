"""
libSQL backend using the native libsql Python package (Rust bindings).
"""
import datetime
import decimal
import warnings
from collections.abc import Mapping
from itertools import chain, tee

import libsql as Database

# The libsql package only exposes a single Error class. Django's database
# backend infrastructure expects a full PEP 249 exception hierarchy, so we
# create the missing classes as aliases of Database.Error.
for _exc_name in (
    "DatabaseError",
    "DataError",
    "InterfaceError",
    "InternalError",
    "IntegrityError",
    "OperationalError",
    "ProgrammingError",
    "NotSupportedError",
):
    if not hasattr(Database, _exc_name):
        setattr(Database, _exc_name, type(_exc_name, (Database.Error,), {}))

from django.core.exceptions import ImproperlyConfigured
from django.db import IntegrityError
from django.db.backends.base.base import BaseDatabaseWrapper
from django.utils.asyncio import async_unsafe
from django.utils.dateparse import parse_date, parse_datetime, parse_time
from django.utils.regex_helper import _lazy_re_compile

from .client import DatabaseClient
from .creation import DatabaseCreation
from .features import DatabaseFeatures
from .introspection import DatabaseIntrospection
from .operations import DatabaseOperations
from .schema import DatabaseSchemaEditor


def adapt_date(val):
    return val.isoformat()


def adapt_datetime(val):
    return val.isoformat(" ")


def adapt_time(val):
    return val.isoformat()


# Type adapters: convert Python types to SQLite-compatible values on input.
# The libsql package does not support register_adapter/register_converter,
# so we handle type conversion manually in SQLiteCursorWrapper.
ADAPTERS = {
    decimal.Decimal: str,
    # datetime.datetime must come before datetime.date because
    # datetime is a subclass of date.
    datetime.datetime: adapt_datetime,
    datetime.date: adapt_date,
    datetime.time: adapt_time,
}


def _adapt_params(params):
    """Adapt parameter values for libsql (which lacks register_adapter)."""
    if params is None:
        return None
    if isinstance(params, Mapping):
        return {k: _adapt_value(v) for k, v in params.items()}
    return tuple(_adapt_value(v) for v in params)


def _adapt_value(value):
    """Adapt a single value using our ADAPTERS table."""
    for type_, adapter in ADAPTERS.items():
        if isinstance(value, type_):
            return adapter(value)
    return value


class DatabaseWrapper(BaseDatabaseWrapper):
    vendor = "libsql"
    display_name = "libSQL"

    data_types = {
        "AutoField": "integer",
        "BigAutoField": "integer",
        "BinaryField": "BLOB",
        "BooleanField": "bool",
        "CharField": "varchar(%(max_length)s)",
        "DateField": "date",
        "DateTimeField": "datetime",
        "DecimalField": "decimal",
        "DurationField": "bigint",
        "FileField": "varchar(%(max_length)s)",
        "FilePathField": "varchar(%(max_length)s)",
        "FloatField": "real",
        "IntegerField": "integer",
        "BigIntegerField": "bigint",
        "IPAddressField": "char(15)",
        "GenericIPAddressField": "char(39)",
        "JSONField": "text",
        "OneToOneField": "integer",
        "PositiveBigIntegerField": "bigint unsigned",
        "PositiveIntegerField": "integer unsigned",
        "PositiveSmallIntegerField": "smallint unsigned",
        "SlugField": "varchar(%(max_length)s)",
        "SmallAutoField": "integer",
        "SmallIntegerField": "smallint",
        "TextField": "text",
        "TimeField": "time",
        "UUIDField": "char(32)",
    }
    data_type_check_constraints = {
        "PositiveBigIntegerField": '"%(column)s" >= 0',
        "JSONField": '(JSON_VALID("%(column)s") OR "%(column)s" IS NULL)',
        "PositiveIntegerField": '"%(column)s" >= 0',
        "PositiveSmallIntegerField": '"%(column)s" >= 0',
    }
    data_types_suffix = {
        "AutoField": "AUTOINCREMENT",
        "BigAutoField": "AUTOINCREMENT",
        "SmallAutoField": "AUTOINCREMENT",
    }


    operators = {
        "exact": "= %s",
        "iexact": "LIKE %s ESCAPE '\\'",
        "contains": "LIKE %s ESCAPE '\\'",
        "icontains": "LIKE %s ESCAPE '\\'",
        "regex": "REGEXP %s",
        "iregex": "REGEXP '(?i)' || %s",
        "gt": "> %s",
        "gte": ">= %s",
        "lt": "< %s",
        "lte": "<= %s",
        "startswith": "LIKE %s ESCAPE '\\'",
        "endswith": "LIKE %s ESCAPE '\\'",
        "istartswith": "LIKE %s ESCAPE '\\'",
        "iendswith": "LIKE %s ESCAPE '\\'",
    }


    pattern_esc = r"REPLACE(REPLACE(REPLACE({}, '\', '\\'), '%%', '\%%'), '_', '\_')"
    pattern_ops = {
        "contains": r"LIKE '%%' || {} || '%%' ESCAPE '\'",
        "icontains": r"LIKE '%%' || UPPER({}) || '%%' ESCAPE '\'",
        "startswith": r"LIKE {} || '%%' ESCAPE '\'",
        "istartswith": r"LIKE UPPER({}) || '%%' ESCAPE '\'",
        "endswith": r"LIKE '%%' || {} ESCAPE '\'",
        "iendswith": r"LIKE '%%' || UPPER({}) ESCAPE '\'",
    }

    Database = Database
    SchemaEditorClass = DatabaseSchemaEditor

    client_class = DatabaseClient
    creation_class = DatabaseCreation
    features_class = DatabaseFeatures
    introspection_class = DatabaseIntrospection
    ops_class = DatabaseOperations

    def get_connection_params(self):
        settings_dict = self.settings_dict
        if not settings_dict["NAME"]:
            raise ImproperlyConfigured(
                "settings.DATABASES is improperly configured. "
                "Please supply the NAME value."
            )
        kwargs = {
            "database": settings_dict["NAME"],
            **settings_dict["OPTIONS"],
        }

        # Pass auth_token from PASSWORD if provided.
        if settings_dict.get("PASSWORD"):
            kwargs.setdefault("auth_token", settings_dict["PASSWORD"])

        if "check_same_thread" in kwargs and kwargs["check_same_thread"]:
            warnings.warn(
                "The `check_same_thread` option was provided and set to "
                "True. It will be overridden with False. Use the "
                "`DatabaseWrapper.allow_thread_sharing` property instead "
                "for controlling thread shareability.",
                RuntimeWarning,
            )
        # Remove non-underscored variants and set the libsql-style params.
        kwargs.pop("check_same_thread", None)
        kwargs.pop("uri", None)
        kwargs.update({"_check_same_thread": False, "_uri": True})
        return kwargs

    def get_database_version(self):
        return self.Database.sqlite_version_info

    @async_unsafe
    def get_new_connection(self, conn_params):
        conn = Database.connect(**conn_params)

        conn.execute("PRAGMA foreign_keys = ON")

        return conn

    def create_cursor(self, name=None):
        return SQLiteCursorWrapper(self.connection.cursor())

    @async_unsafe
    def close(self):
        self.validate_thread_sharing()
        # If database is in memory, closing the connection destroys the
        # database. To prevent accidental data loss, ignore close requests on
        # an in-memory db.
        if not self.is_in_memory_db():
            BaseDatabaseWrapper.close(self)

    def _savepoint_allowed(self):
        # When 'isolation_level' is not None, sqlite3 commits before each
        # savepoint; it's a bug. When it is None, savepoints don't make sense
        # because autocommit is enabled. The only exception is inside 'atomic'
        # blocks. To work around that bug, on SQLite, 'atomic' starts a
        # transaction explicitly rather than simply disable autocommit.
        return self.in_atomic_block

    def _set_autocommit(self, autocommit):
        # The native libsql package exposes autocommit directly rather than
        # the stdlib sqlite3 isolation_level API.
        with self.wrap_database_errors:
            self.connection.autocommit = autocommit

    def _is_remote_db(self):
        name = self.settings_dict.get("NAME", "")
        return isinstance(name, str) and name.startswith(
            ("http://", "https://", "libsql://", "ws://", "wss://")
        )

    def disable_constraint_checking(self):
        with self.cursor() as cursor:
            cursor.execute("PRAGMA foreign_keys = OFF")
            # Foreign key constraints cannot be turned off while in a multi-
            # statement transaction. Fetch the current state of the pragma
            # to determine if constraints are effectively disabled.
            enabled = cursor.execute("PRAGMA foreign_keys").fetchone()[0]
        if bool(enabled) and self._is_remote_db():
            # Remote libsql-server does not support disabling foreign keys
            # via PRAGMA, but schema changes are handled server-side.
            return True
        return not bool(enabled)

    def enable_constraint_checking(self):
        with self.cursor() as cursor:
            cursor.execute("PRAGMA foreign_keys = ON")

    def check_constraints(self, table_names=None):
        """
        Check each table name in `table_names` for rows with invalid foreign
        key references. This method is intended to be used in conjunction with
        `disable_constraint_checking()` and `enable_constraint_checking()`, to
        determine if rows with invalid references were entered while constraint
        checks were off.
        """
        with self.cursor() as cursor:
            if table_names is None:
                violations = cursor.execute("PRAGMA foreign_key_check").fetchall()
            else:
                violations = chain.from_iterable(
                    cursor.execute(
                        "PRAGMA foreign_key_check(%s)" % self.ops.quote_name(table_name)
                    ).fetchall()
                    for table_name in table_names
                )
            # See https://www.sqlite.org/pragma.html#pragma_foreign_key_check
            for (
                table_name,
                rowid,
                referenced_table_name,
                foreign_key_index,
            ) in violations:
                foreign_key = cursor.execute(
                    "PRAGMA foreign_key_list(%s)" % self.ops.quote_name(table_name)
                ).fetchall()[foreign_key_index]
                column_name, referenced_column_name = foreign_key[3:5]
                primary_key_column_name = self.introspection.get_primary_key_column(
                    cursor, table_name
                )
                primary_key_value, bad_value = cursor.execute(
                    "SELECT %s, %s FROM %s WHERE rowid = %%s"
                    % (
                        self.ops.quote_name(primary_key_column_name),
                        self.ops.quote_name(column_name),
                        self.ops.quote_name(table_name),
                    ),
                    (rowid,),
                ).fetchone()
                raise IntegrityError(
                    "The row in table '%s' with primary key '%s' has an "
                    "invalid foreign key: %s.%s contains a value '%s' that "
                    "does not have a corresponding value in %s.%s."
                    % (
                        table_name,
                        primary_key_value,
                        table_name,
                        column_name,
                        bad_value,
                        referenced_table_name,
                        referenced_column_name,
                    )
                )

    def is_usable(self):
        return True

    def _start_transaction_under_autocommit(self):
        """
        Start a transaction explicitly in autocommit mode.

        Staying in autocommit mode works around a bug of sqlite3 that breaks
        savepoints when autocommit is disabled.
        """
        self.cursor().execute("BEGIN")

    def is_in_memory_db(self):
        return self.creation.is_in_memory_db(self.settings_dict["NAME"])


FORMAT_QMARK_REGEX = _lazy_re_compile(r"(?<!%)%s")


class SQLiteCursorWrapper:
    """
    Wraps a libsql Cursor to convert Django's parameter styles.

    Django uses the "format" and "pyformat" styles, but libsql's cursor
    supports only "qmark" style.

    This wrapper performs the following conversions:

    - "format" style to "qmark" style
    - "pyformat" style to "named" style

    It also adapts parameter types since libsql does not support
    register_adapter/register_converter.

    In both cases, if you want to use a literal "%s", you'll need to use "%%s".
    """

    def __init__(self, cursor):
        self._cursor = cursor

    def __getattr__(self, name):
        return getattr(self._cursor, name)

    def __iter__(self):
        return iter(self._cursor)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def execute(self, query, params=None):
        if params is None:
            self._cursor.execute(query)
        else:
            # Extract names if params is a mapping, i.e. "pyformat" style is used.
            param_names = list(params) if isinstance(params, Mapping) else None
            query = self.convert_query(query, param_names=param_names)
            params = _adapt_params(params)
            self._cursor.execute(query, params)
        # Return self so chaining like cursor.execute(...).fetchone() works.
        return self

    def executemany(self, query, param_list):
        # Extract names if params is a mapping, i.e. "pyformat" style is used.
        # Peek carefully as a generator can be passed instead of a list/tuple.
        peekable, param_list = tee(iter(param_list))
        if (params := next(peekable, None)) and isinstance(params, Mapping):
            param_names = list(params)
        else:
            param_names = None
        query = self.convert_query(query, param_names=param_names)
        adapted = (_adapt_params(p) for p in param_list)
        self._cursor.executemany(query, adapted)
        return self

    def convert_query(self, query, *, param_names=None):
        if param_names is None:
            # Convert from "format" style to "qmark" style.
            return FORMAT_QMARK_REGEX.sub("?", query).replace("%%", "%")
        else:
            # Convert from "pyformat" style to "named" style.
            return query % {name: f":{name}" for name in param_names}

    def close(self):
        try:
            self._cursor.close()
        except Exception:
            pass

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchmany(self, size=None):
        if size is None:
            return self._cursor.fetchmany()
        return self._cursor.fetchmany(size)

    def fetchall(self):
        return self._cursor.fetchall()
