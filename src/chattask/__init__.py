"""ChatTask: explicit task execution, sessions and an optional browser adapter."""

__version__ = "0.1.0.dev0"
__all__ = ["__version__", "TaskService", "TaskSettings", "create_app", "state_paths"]


def __getattr__(name):
    if name == 'TaskSettings':
        from .settings import TaskSettings
        return TaskSettings
    if name == 'TaskService':
        from .service import TaskService
        return TaskService
    raise AttributeError(name)


def create_app(settings=None, *, service=None):
    from .web import create_app as factory
    return factory(settings, service=service)


def state_paths(*, home=None):
    from .settings import state_paths as resolve
    return resolve(home=home)
