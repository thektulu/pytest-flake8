"""py.test plugin to test with flake8."""

import os
import re

from flake8.main import application
from flake8.options import config

import py

import pytest

__version__ = '0.6'

HISTKEY = "flake8/mtimes"


def pytest_addoption(parser):
    """Hook up additional options."""
    group = parser.getgroup("general")
    group.addoption(
        '--flake8', action='store_true',
        help="perform some flake8 sanity checks on .py files")
    group.addoption(
        '--flake8-fast', action='store_true',
        help="same as --flake8 but aggregates all files to single check for much better performance")
    parser.addini(
        "flake8-ignore", type="linelist",
        help="each line specifies a glob pattern and whitespace "
             "separated FLAKE8 errors or warnings which will be ignored, "
             "example: *.py W293")
    parser.addini(
        "flake8-max-line-length",
        help="maximum line length")
    parser.addini(
        "flake8-max-complexity",
        help="McCabe complexity threshold")
    parser.addini(
        "flake8-show-source", type="bool",
        help="show the source generate each error or warning")
    parser.addini(
        "flake8-statistics", type="bool",
        help="count errors and warnings")
    parser.addini(
        "flake8-extensions", type="args", default=[".py"],
        help="a list of file extensions, for example: .py .pyx")


def pytest_configure(config):
    """Start a new session."""
    if config.option.flake8 or config.option.flake8_fast:
        config._flake8ignore = Ignorer(config.getini("flake8-ignore"))
        config._flake8maxlen = config.getini("flake8-max-line-length")
        config._flake8maxcomplexity = config.getini("flake8-max-complexity")
        config._flake8showshource = config.getini("flake8-show-source")
        config._flake8statistics = config.getini("flake8-statistics")
        config._flake8exts = config.getini("flake8-extensions")
        config.addinivalue_line('markers', "flake8: Tests which run flake8.")
        if hasattr(config, 'cache'):
            config._flake8mtimes = config.cache.get(HISTKEY, {})


def pytest_collect_file(path, parent):
    """Filter files down to which ones should be checked."""
    config = parent.config
    if (config.option.flake8 or config.option.flake8_fast) and path.ext in config._flake8exts:
        flake8ignore = config._flake8ignore(path)
        if flake8ignore is not None:
            if hasattr(Flake8Item, "from_parent"):
                item = Flake8Item.from_parent(parent, fspath=path)
                item.flake8ignore = flake8ignore
                item.maxlength = config._flake8maxlen
                item.maxcomplexity = config._flake8maxcomplexity
                item.showshource = config._flake8showshource
                item.statistics = config._flake8statistics
                return item
            else:
                return Flake8Item(
                    path,
                    parent,
                    flake8ignore=flake8ignore,
                    maxlength=config._flake8maxlen,
                    maxcomplexity=config._flake8maxcomplexity,
                    showshource=config._flake8showshource,
                    statistics=config._flake8statistics)


def find_top_parent(item):
    curr = item
    while True:
        if hasattr(curr, 'parent') and curr.parent:
            curr = curr.parent
        else:
            return curr


def pytest_collection_modifyitems(config, items):
    if config.option.flake8_fast:
        other_items = []
        deselected_items = []
        fspaths = []
        parent = None
        for item in items:
            if not isinstance(item, Flake8Item):
                other_items.append(item)
            else:
                deselected_items.append(item)
                fspaths.append(item.fspath)
                if parent is None:
                    parent = find_top_parent(item)
        if fspaths:
            flake8ignore = config._flake8ignore('__flake8__')
            if flake8ignore is not None:
                if hasattr(Flake8FileCollectionItem, "from_parent"):
                    collection_item = Flake8FileCollectionItem.from_parent(parent, name='__flake8__')
                    collection_item.fspaths = fspaths
                    collection_item.flake8ignore = flake8ignore
                    collection_item.maxlength = config._flake8maxlen
                    collection_item.maxcomplexity = config._flake8maxcomplexity
                    collection_item.showshource = config._flake8showshource
                    collection_item.statistics = config._flake8statistics
                else:
                    collection_item = Flake8FileCollectionItem(
                        '__flake8__',
                        parent,
                        fspaths=fspaths,
                        flake8ignore=flake8ignore,
                        maxlength=config._flake8maxlen,
                        maxcomplexity=config._flake8maxcomplexity,
                        showshource=config._flake8showshource,
                        statistics=config._flake8statistics
                    )
            config.hook.pytest_deselected(items=deselected_items)
            items[:] = [collection_item] + other_items


def pytest_unconfigure(config):
    """Flush cache at end of run."""
    if hasattr(config, "_flake8mtimes"):
        config.cache.set(HISTKEY, config._flake8mtimes)


class Flake8Error(Exception):
    """ indicates an error during flake8 checks. """


class Flake8Item(pytest.Item, pytest.File):

    def __init__(self, fspath, parent, flake8ignore=None, maxlength=None,
                 maxcomplexity=None, showshource=None, statistics=None):
        super(Flake8Item, self).__init__(fspath, parent)
        self._nodeid += "::FLAKE8"
        self.add_marker("flake8")
        self.flake8ignore = flake8ignore
        self.maxlength = maxlength
        self.maxcomplexity = maxcomplexity
        self.showshource = showshource
        self.statistics = statistics

    def setup(self):
        if hasattr(self.config, "_flake8mtimes"):
            flake8mtimes = self.config._flake8mtimes
        else:
            flake8mtimes = {}
        self._flake8mtime = self.fspath.mtime()
        old = flake8mtimes.get(str(self.fspath), (0, []))
        if old == [self._flake8mtime, self.flake8ignore]:
            pytest.skip("file(s) previously passed FLAKE8 checks")

    def runtest(self):
        call = py.io.StdCapture.call
        found_errors, out, err = call(
            check_files,
            [self.fspath],
            self.flake8ignore,
            self.maxlength,
            self.maxcomplexity,
            self.showshource,
            self.statistics)
        if found_errors:
            raise Flake8Error(out, err)
        # update mtime only if test passed
        # otherwise failures would not be re-run next time
        if hasattr(self.config, "_flake8mtimes"):
            self.config._flake8mtimes[str(self.fspath)] = (self._flake8mtime,
                                                           self.flake8ignore)

    def repr_failure(self, excinfo):
        if excinfo.errisinstance(Flake8Error):
            return excinfo.value.args[0]
        return super(Flake8Item, self).repr_failure(excinfo)

    def reportinfo(self):
        if self.flake8ignore:
            ignores = "(ignoring %s)" % " ".join(self.flake8ignore)
        else:
            ignores = ""
        return (self.fspath, -1, "FLAKE8-check%s" % ignores)

    def collect(self):
        return iter((self,))


class Flake8FileCollectionItem(pytest.Item):
    def __init__(self, name, parent, fspaths=None, flake8ignore=None, maxlength=None,
                 maxcomplexity=None, showshource=None, statistics=None):
        super(Flake8FileCollectionItem, self).__init__(name=name, parent=parent)
        self.add_marker("flake8")
        self.fspaths = fspaths
        self.filtered_fspaths = tuple()
        self.flake8ignore = flake8ignore
        self.maxlength = maxlength
        self.maxcomplexity = maxcomplexity
        self.showshource = showshource
        self.statistics = statistics

    def setup(self):
        if hasattr(self.config, "_flake8mtimes"):
            flake8mtimes = self.config._flake8mtimes
        else:
            flake8mtimes = {}
        self._flake8mtimes = {}
        self.filtered_fspaths = []
        for fspath in self.fspaths:
            flake8mtime = fspath.mtime()
            self._flake8mtimes[str(fspath)] = flake8mtime
            old = flake8mtimes.get(str(fspath), (0, []))
            if old == [flake8mtime, self.flake8ignore]:
                continue
            self.filtered_fspaths.append(fspath)

    def runtest(self):
        call = py.io.StdCapture.call
        found_errors, out, err = call(
            check_files,
            self.filtered_fspaths,
            self.flake8ignore,
            self.maxlength,
            self.maxcomplexity,
            self.showshource,
            self.statistics)
        if found_errors:
            raise Flake8Error(out, err)
        # update mtime only if test passed
        # otherwise failures would not be re-run next time
        if hasattr(self.config, "_flake8mtimes"):
            for fspath in self.filtered_fspaths:
                self.config._flake8mtimes[str(fspath)] = (
                    self._flake8mtimes[str(fspath)],
                    self.flake8ignore,
                )

    def repr_failure(self, excinfo):
        if excinfo.errisinstance(Flake8Error):
            return excinfo.value.args[0]
        return super(Flake8Item, self).repr_failure(excinfo)

    def reportinfo(self):
        if self.flake8ignore:
            ignores = "(ignoring %s)" % " ".join(self.flake8ignore)
        else:
            ignores = ""
        return (self.fspath, -1, "FLAKE8-check%s" % ignores)

    def collect(self):
        return iter((self,))


class Ignorer:
    def __init__(self, ignorelines, coderex=re.compile(r"[EW]\d\d\d")):
        self.ignores = ignores = []
        for line in ignorelines:
            i = line.find("#")
            if i != -1:
                line = line[:i]
            try:
                glob, ign = line.split(None, 1)
            except ValueError:
                glob, ign = None, line
            if glob and coderex.match(glob):
                glob, ign = None, line
            ign = ign.split()
            if "ALL" in ign:
                ign = None
            if glob and "/" != os.sep and "/" in glob:
                glob = glob.replace("/", os.sep)
            ignores.append((glob, ign))

    def __call__(self, path):
        l = []  # noqa: E741
        for (glob, ignlist) in self.ignores:
            if not glob or path.fnmatch(glob):
                if ignlist is None:
                    return None
                l.extend(ignlist)
        return l


def check_files(paths, flake8ignore, maxlength, maxcomplexity,
                showshource, statistics):
    """Run flake8 over a single file, and return the number of failures."""
    args = []
    if maxlength:
        args += ['--max-line-length', maxlength]
    if maxcomplexity:
        args += ['--max-complexity', maxcomplexity]
    if showshource:
        args += ['--show-source']
    if statistics:
        args += ['--statistics']
    app = application.Application()
    if not hasattr(app, 'parse_preliminary_options_and_args'):  # flake8 >= 3.8
        prelim_opts, remaining_args = app.parse_preliminary_options(args)
        config_finder = config.ConfigFileFinder(
            app.program,
            prelim_opts.append_config,
            config_file=prelim_opts.config,
            ignore_config_files=prelim_opts.isolated,
        )
        app.find_plugins(config_finder)
        app.register_plugin_options()
        app.parse_configuration_and_cli(config_finder, remaining_args)
    else:
        app.parse_preliminary_options_and_args(args)
        app.make_config_finder()
        app.find_plugins()
        app.register_plugin_options()
        app.parse_configuration_and_cli(args)
    if flake8ignore:
        app.options.ignore = flake8ignore
    app.make_formatter()  # fix this
    if hasattr(app, 'make_notifier'):
        # removed in flake8 3.7+
        app.make_notifier()
    app.make_guide()
    app.make_file_checker_manager()
    app.run_checks(list(map(str, paths)))
    app.formatter.start()
    app.report_errors()
    app.formatter.stop()
    return app.result_count
