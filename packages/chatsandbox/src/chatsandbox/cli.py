"""Thin CLI for shared Sandbox profiles and the loopback gateway."""
import json
import click
from chatstyle import add_tree_option
from chatenv import get_paths
from . import __version__


@click.group(name='chatsandbox',invoke_without_command=True,no_args_is_help=True)
@click.version_option(__version__,prog_name='chatsandbox')
@add_tree_option(renderer_options={'root_name':'chatsandbox'})
def main():
    """Shared sandbox lifecycle, runtime evidence and authenticated API gateway."""


@main.command()
def paths():
    """Show non-secret ChatArch runtime locations without creating state."""
    home=get_paths().home_dir
    click.echo(json.dumps({'home':str(home),'state':str(home/'chatsandbox/state'),'profiles':str(home/'envs/ChatSandbox')},indent=2))


@main.command()
@click.option('--env-profile',required=True,help='Existing ChatEnv ChatSandbox server profile.')
@click.option('--port',type=click.IntRange(1,65535),default=10449,show_default=True)
def serve(env_profile,port):
    """Serve the native-compatible API on loopback only."""
    try:
        import uvicorn
        from .config import load_server
        app=load_server(env_profile)
    except (ValueError,OSError,ImportError):
        raise click.ClickException('Server dependencies or protected profile configuration are unavailable') from None
    uvicorn.run(app,host='127.0.0.1',port=port,proxy_headers=False,access_log=False,
                log_level='warning',limit_concurrency=32,timeout_keep_alive=5)
