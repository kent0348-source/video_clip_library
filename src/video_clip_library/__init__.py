try:
    from aqt import mw
except ModuleNotFoundError as error:
    if error.name != "aqt":
        raise
    mw = None

if mw is not None:
    from .controller import init_addon

    init_addon()
