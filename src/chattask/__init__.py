"""ChatTask: explicit task execution, sessions and an optional browser adapter."""

__version__ = "0.1.1"
__all__ = ["__version__", "TaskService", "TaskSettings", "create_app", "state_paths", "WorkflowService", "TaskStore"]


def __getattr__(name):
    if name == 'TaskSettings':
        from .settings import TaskSettings
        return TaskSettings
    if name in ('WorkflowService', 'TaskStore'):
        from .workflow import WorkflowService, TaskStore
        return {'WorkflowService': WorkflowService, 'TaskStore': TaskStore}[name]
    if name == 'TaskService':
        from .service import TaskService
        return TaskService
    raise AttributeError(name)


def create_app(settings=None, *, service=None, workflow=None, agent_tasks=None):
    from .web import create_app as factory
    return factory(settings, service=service, workflow=workflow, agent_tasks=agent_tasks)


def state_paths(*, home=None):
    from .settings import state_paths as resolve
    return resolve(home=home)
