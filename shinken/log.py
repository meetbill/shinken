#!/usr/bin/env python
# -*- coding: utf-8 -*-

# Copyright (C) 2009-2014:
#     Gabes Jean, naparuba@gmail.com
#     Gerhard Lausser, Gerhard.Lausser@consol.de
#     Gregory Starck, g.starck@gmail.com
#     Hartmut Goebel, h.goebel@goebel-consult.de
#
# This file is part of Shinken.
#
# Shinken is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Shinken is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with Shinken.  If not, see <http://www.gnu.org/licenses/>.

import logging
import sys
import os
import stat
import inspect
from logging import Handler, Formatter, StreamHandler, NOTSET, FileHandler
from logging.handlers import TimedRotatingFileHandler

from brok import Brok

try:
    from shinken.misc.termcolor import cprint
except (SyntaxError, ImportError), exp:
    # Outch can't import a cprint, do a simple print
    def cprint(s, color='', end=''):
        print s

# obj = None
# name = None
human_timestamp_log = False

_brokhandler_ = None


#defaultFormatter = Formatter('[%(created)i] %(levelname)s: %(message)s')
defaultFormatter = Formatter('%(levelname)s: %(asctime)s: %(thread)d %(message)s')
#defaultFormatter_named = Formatter('[%(created)i] %(levelname)s: [%(name)s] %(message)s')
defaultFormatter_named = Formatter('%(levelname)s: %(asctime)s: %(thread)d [%(name)s] %(message)s')
humanFormatter = Formatter('[%(asctime)s] %(levelname)s: %(message)s', '%a %b %d %H:%M:%S %Y')
humanFormatter_named = Formatter('[%(asctime)s] %(levelname)s: [%(name)s] %(message)s',
                                 '%a %b %d %H:%M:%S %Y')
nagFormatter = Formatter('[%(created)i] %(message)s')

class BrokHandler(Handler):
    """
    This log handler is forwarding log messages as broks to the broker.

    Only messages of level higher than DEBUG are send to other
    satellite to not risk overloading them.
    """

    def __init__(self, broker):
        # Only messages of level INFO or higher are passed on to the
        # broker. Other handlers have a different level.
        Handler.__init__(self, logging.INFO)
        self._broker = broker

    def emit(self, record):
        try:
            msg = self.format(record)
            brok = Brok('log', {'log': msg + '\n'})
            self._broker.add(brok)
        except Exception:
            self.handleError(record)


class ColorStreamHandler(StreamHandler):
    def emit(self, record):
        try:
            msg = self.format(record)
            colors = {'DEBUG': 'cyan', 'INFO': 'magenta',
                      'WARNING': 'yellow', 'CRITICAL': 'magenta', 'ERROR': 'red'}
            cprint(msg, colors[record.levelname])
        except UnicodeEncodeError:
            print msg.encode('ascii', 'ignore')
        except Exception:
            self.handleError(record)


class Log(logging.Logger):
    """
    Shinken logger class, wrapping access to Python logging standard library.
    See : https://docs.python.org/2/howto/logging.html#logging-flow for more detail about
    how log are handled"""

    def __init__(self, name="Shinken", level=NOTSET, log_set=False):
        logging.Logger.__init__(self, name, level)
        self.pre_log_buffer = []
        self.log_set = log_set

    def _get_backframe_info(self, f):
        return f.f_back.f_code.co_filename, f.f_back.f_lineno, f.f_back.f_code.co_name

    def setLevel(self, level):
        """ Set level of logger and handlers.
        The logger need the lowest level (see link above)
        """
        if not isinstance(level, int):
            level = getattr(logging, level, None)
            if not level or not isinstance(level, int):
                raise TypeError('log level must be an integer')
        # Not very useful, all we have to do is no to set the level > info for the brok handler
        self.level = min(level, logging.INFO)
        # Only set level to file and/or console handler
        for handler in self.handlers:
            if isinstance(handler, BrokHandler):
                continue
            handler.setLevel(level)


    def load_obj(self, object, name_=None):
        """ We load the object where we will put log broks
        with the 'add' method
        """
        global _brokhandler_
        _brokhandler_ = BrokHandler(object)
        if name_ is not None or self.name is not None:
            if name_ is not None:
                self.name = name_
            # We need to se the name format to all other handlers
            for handler in self.handlers:
                handler.setFormatter(defaultFormatter_named)
            _brokhandler_.setFormatter(defaultFormatter_named)
        else:
            _brokhandler_.setFormatter(defaultFormatter)
        self.addHandler(_brokhandler_)


    def register_local_log(self, path, level=None, purge_buffer=True):
        """The shinken logging wrapper can write to a local file if needed
        and return the file descriptor so we can avoid to
        close it.

        Add logging to a local log-file.

        The file will be rotated once a day
        """
        self.log_set = True
        # Todo : Create a config var for backup count
        if os.path.exists(path) and not stat.S_ISREG(os.stat(path).st_mode):
            # We don't have a regular file here. Rotate may fail
            # It can be one of the stat.S_IS* (FIFO? CHR?)
            handler = FileHandler(path)
        else:
            handler = TimedRotatingFileHandler(path, 'midnight', backupCount=5)
        if level is not None:
            handler.setLevel(level)
        if self.name is not None:
            handler.setFormatter(defaultFormatter_named)
        else:
            handler.setFormatter(defaultFormatter)
        self.addHandler(handler)

        # Ok now unstack all previous logs
        if purge_buffer:
            self._destack()

        # Todo : Do we need this now we use logging?
        return handler.stream.fileno()


    def set_human_format(self, on=True):
        """
        Set the output as human format.

        If the optional parameter `on` is False, the timestamps format
        will be reset to the default format.
        """
        global human_timestamp_log
        human_timestamp_log = bool(on)

        # Apply/Remove the human format to all handlers except the brok one.
        for handler in self.handlers:
            if isinstance(handler, BrokHandler):
                continue

            if self.name is not None:
                handler.setFormatter(human_timestamp_log and humanFormatter_named or
                                     defaultFormatter_named)
            else:
                handler.setFormatter(human_timestamp_log and humanFormatter or defaultFormatter)


    # Stack logs if we don't open a log file so we will be able to flush them
    # Stack max 500 logs (no memory leak please...)
    def _stack(self, level, msg, args, kwargs):
        if self.log_set:
            return
        self.pre_log_buffer.append((level, msg, args, kwargs))
        if len(self.pre_log_buffer) > 500:
            self.pre_log_buffer = self.pre_log_buffer[2:]


    # Ok, we are opening a log file, flush all the logs now
    def _destack(self):
        for (level, msg, args, kwargs) in self.pre_log_buffer:
            f = getattr(logging.Logger, level, None)
            if f is None:
                self.warning('Missing level for a log? %s', level)
                continue
            f(self, msg, *args, **kwargs)


    def debug(self, msg, *args, **kwargs):
        f = inspect.currentframe()
        filename, lineno, funcname = self._get_backframe_info(f)
        cur_info = "[file]:{filename} [line]:{lineno} [func]:{func} ".format(filename=filename, lineno=lineno, func=funcname)
        msg = cur_info + msg
        self._stack('debug', msg, args, kwargs)
        logging.Logger.debug(self, msg, *args, **kwargs)


    def info(self, msg, *args, **kwargs):
        f = inspect.currentframe()
        filename, lineno, funcname = self._get_backframe_info(f)
        cur_info = "[file]:{filename} [line]:{lineno} [func]:{func} ".format(filename=filename, lineno=lineno, func=funcname)
        msg = cur_info + msg
        self._stack('info', msg, args, kwargs)
        logging.Logger.info(self, msg, *args, **kwargs)



    def warning(self, msg, *args, **kwargs):
        f = inspect.currentframe()
        filename, lineno, funcname = self._get_backframe_info(f)
        cur_info = "[file]:{filename} [line]:{lineno} [func]:{func} ".format(filename=filename, lineno=lineno, func=funcname)
        msg = cur_info + msg
        self._stack('warning', msg, args, kwargs)
        logging.Logger.warning(self, msg, *args, **kwargs)


    def error(self, msg, *args, **kwargs):
        f = inspect.currentframe()
        filename, lineno, funcname = self._get_backframe_info(f)
        cur_info = "[file]:{filename} [line]:{lineno} [func]:{func} ".format(filename=filename, lineno=lineno, func=funcname)
        msg = cur_info + msg
        self._stack('error', msg, args, kwargs)
        logging.Logger.error(self, msg, *args, **kwargs)


    def debug_trace(self, msg, *args, **kwargs):
        stack = inspect.stack()
        msg = msg + "\n" + "[call stack]:"
        for i in range(1, len(stack) ):       # "1" : stack[0] 是调用 stack() 处的 frame_record, 丢弃.
            filename = stack[i][1]
            lineno = stack[i][2]
            funcName = stack[i][3]
            code = stack[i][4][0]
            msg=msg +"\n" + '\t\t<frame %d>:%s, line:%d  func:%s' % (i - 1, filename, lineno, code[0 : -1] )

        logging.Logger.debug(self, msg, *args, **kwargs)





# --- create the main logger ---
logging.setLoggerClass(Log)
logger = logging.getLogger('Shinken')
if hasattr(sys.stdout, 'isatty'):
    csh = ColorStreamHandler(sys.stdout)
    if logger.name is not None:
        csh.setFormatter(defaultFormatter_named)
    else:
        csh.setFormatter(defaultFormatter)
    logger.addHandler(csh)


def naglog_result(level, result, *args):
    """
    Function use for old Nag compatibility. We to set format properly for this call only.

    Dirty Hack to keep the old format, we should have another logger and
    use one for Shinken logs and another for monitoring data
    """
    prev_formatters = []
    for handler in logger.handlers:
        prev_formatters.append(handler.formatter)
        handler.setFormatter(nagFormatter)

    log_fun = getattr(logger, level)

    if log_fun:
        log_fun(result)

    for index, handler in enumerate(logger.handlers):
        handler.setFormatter(prev_formatters[index])
