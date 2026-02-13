# Django + LibSQL / Turso (Native Bindings)

Fork of [aaronkazah/django-libsql](https://github.com/aaronkazah/django-libsql) that uses the native [`libsql`](https://pypi.org/project/libsql/) Python package (Rust bindings) instead of the deprecated `libsql-client` (Python WebSocket client).

## Why this fork?

- `libsql-client` is **archived and deprecated** by Turso
- `libsql` is a **native Rust extension** — faster and more reliable
- `libsql` supports **embedded replicas** (local SQLite file + remote sync)

## Installation

Install directly from the git repo:

```
pip install git+https://github.com/Vultron-AI/django-libsql.git@main
```

## Configuration

### Remote database (Turso)

```python
DATABASES = {
    "default": {
        "ENGINE": "libsql.db.backends.sqlite3",
        "NAME": "libsql://${your-db-name}.turso.io",
        "PASSWORD": "${your-auth-token}",
    }
}
```

### Embedded replica (local file + remote sync)

```python
DATABASES = {
    "default": {
        "ENGINE": "libsql.db.backends.sqlite3",
        "NAME": "local_replica.db",
        "PASSWORD": "${your-auth-token}",
        "OPTIONS": {
            "sync_url": "libsql://${your-db-name}.turso.io",
        },
    }
}
```

### Local SQLite file

```python
DATABASES = {
    "default": {
        "ENGINE": "libsql.db.backends.sqlite3",
        "NAME": "db.sqlite3",
    }
}
```

## Usage

After configuration, use Django's ORM as usual. The libsql backend handles all database operations.

## Known Issues

1. Custom Django functions registered via `create_function` are not supported by the `libsql` package.
2. Certain Django ORM features that rely on custom functions will not work:
    - Date/time operations using `F()` objects
    - `dates()` queryset method

## License

This project is distributed under the MIT license.
