from src.database import fetch_user


def get_user(user_id):
    return fetch_user(user_id)['name']
