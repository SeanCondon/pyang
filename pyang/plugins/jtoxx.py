# Copyright (c) 2018 by Sean Condon <sean.condon@posteo.net>
# Based off jtox plugin by Ladislav Lhotka, CZ.NIC <lhotka@nic.cz>
#
# Pyang plugin generating metadata that can be used by YangUiComponents
#
# Permission to use, copy, modify, and/or distribute this software for any
# purpose with or without fee is hereby granted, provided that the above
# copyright notice and this permission notice appear in all copies.
#
# THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES
# WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF
# MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR
# ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES
# WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN
# ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF
# OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.

"""JTOXX output plugin

This plugin takes a YANG data model and produces a JSON driver file
that can be used by YangUiComponents set of Angular Web Components
"""

import os
import json

from pyang import plugin, statements, error
from pyang.util import unique_prefixes

def pyang_plugin_init():
    plugin.register_plugin(JtoXXPlugin())

class JtoXXPlugin(plugin.PyangPlugin):
    def add_output_format(self, fmts):
        self.multiple_modules = True
        fmts['jtoxx'] = self

    def setup_fmt(self, ctx):
        ctx.implicit_errors = False

    def emit(self, ctx, modules, fd):
        """Main control function.
        """
        for (epos, etag, eargs) in ctx.errors:
            if error.is_error(error.err_level(etag)):
                raise error.EmitError("JTOXX plugin needs a valid module")
        tree = {}
        mods = {}
        annots = {}
        for m,p in unique_prefixes(ctx).items():
            mods[m.i_modulename] = [p, m.search_one("namespace").arg]
        for module in modules:
            for ann in module.search(("ietf-yang-metadata", "annotation")):
                typ = ann.search_one("type")
                annots[module.arg + ":" + ann.arg] = (
                    "string" if typ is None else self.base_type(typ))
        for module in modules:
            self.process_children(module, tree, None)
        json.dump({"modules": mods, "tree": tree, "annotations": annots}, fd)

    def process_children(self, node, parent, pmod):
        """Process all children of `node`, except "rpc" and "notification".
        """
        for ch in node.i_children:
            if ch.keyword in ["rpc", "notification"]: continue
            if ch.keyword in ["choice", "case"]:
                self.process_children(ch, parent, pmod)
                continue
            if ch.i_module.i_modulename == pmod:
                nmod = pmod
                nodename = ch.arg
            else:
                nmod = ch.i_module.i_modulename
                nodename = "%s:%s" % (nmod, ch.arg)
            ndataDict = {}
            ndataDict["nodeType"]=ch.keyword
            description=ch.search_one("description")
            if (ch.search_one("description") is not None):
                ndataDict["description"]=description.arg
            if (ch.search_one("reference") is not None):
                ndataDict['reference'] = ch.search_one("reference").arg;
            if (ch.search_one("config") is not None):
                ndataDict['config'] = False if (ch.search_one("config").arg == "false") else True;
            ndata = [ndataDict]
            if ch.keyword == "container":
                self.process_children(ch, ndataDict, nmod)
            elif ch.keyword == "list":
                self.process_children(ch, ndataDict, nmod)
                ndataDict['keys'] = ([(k.arg)
                              for k in ch.i_key])
                if (ch.search_one("min-elements") is not None):
                    ndataDict['min-elements'] = ch.search_one("max-elements").arg;
                if (ch.search_one("max-elements") is not None):
                    ndataDict['max-elements'] = ch.search_one("max-elements").arg;
            elif ch.keyword in ["leaf", "leaf-list"]:
                ndataDict["dataType"]=self.base_type(ch.search_one("type"))
                # if (ch.search_one("description") is not None):
                #     leafattrs['description'] = ch.search_one("description").arg;
                if (ch.search_one("default") is not None):
                    ndataDict['default'] = ch.search_one("default").arg;
                if (ch.search_one("mandatory") is not None):
                    ndataDict['mandatory'] = False if (ch.search_one("mandatory").arg == "false") else True;
                if (ch.search_one("units") is not None):
                    ndataDict['units'] = ch.search_one("units").arg;
            modname = ch.i_module.i_modulename
            parent[nodename] = ndata

    def base_type(self, type):
        """Return the base type of `type`."""
        while 1:
            if type.arg == "leafref":
                node = type.i_type_spec.i_target_node
            elif type.i_typedef is None:
                break
            else:
                node = type.i_typedef
            type = node.search_one("type")

        arr = []
        dict = {};
        dict['type'] = type.arg
        arr.append(dict)
        if type.arg == "decimal64":
            dict["fraction-digits"] = int(type.search_one("fraction-digits").arg)
            range = type.search_one("range")
            if (range is not None):
                dict["range"] = str(type.search_one("range").arg)
            return arr
        elif type.arg in ["uint8", "uint16", "uint32", "uint64"]:
            range = type.search_one("range")
            if (range is not None):
                dict["range"] = str(type.search_one("range").arg)
            return arr
        elif type.arg == "string":
            len = type.search_one("length")
            if (len is not None):
                dict["length"] = str(len.arg)
            pattern = type.search_one("pattern");
            if (pattern is not None):
                dict["pattern"] = str(pattern.arg)
            return arr
        elif type.arg == "union":
            return [type.arg, [self.base_type(x) for x in type.i_type_spec.types]]
        else:
            return type.arg
