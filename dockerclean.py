#!/usr/bin/env python
# PYTHON_ARGCOMPLETE_OK

# Copyright 2018 Micah Culpepper
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Carefully remove unused docker artifacts. Deletes:

    1. Containers that haven't been running for longer than $GRACE_PERIOD,
    2. Images that have existed for longer than $GRACE_PERIOD, are not
       referenced by any container, and do not have a tag. In $AGGRESSIVE
       mode, ignores tags.
    3. Networks that have existed for longer than $GRACE_PERIOD and are not
       in use by any container,
    4. Volumes that have existed for longer than $GRACE_PERIOD, are not in
       use by any container, and do not have a name. In $AGGRESSIVE mode,
       ignores names.
"""

from __future__ import print_function

import argparse
import collections
import datetime
import os
import re
import shlex
import subprocess
import sys
import time

# requires pip package: python-dateutil
import dateutil.parser

# optional pip package: argcomplete  (for tab completion)
try:
    import argcomplete
except ImportError:
    argcomplete = None


PY3 = sys.version_info[0] == 3

if (not PY3 and sys.version_info[1] < 7) or (PY3 and sys.version_info[1] < 5):
    print("This script requires Python 2.7, or 3.5+.", file=sys.stderr)
    sys.exit(1)

if not PY3:
    # subprocess.Popen on Python 2 doesn't have a timeout mechanism, so we have
    # to roll our own with SIGALRM. That means this script won't work on
    # Windows with Python 2.
    import signal

    class Alarm(Exception):
        pass

    def alarm_handler(signum, frame):
        raise Alarm()


# These defaults can be overridden by environment variables or CLI arguments
GRACE_PERIOD = os.getenv("GRACE_PERIOD") or "720h"  # one month
AGGRESSIVE = os.getenv("AGGRESSIVE") or False

PResponse = collections.namedtuple(
    "PResponse", field_names=[
        "stdout",
        "stderr",
        "code",
    ]
)
ContainerData = collections.namedtuple(
    "ContainerData", field_names=[
        "id",
        "finished_at",
    ]
)
VolumeData = collections.namedtuple(
    "VolumeData", field_names=[
        "name",
        "created",
    ]
)


class ImageData(object):
    def __init__(self, id, parent, created, repo_tags):
        self.id = self.hashfix(id)
        self.parent = self.hashfix(parent)
        self.created = created
        self.repo_tags = repo_tags

    def __repr__(self):
        return "{}({})".format(self.__class__.__name__, self.id)

    @staticmethod
    def hashfix(hash):
        if hash.startswith("sha256:"):
            return hash[7:]
        else:
            return hash


class NetworkData(object):
    def __init__(self, id, created, containers, name):
        self.id = id
        self.created = created
        if containers == "map[]":
            containers = None
        self.containers = containers
        self.name = name


class Duration(object):

    pattern = r"(\d+)([mh])"

    def __new__(cls, s):
        """Convert a duration string to a datetime.timedelta object

        :param s: String representation of a duration, like 60m or 24h
        :type s: str
        :return: a duration
        :rtype: datetime.timedelta
        """
        # For potential interoperability with docker commands, these durations should
        # be kept to a format compatible with https://golang.org/pkg/time/#ParseDuration
        match = re.match(Duration.pattern, s)
        if not match:
            raise ValueError("Invalid duration format: {}".format(s))
        n = int(match.group(1))
        t = match.group(2)
        if t == "m":
            return datetime.timedelta(minutes=n)
        else:
            return datetime.timedelta(hours=n)


def run_command(cmd, timeout=30, check=True):
    """Run the given system command. Return results.

    :param cmd: Command to run
    :type cmd: str
    :param timeout: Number of seconds to wait for the command to complete. If \
        the timeout expires, raise a subprocess.CalledProcessError on py2 \
        or a subprocess.TimeoutExpired on py3. Pass a value of `None` to \
        disable timeouts.
    :type timeout: int
    :param check: Raise a subprocess.CalledProcessError if the command failed.
    :type check: bool
    :return: stdout, stderr, code
    :rtype: PResponse
    """
    cmd = shlex.split(cmd)
    if PY3:
        p = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            encoding="utf-8", timeout=timeout, check=check
        )
        return PResponse(p.stdout, p.stderr, p.returncode)
    else:
        p = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        if timeout is None:
            timeout = 0
        signal.signal(signal.SIGALRM, alarm_handler)  # Enable signal handler
        signal.alarm(timeout)  # set a timer for SIGALRM
        try:
            stdout, stderr = p.communicate()
            code = p.wait()
        except Alarm:
            stdout = ""
            stderr = ""
            code = -1
            p.terminate()  # SIGTERM
            time.sleep(2)
            if p.poll() is None:  # Process is still running
                p.kill()  # SIGKILL
        finally:
            signal.alarm(0)  # turn off our SIGALRM timer
            signal.signal(signal.SIGALRM, signal.SIG_DFL)  # Disable signal handler
        retval = PResponse(stdout, stderr, code)
        if retval.code != 0 and check:
            raise subprocess.CalledProcessError(
                retval.code, cmd, output=retval.stdout
            )
        else:
            return retval


def older_than(timestamp, age):
    """Check if the age of the given timestamp is greater than the given age.

    :param timestamp: An RFC3339-formatted timestamp
    :type timestamp: str
    :param age: A duration
    :type age: datetime.timedelta
    :return: Whether the given timestamp is older than the current time minus \
        the given age.
    :rtype: bool
    """
    try:
        t = dateutil.parser.parse(timestamp)
    except ValueError:
        # `docker network inspect` yields timestamps in a different format.
        # ignoring the last word in this format enables parsing.
        t = dateutil.parser.parse(timestamp.rpartition(" ")[0])
    now = datetime.datetime.now(tz=t.tzinfo)
    cutoff = now - age
    return bool(t < cutoff)


def prune_containers(grace):
    """Delete non-running containers whose FinishedAt timestamp is older than
    `grace`.

    :param grace: Containers younger than this will be spared.
    :type grace: datetime.timedelta
    :return: IDs of pruned containers
    :rtype: tuple(str)
    """
    running_containers = set(run_command(
        "docker container ls -q --no-trunc"
    ).stdout.strip().splitlines())

    all_containers = set(run_command(
        "docker container ls -a -q --no-trunc"
    ).stdout.strip().splitlines())

    non_running = all_containers.difference(running_containers)
    if not non_running:
        container_data = []
    else:
        container_data = [
            ContainerData(*i.split("|", 1)) for i in run_command(
                "docker container inspect --format '{{.ID}}|{{.State.FinishedAt}}' "
                + " ".join(non_running)
            ).stdout.strip().splitlines()
        ]

    to_prune = set((i.id for i in container_data if older_than(i.finished_at, grace)))

    if not to_prune:
        return tuple()
    else:
        return tuple(run_command(
            "docker rm "
            + " ".join(to_prune)
        ).stdout.strip().splitlines())


def prune_images(grace, aggressive=False):
    """Delete unused images

    :param grace: Images younger than this will be spared.
    :type grace: datetime.timedelta
    :param aggressive: If true, prune all images not in use. If false, \
        prune only non-tagged images that are not in use.
    :type aggressive: bool
    :return: IDs of pruned images
    :rtype: tuple(str)
    """

    all_images = set((
        ImageData.hashfix(i) for i in
        run_command("docker image ls -a -q --no-trunc").stdout.strip().splitlines()
    ))

    all_containers = set(
        run_command("docker ps -a -q --no-trunc").stdout.strip().splitlines()
    )

    if not all_containers:
        nominally_used_images = set()
    else:
        nominally_used_images = set([
            ImageData.hashfix(i) for i in
                run_command(
                    "docker inspect --format '{{.Image}}' "
                    + " ".join(all_containers)
                ).stdout.strip().splitlines()
            ]
        )

    if not all_images:
        image_data = {}
    else:
        image_data = {
            i.id: i
            for i in [
                ImageData(*i.split("|", 3)) for i in
                run_command(
                    "docker image inspect --format "
                    "'{{.ID}}|{{.Parent}}|{{.Created}}|{{.RepoTags}}' "
                    + " ".join(all_images)
                ).stdout.strip().splitlines()
            ]
        }

    lineages = []
    for image_id in nominally_used_images:
        image = image_data[image_id]
        line = []
        line.append(image.id)
        while image.parent:
            image = image_data[image.parent]
            line.append(image.id)
        lineages.append(line)
    all_used_images = set([i for l in lineages for i in l])
    all_untagged_images = set(i.id for i in image_data.values() if not i.repo_tags)
    all_old_images = set(i.id for i in image_data.values() if older_than(i.created, grace))
    pruneable_images = all_old_images.difference(all_used_images)
    if not aggressive:
        pruneable_images = pruneable_images.intersection(all_untagged_images)

    if not pruneable_images:
        return tuple()
    else:
        return tuple(
            run_command(
                "docker rmi -f "
                + " ".join(pruneable_images)
            ).stdout.strip().splitlines()
        )


def prune_networks(grace):
    all_networks = set(run_command(
        "docker network ls -q --no-trunc"
    ).stdout.strip().splitlines())

    if not all_networks:
        network_data = set()
    else:
        network_data = [
                NetworkData(*n.split("|", 3)) for n in
                run_command(
                    "docker network inspect --format '{{.ID}}|{{.Created}}|{{.Containers}}|{{.Name}}' "
                    + " ".join(all_networks)
                ).stdout.strip().splitlines()
            ]

    used_networks = set(i.id for i in network_data if i.containers)
    old_networks = set(i.id for i in network_data if older_than(i.created, grace))
    reserved_networks = set(i.id for i in network_data if i.name in ("bridge", "host", "none"))
    prunable_networks = old_networks.difference(used_networks).difference(reserved_networks)

    if not prunable_networks:
        return tuple()
    else:
        return tuple(run_command(
            "docker network rm "
            + " ".join(prunable_networks)
        ).stdout.strip().splitlines())


def prune_volumes(grace, aggressive=False):
    """

    :param grace: Volumes younger than this will be spared.
    :type grace: datetime.timedelta
    :param aggressive: If true, prune all volumes not in use. If false, \
        prune only non-named volumes that are not in use.
    :type aggressive: bool
    :return:
    :rtype:
    """
    dangling_volumes = set(run_command(
        "docker volume ls -q -f dangling=true"
    ).stdout.strip().splitlines())

    if not dangling_volumes:
        volume_data = []
    else:
        volume_data = [
                VolumeData(*i.split("|", 1)) for i in
                run_command(
                    "docker volume inspect --format "
                    "'{{.Name}}|{{.CreatedAt}}' "
                    + " ".join(dangling_volumes)
                ).stdout.strip().splitlines()
            ]

    old_volumes = set(v.name for v in volume_data if older_than(v.created, grace))
    named_volumes = set(v.name for v in volume_data if len(v.name) < 64)
    to_prune = old_volumes
    if not aggressive:
        to_prune = to_prune.difference(named_volumes)

    if not to_prune:
        return tuple()
    else:
        return tuple(run_command(
            "docker volume rm -f "
            + " ".join(to_prune)
        ).stdout.strip().splitlines())


def parse_cli_args(argv):
    parser = argparse.ArgumentParser(
        prog="dockerclean", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "-g", "--grace_period", default=Duration(GRACE_PERIOD),
        help=(
            "Do not delete images that were created within this period, "
            "and do not delete any containers that last stopped within "
            "this period. Must be a number followed by 'm' for minutes or "
            "'h' for hours. Default: {}".format(GRACE_PERIOD)
        ),
        metavar=Duration.pattern, type=Duration
    )
    parser.add_argument(
        "-a", "--aggressive", default=AGGRESSIVE, action="store_true",
        help=(
            "Delete unused images, regardless of their tags, and delete "
            "unused volumes, regardless of their names."
        )
    )
    if argcomplete and os.isatty(sys.stdin.fileno()):
        argcomplete.autocomplete(parser)
    return parser.parse_args(argv)


def print_flush(*args, **kwargs):
    print(*args, **kwargs)
    sys.stdout.flush()


def main(argv=sys.argv):
    args = parse_cli_args(argv[1:])
    grace, aggressive = args.grace_period, args.aggressive

    print_flush("(1/4) Pruning containers... ", end="")
    pruned_containers = prune_containers(grace)
    print_flush("{} pruned.".format(len(pruned_containers)))

    print_flush("(2/4) Pruning images... ", end="")
    pruned_images = prune_images(grace, aggressive)
    print_flush("{} pruned.".format(len(pruned_images)))

    print_flush("(3/4) Pruning networks... ", end="")
    pruned_networks = prune_networks(grace)
    print_flush("{} pruned.".format(len(pruned_networks)))

    print_flush("(4/4) Pruning volumes... ", end="")
    pruned_volumes = prune_volumes(grace, aggressive)
    print_flush("{} pruned.".format(len(pruned_volumes)))


if __name__ == "__main__":
    sys.exit(main(sys.argv))
