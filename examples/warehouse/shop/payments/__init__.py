"""Payment providers register themselves here with @register("name")."""

PROVIDERS = {}


def register(name):
    def decorator(cls):
        PROVIDERS[name] = cls
        return cls
    return decorator


def get_provider(name):
    if name not in PROVIDERS:
        raise KeyError("no payment provider registered for %r" % name)
    return PROVIDERS[name]()
