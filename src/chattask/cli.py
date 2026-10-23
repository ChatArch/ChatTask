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


if __name__ == "__main__":
    main()
