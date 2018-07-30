dockerclean
===========

Carefully remove unused docker artifacts. Deletes:

    1. Containers that haven't been running for longer than ``$GRACE_PERIOD``,
    2. Images that have existed for longer than ``$GRACE_PERIOD``, are not
       referenced by any container, and do not have a tag. In ``$AGGRESSIVE``
       mode, ignores tags.
    3. Networks that have existed for longer than ``$GRACE_PERIOD`` and are not
       in use by any container,
    4. Volumes that have existed for longer than ``$GRACE_PERIOD``, are not in
       use by any container, and do not have a name. In ``$AGGRESSIVE`` mode,
       ignores names.


Motivation
==========

There are built-in docker commands to do things similar to what this script does.
For example, `docker system prune <https://docs.docker.com/engine/reference/commandline/system_prune/>`_.
However, all of the built-in commands seemed too aggressive to me. I didn't want to run
them in my production environment for fear of deleting resources that I still might need.

With the ``--until`` filters to the builtin commands, you can delete things that have existed
for more than a certain amount of time. But existing is not the same as being idle.
It's very possible to delete an important container that has been running for months
and just-so-happened to stop a few minutes before you decided to clean up some space.

This script is something that I feel is safe enough to run on a cron job in my prod environment
at work.

Disclaimer
==========

While I feel that this script is safer than some other options, I do not guarantee that it
is fit for any specific purpose, or that it is safe enough for _your_ environment. Use
your best judgement when running strangers' scripts on your servers.


Installation
============

::

    pip install python-dateutil
    curl -fsSLO https://raw.githubusercontent.com/micahculpepper/dockerclean/master/dockerclean.py
    chmod +x dockerclean.py
    ln -s $(pwd)/dockerclean.py /usr/local/bin/dockerclean

pip-installable package coming soon. Possibly system packages, too. Open an issue and let me know what
systems you'd like to see a package for.

Usage
=====

The user running this script will need to have permissions to run docker commands. The easiest way to
do that is to make sure they're a member of `the docker group <https://docs.docker.com/install/linux/linux-postinstall/#manage-docker-as-a-non-root-user>`_.

Options

    -g, --grace_period
        Do not delete images that were created within this period, and do not delete any containers that last
        stopped within this period. Must be a number followed by 'm' for minutes or 'h' for hours. Default: 720h.
        This option can also be set via the environment variable ``GRACE_PERIOD``

    -a, --aggressive
        Delete unused images, regardless of their tags, and delete unused volumes, regardless of their names.
        This option can also be set via the environment variable ``AGGRESSIVE``

Example
=======
::

    $ dockerclean
    (1/4) Pruning containers... 2 pruned.
    (2/4) Pruning images... 0 pruned.
    (3/4) Pruning networks... 0 pruned.
    (4/4) Pruning volumes... 0 pruned.

::

    $ dockerclean -a -g 1h
    (1/4) Pruning containers... 33 pruned.
    (2/4) Pruning images... 0 pruned.
    (3/4) Pruning networks... 0 pruned.
    (4/4) Pruning volumes... 0 pruned.
