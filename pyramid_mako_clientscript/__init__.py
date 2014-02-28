__author__ = 'tarzan'

import os
import sys
import re
from pyramid.events import BeforeRender
from pyramid.threadlocal import get_current_request
from mako.runtime import supports_caller, Context as MakoContext
from collections import OrderedDict
from pkg_resources import resource_filename

TYPE_CSS = 'css'
TYPE_JS = 'javascript'
TYPE_SCRIPT = 'script'

POS_HEAD = 'HEAD'
POS_BEGIN = 'BEGIN'
POS_END = 'END'

MAKO_VAR_NAME = None
STATIC_DIR = None

class _ClientScriptPool(OrderedDict):
    def __setitem__(self, key, value):
        if key is None:
            value = self.get(key, '') + value
        super(_ClientScriptPool, self).__setitem__(key, value)

class ClientScriptManager(object):
    _end_patterns = [
        (
            True,
            re.compile(r"(</body\s*>)", re.IGNORECASE),
        ),
        (
            True,
            re.compile(r"(</html\s*>)", re.IGNORECASE),
        ),
        ]
    _head_patterns = [
        (
            True,
            re.compile(r"(</head\s*>|<title\b[^>]*>|<body\b[^>]*>)", re.IGNORECASE),
        ),
        (
            False,
            re.compile(r"(<html\b[^>]*>)", re.IGNORECASE),
        ),
        ]
    _begin_patterns = [
        (
            False,
            re.compile(r"(<body\b[^>]*>)", re.IGNORECASE),
        ),
        ]

    def __init__(self, request):
        self.request = request
        self.pools = {
            POS_HEAD: {
                TYPE_CSS: _ClientScriptPool(),
                TYPE_SCRIPT: _ClientScriptPool(),
                TYPE_JS: _ClientScriptPool(),
            },
            POS_BEGIN: {
                TYPE_CSS: _ClientScriptPool(),
                TYPE_SCRIPT: _ClientScriptPool(),
                TYPE_JS: _ClientScriptPool(),
            },
            POS_END: {
                TYPE_CSS: _ClientScriptPool(),
                TYPE_SCRIPT: _ClientScriptPool(),
                TYPE_JS: _ClientScriptPool(),
            },
        }

    def register(self, type, position, name, content):
        try:
            pool = self.pools[position][type]
            if isinstance(content, unicode):
                content = content.encode('utf-8')
            pool[name] = content
        except KeyError:
            raise RuntimeWarning("Unsupported client script %s::%s" % (position, type))
        return ''

    def register_css(self, content, name=None, position=POS_HEAD):
        return self.register(TYPE_CSS, position, name, content)

    def register_css_file(self, file, name=None, position=POS_HEAD, **kwargs):
        attrs = " ".join(['%s="%s"' % (k, v) for k, v in kwargs.items()])
        content = '<link href="%(file)s" rel="stylesheet"%(attrs)s>' % \
                  {
                      "file": file,
                      "attrs": (" " + attrs) if attrs else ""
                  }
        return self.register_css(content, name, position)

    def register_js(self, content, name=None, position=POS_END):
        return self.register(TYPE_JS, position, name, content)

    def register_js_file(self, file, name=None, position=POS_END, **kwargs):
        attrs = " ".join(['%s="%s"' % (k, v) for k, v in kwargs.items()])
        content = '<script language="javascript" src="%(file)s"%(attrs)s></script>' % \
                  {
                      "file": file,
                      "attrs": (" " + attrs) if attrs else ""
                  }
        return self.register_js(content, name, position)

    def script(self, content, name=None, position=POS_HEAD):
        return self.register(TYPE_SCRIPT, position, name, content)

    css = register_css
    css_file = register_css_file
    js = register_js
    js_file = register_js_file

    def _pack_scripts(self, pools):
        pool_scripts = ["".join(pool.values()) for pool in pools.values()]
        scripts = "".join(pool_scripts)
        return scripts

    def _attach_script(self, scripts, patterns, html, before=True):
        if not scripts:
            return html
        repl_scripts = scripts.replace(chr(92), r"\\") # chr(92) = \
        for pattern_before, pattern in patterns:
            repl = repl_scripts + r"\1" if pattern_before else r"\1" + repl_scripts
            html, count = re.subn(
                pattern=pattern,
                repl=repl,
                string=html,
                count=1)
            if count:
                return html
        if before:
            html = scripts + html
        else:
            html = html + scripts
        return html

    def _attach_head_scripts(self, html):
        return self._attach_script(
            scripts=self._pack_scripts(self.pools[POS_HEAD]),
            patterns=self._head_patterns,
            html=html,
            before=True,
        )

    def _attach_begin_scripts(self, html):
        return self._attach_script(
            scripts=self._pack_scripts(self.pools[POS_BEGIN]),
            patterns=self._begin_patterns,
            html=html,
            before=True,
        )

    def _attach_end_scripts(self, html):
        return self._attach_script(
            scripts=self._pack_scripts(self.pools[POS_END]),
            patterns=self._end_patterns,
            html=html,
            before=False,
        )

    def attach_to_response(self, html):
        html = self._attach_begin_scripts(html)
        html = self._attach_head_scripts(html)
        html = self._attach_end_scripts(html)
        return html

def client_script_tween_factory(handler, registry):
    def client_script_tween(request):
        cs_manager = ClientScriptManager(request)
        request.client_script_manager = cs_manager
        response = handler(request)
        try:
            csmgr = request.client_script_manager
        except AttributeError:
            csmgr = None
        if csmgr is not None:
            response.body = csmgr.attach_to_response(response.body)
        return response

    return client_script_tween

def static_url(path, **kw):
    return get_current_request().static_url(
        os.path.join(STATIC_DIR, path), **kw)

def add_renderer_globals(event):
    event[MAKO_VAR_NAME] = sys.modules[__name__]
    if STATIC_DIR:
        event['static_url'] = static_url

def _get_csmgr():
    return get_current_request().client_script_manager

def _context_to_content(context):
    if isinstance(context, basestring):
        return context
    if isinstance(context, MakoContext):
        context._push_buffer()
        context.caller_stack._get_caller().body()
        buf = context._pop_buffer()
        return buf.getvalue()
    return str(context)

def js_file(*args, **kwargs):
    return _get_csmgr().js_file(*args, **kwargs)

def css_file(*args, **kwargs):
    return _get_csmgr().css_file(*args, **kwargs)

@supports_caller
def js(context, *args, **kwargs):
    context = _context_to_content(context)
    return _get_csmgr().js(context, *args, **kwargs)

@supports_caller
def css(context, *args, **kwargs):
    context = _context_to_content(context)
    return _get_csmgr().css(context, *args, **kwargs)


def includeme(config):
    """
    :type config: pyramid.config.Configurator
    """
    global MAKO_VAR_NAME, STATIC_DIR
    settings = config.get_settings()
    def sget(key, default=None):
        return settings.get("clientscript." + key, default)
    MAKO_VAR_NAME = sget('mako_var_name', 'ClientScript')
    STATIC_DIR = sget('static_dir')

    config.add_subscriber(add_renderer_globals, BeforeRender)
    config.add_tween(__name__ + '.client_script_tween_factory')
