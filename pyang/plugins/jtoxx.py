# Copyright (c) 2015 by Ladislav Lhotka, CZ.NIC <lhotka@nic.cz>
#
# Pyang plugin generating a driver file for JSON->XML translation.
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

"""JTOX output plugin

This plugin takes a YANG data model and produces a JSON driver file
that can be used by the *json2xml* script for translating a valid JSON
configuration or state data to XML.
"""

import os
import json

from pyang import plugin, statements, error
from pyang.util import unique_prefixes

def pyang_plugin_init():
    plugin.register_plugin(JtoXPlugin())

class JtoXPlugin(plugin.PyangPlugin):
    def add_output_format(self, fmts):
        self.multiple_modules = True
        fmts['jtox'] = self

    def setup_fmt(self, ctx):
        ctx.implicit_errors = False

    def emit(self, ctx, modules, fd):
        """Main control function.
        """
        for (epos, etag, eargs) in ctx.errors:
            if error.is_error(error.err_level(etag)):
                raise error.EmitError("JTOX plugin needs a valid module")
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
            ndata = [ch.keyword]
            if ch.keyword == "container":
                ndata.append({})
                self.process_children(ch, ndata[1], nmod)
            elif ch.keyword == "list":
                ndata.append({})
                self.process_children(ch, ndata[1], nmod)
                ndata.append([(k.i_module.i_modulename, k.arg)
                              for k in ch.i_key])
            elif ch.keyword in ["leaf", "leaf-list"]:
                ndata.append(self.base_type(ch.search_one("type")))
                leafattrs = {}
                if (ch.search_one("description") is not None):
                    leafattrs['description'] = ch.search_one("description").arg;
                if (ch.search_one("reference") is not None):
                    leafattrs['reference'] = ch.search_one("reference").arg;
                if (ch.search_one("config") is not None):
                    leafattrs['config'] = False if (ch.search_one("config").arg == "false") else True;
                if (ch.search_one("default") is not None):
                    leafattrs['default'] = ch.search_one("default").arg;
                if (ch.search_one("mandatory") is not None):
                    leafattrs['mandatory'] = False if (ch.search_one("mandatory").arg == "false") else True;
                if (len(leafattrs) > 0):
                    ndata.append(leafattrs);
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
        if type.arg == "decimal64":
            decarr = [type.arg]
            decdict = {};
            decdict["fraction-digits"] = int(type.search_one("fraction-digits").arg)
            range = type.search_one("range")
            if (range is not None):
                decdict["range"] = str(type.search_one("range").arg)
            decarr.append(decdict)
            return decarr
        elif type.arg in ["uint8", "uint16", "uint32", "uint64"]:
            intarr = [type.arg]
            intdict = {}
            range = type.search_one("range")
            if (range is not None):
                intdict["range"] = str(type.search_one("range").arg)
            intarr.append(intdict)
            return intarr
        elif type.arg == "string":
            strarr = [type.arg]
            strdict = {}
            len = type.search_one("length")
            if (len is not None):
                strdict["length"] = str(type.search_one("length").arg)
            pattern = type.search_one("pattern");
            if (pattern is not None):
                strarr["pattern"] = str(type.search_one("pattern").arg)
            return strarr
        elif type.arg == "union":
            return [type.arg, [self.base_type(x) for x in type.i_type_spec.types]]
        else:
            return type.arg
