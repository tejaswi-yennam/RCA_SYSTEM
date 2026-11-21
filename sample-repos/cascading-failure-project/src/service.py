from src.database import connect


def initialise_service():
    return {'connection': connect()}
