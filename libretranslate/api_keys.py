import os
import pymysql
import uuid

import requests
from expiringdict import ExpiringDict

from libretranslate.default_values import DEFAULT_ARGUMENTS as DEFARGS

DEFAULT_DB_PATH = DEFARGS['API_KEYS_DB_PATH']


class Database:
    def __init__(self, db_path=DEFAULT_DB_PATH, max_cache_len=1000, max_cache_age=30):
        db_host = os.environ.get('DB_HOST')
        db_port = int(os.environ.get('DB_PORT', 3306))
        db_user = os.environ.get('DB_USER')
        db_pass = os.environ.get('DB_PASS')
        db_name = os.environ.get('DB_NAME')

        self.cache = ExpiringDict(max_len=max_cache_len, max_age_seconds=max_cache_age)

        self.conn = pymysql.connect(
            host=db_host,
            port=db_port,
            user=db_user,
            password=db_pass,
            database=db_name,
            autocommit=True
        )

        with self.conn.cursor() as cursor:
            cursor.execute(
                """CREATE TABLE IF NOT EXISTS api_keys (
                api_key VARCHAR(255) PRIMARY KEY,
                req_limit INT,
                char_limit INT DEFAULT NULL
            );"""
            )
            
            # Asegurar que char_limit exista si la tabla fue creada previamente sin él
            cursor.execute("""
                SELECT COUNT(*)
                FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_NAME = 'api_keys'
                AND COLUMN_NAME = 'char_limit'
                AND TABLE_SCHEMA = %s
            """, (db_name,))
            if cursor.fetchone()[0] == 0:
                cursor.execute("ALTER TABLE api_keys ADD COLUMN char_limit INT DEFAULT NULL;")

    def lookup(self, api_key):
        val = self.cache.get(api_key)
        if val is None:
            # DB Lookup
            with self.conn.cursor() as cursor:
                cursor.execute(
                    "SELECT req_limit, char_limit FROM api_keys WHERE api_key = %s", (api_key,)
                )
                row = cursor.fetchone()
                if row is not None:
                    # LibreTranslate expects (req_limit, char_limit)
                    self.cache[api_key] = row
                    val = row
                else:
                    self.cache[api_key] = False
                    val = False

        if isinstance(val, bool):
            val = None

        return val

    def add(self, req_limit, api_key="auto", char_limit=None):
        if api_key == "auto":
            api_key = str(uuid.uuid4())
        if char_limit == 0:
            char_limit = None

        self.remove(api_key)
        with self.conn.cursor() as cursor:
            cursor.execute(
                "INSERT INTO api_keys (api_key, req_limit, char_limit) VALUES (%s, %s, %s)",
                (api_key, req_limit, char_limit),
            )
        return (api_key, req_limit, char_limit)

    def remove(self, api_key):
        with self.conn.cursor() as cursor:
            cursor.execute("DELETE FROM api_keys WHERE api_key = %s", (api_key,))
        return api_key

    def all(self):
        with self.conn.cursor() as cursor:
            cursor.execute("SELECT api_key, req_limit, char_limit FROM api_keys")
            rows = cursor.fetchall()
            return rows


class RemoteDatabase:
    def __init__(self, url, max_cache_len=1000, max_cache_age=600):
        self.url = url
        self.cache = ExpiringDict(max_len=max_cache_len, max_age_seconds=max_cache_age)

    def lookup(self, api_key):
        val = self.cache.get(api_key)
        if val is None:
            try:
                r = requests.post(self.url, data={'api_key': api_key}, timeout=60)
                res = r.json()
            except Exception as e:
                print("Cannot authenticate API key: " + str(e))
                return None

            if res.get('error') is not None:
                return None

            req_limit = res.get('req_limit', None)
            char_limit = res.get('char_limit', None)

            val = self.cache[api_key] = (req_limit, char_limit)
            
        return val
