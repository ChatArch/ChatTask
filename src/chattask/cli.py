"""CLI entrypoint for chattask."""

from __future__ import annotations

import click
from chatstyle import add_tree_option

from chattask import __version__


@click.group(name="chattask", invoke_without_command=True, no_args_is_help=True)
@click.version_option(__version__, prog_name="chattask")
@add_tree_option(renderer_options={"root_name": "chattask"})
def main() -> None:
    """chattask command line interface."""
    # Add package-specific commands here. Prefer ChatStyle helpers for
    # interactive input when a command needs recoverable user input.
    pass


@main.command()
@click.option('--host', default='127.0.0.1', show_default=True, help='Bind address; prefer loopback behind a trusted reverse proxy.')
@click.option('--port', type=click.IntRange(1,65535), default=8000, show_default=True)
@click.option('-e','--env-profile', default=None, help='Read this ChatEnv profile without globally activating it.')
def serve(host, port, env_profile):
    """Serve the authenticated Task/Sandbox browser and API from this package."""
    try:
        import uvicorn
        from .settings import load_settings
        from . import create_app
        app=create_app(load_settings(profile=env_profile))
    except ImportError as exc:
        raise click.ClickException('Web dependencies are required; install ChatTask[web].') from exc
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    uvicorn.run(app,host=host,port=port,access_log=False,proxy_headers=False)


@main.command(name='paths')
@click.option('--json-output',is_flag=True,help='Emit the resolved runtime paths as JSON.')
def paths_command(json_output):
    """Resolve ChatArch-contained state paths without creating them."""
    from . import state_paths
    import json
    paths=state_paths()
    if json_output:
        click.echo(json.dumps(paths,ensure_ascii=False))
    else:
        for key,value in paths.items():click.echo(f'{key}={value}')



@main.group(name='repository')
def repository_group():
    """Manage trusted local source roots for template runs."""


@repository_group.command(name='register')
@click.option('--id', 'repo_id', required=True, help='Stable repository ID shown in the browser.')
@click.option('--path', type=click.Path(path_type=str, exists=True, file_okay=False), required=True,
              help='Absolute clean managed Git root. Only an operator may register it.')
@click.option('--github-repo', required=True, help='GitHub owner/name; no URL or credentials.')
@click.option('--default-branch', default='main', show_default=True)
@click.option('--package-name', default='', help='PyPI distribution name for metadata probe.')
def register_repository(repo_id, path, github_repo, default_branch, package_name):
    """Register an operator-trusted Git root; browser users cannot supply paths."""
    from .settings import state_paths
    from .workflow import TaskStore, WorkflowService
    try:
        flow = WorkflowService(TaskStore(state_paths()['state_dir']))
        record = flow.register_repository(repo_id, path, github_repo, default_branch, package_name)
    except (ValueError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Registered {record['id']} at an exact managed Git root.")

if __name__ == "__main__":
    main()
