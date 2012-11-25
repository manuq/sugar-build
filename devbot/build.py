#!/usr/bin/python -u

from distutils import sysconfig
import fnmatch
import json
import os
import multiprocessing
import shutil
import sys
import subprocess

from devbot import command
from devbot import config
from devbot import environ

state = { "built_modules": {} }

def get_state_path():
    return os.path.join(config.devbot_dir, "state.json")

def load_state():
    global state

    state_path = get_state_path()
    if os.path.exists(state_path):
        state = json.load(open(state_path))

def save_state():
    json.dump(state, open(get_state_path(), "w+"))

def add_path(name, path):
    if name not in os.environ:
        os.environ[name] = path
        return

    splitted = os.environ[name].split(":")
    splitted.append(path)

    os.environ[name] = ":".join(splitted)

def get_module_commit_id(module):
    orig_cwd = os.getcwd()
    os.chdir(module.get_source_dir())

    commit_id = subprocess.check_output(["git", "rev-parse", "HEAD"])

    os.chdir(orig_cwd)

    return commit_id.strip()

def unlink_libtool_files():
    def func(arg, dirname, fnames):
        for fname in fnmatch.filter(fnames, "*.la"):
            os.unlink(os.path.join(dirname, fname))

    os.path.walk(config.lib_dir, func, None)

def pull_source(module):
    module_dir = module.get_source_dir()

    if os.path.exists(module_dir):
        os.chdir(module_dir)

        command.run(["git", "remote", "set-url", "origin", module.repo])
        command.run(["git", "remote", "update", "origin"], retry=10)
    else:
        os.chdir(config.get_source_dir())
        command.run(["git", "clone", "--progress",
                     module.repo, module.name],
                    retry=10)
        os.chdir(module_dir)

    command.run(["git", "checkout", module.branch])

def build_make(module):
    command.run(["make"])

def build_autotools(module):
    autogen = os.path.join(module.get_source_dir(), "autogen.sh")

    jobs = multiprocessing.cpu_count() * 2

    args = [autogen,
            "--prefix", config.prefix_dir,
            "--libdir", config.lib_dir]
    args.extend(module.options)

    command.run(args)

    command.run(["make", "-j", "%d" % jobs])
    command.run(["make", "install"])

    unlink_libtool_files()

def build_activity(module):
    command.run(["./setup.py", "install", "--prefix", config.prefix_dir])

def build_module(module):
    module_source_dir = module.get_source_dir()

    if module.out_of_source:
        module_build_dir = module.get_build_dir()

        if not os.path.exists(module_build_dir):
            os.mkdir(module_build_dir)

        os.chdir(module_build_dir)
    else:
        os.chdir(module_source_dir)

    if os.path.exists(os.path.join(module_source_dir, "setup.py")):
        build_activity(module)
    elif os.path.exists(os.path.join(module_source_dir, "autogen.sh")):
        build_autotools(module)
    elif os.path.exists(os.path.join(module_source_dir, "Makefile")):
        build_make(module)
    else:
        print "Unknown build system"
        sys.exit(1)

    state["built_modules"][module.name] = get_module_commit_id(module)
    save_state()

def clear_built_modules(modules, index):
    if index < len(modules) - 1:
        for module in modules[index + 1:]:
            name = module.name
            if name in state["built_modules"]:
                del state["built_modules"][name]

def rmtree(dir):
    print "Deleting %s" % dir
    shutil.rmtree(dir, ignore_errors=True)

def build():
    environ.setup()
    load_state()

    modules = config.load_modules()

    for i, module in enumerate(modules):
        print "\n=== Building %s ===\n" % module.name

        try:
            pull_source(module)

            old_commit_id = state["built_modules"].get(module.name, None)
            new_commit_id = get_module_commit_id(module)

            if old_commit_id is None or old_commit_id != new_commit_id:
                clear_built_modules(modules, i)
                build_module(module)
            else:
                print "\n* Already built, skipping *"
        except subprocess.CalledProcessError:
            sys.exit(1)

def clean():
    rmtree(config.install_dir)
    rmtree(config.prefix_dir)
    rmtree(config.get_build_dir())

    for module in config.load_modules():
        if not module.out_of_source:
            rmtree(module.get_source_dir())
