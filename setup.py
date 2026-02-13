from setuptools import setup

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setup(
    name="django-libsql",
    version="0.2.0",
    author="Aaron Kazah",
    description="A Django integration for libSQL / turso database (native libsql bindings)",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/Vultron-AI/django-libsql",
    packages=['libsql.db.backends.sqlite3'],
    package_data={
        'libsql.db.backends.sqlite3': ['*.py'],
    },
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Programming Language :: Python :: 3.13",
        "Framework :: Django",
    ],
    python_requires=">=3.10",
    install_requires=[
        "Django>=4.2",
        "libsql",
    ],
)
