import os

DB_URL = os.environ['DB_URL']


def connect():
    return DB_URL
