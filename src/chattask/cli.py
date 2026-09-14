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


if __name__ == "__main__":
    main()
