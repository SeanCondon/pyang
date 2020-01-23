import sys
from numbers import Integral as int_types

from .error import err_add

str_types = str if isinstance(u'', str) else (str, type(u''))

def attrsearch(tag, attr, list):
    for x in list:
        if getattr(x, attr) == tag:
            return x
    return None

def keysearch(tag, n, list):
    for x in list:
        if x[n] == tag:
            return x
    return None

def dictsearch(val, dict):
    for key in dict:
        if dict[key] == val:
            return key
    return None

def is_prefixed(identifier):
    return isinstance(identifier, tuple) and len(identifier) == 2

def is_local(identifier):
    return isinstance(identifier, str_types)

def keyword_to_str(keyword):
    if keyword == '__tmp_augment__':
        return "undefined"
    elif is_prefixed(keyword):
        (prefix, keyword) = keyword
        return prefix + ":" + keyword
    else:
        return keyword

def guess_format(text):
    """Guess YANG/YIN format

    If the first non-whitespace character is '<' then it is XML.
    Return 'yang' or 'yin'"""
    format = 'yang'
    i = 0
    while i < len(text) and text[i].isspace():
        i += 1
    if i < len(text):
        if text[i] == '<':
            format = 'yin'
    return format

def listsdelete(x, xs):
    """Return a new list with x removed from xs"""
    i = xs.index(x)
    return xs[:i] + xs[(i+1):]

def get_latest_revision(module):
    latest = None
    for r in module.search('revision'):
        if latest is None or r.arg > latest:
            latest = r.arg
    if latest is None:
        #return datetime.date.today().isoformat()
        return "unknown"
    else:
        return latest

def prefix_to_modulename_and_revision(module, prefix, pos, errors):
    if prefix == '':
        return module.arg, None
    if prefix == module.i_prefix:
        return module.arg, None
    try:
        (modulename, revision) = module.i_prefixes[prefix]
    except KeyError:
        if prefix not in module.i_missing_prefixes:
            err_add(errors, pos, 'PREFIX_NOT_DEFINED', prefix)
        module.i_missing_prefixes[prefix] = True
        return None, None
    # remove the prefix from the unused
    if prefix in module.i_unused_prefixes:
        del module.i_unused_prefixes[prefix]
    return modulename, revision

def prefix_to_module(module, prefix, pos, errors):
    if prefix == '':
        return module
    if prefix == module.i_prefix:
        return module
    modulename, revision = \
        prefix_to_modulename_and_revision(module, prefix, pos, errors)
    if modulename is None:
        return None
    return module.i_ctx.get_module(modulename, revision)

def unique_prefixes(context):
    """Return a dictionary with unique prefixes for modules in `context`.

    Keys are 'module' statements and values are prefixes,
    disambiguated where necessary.
    """
    res = {}
    for m in context.modules.values():
        if m.keyword == "submodule":
            continue
        prf = new = m.i_prefix
        suff = 0
        while new in res.values():
            suff += 1
            new = "%s%x" % (prf, suff)
        res[m] = new
    return res

files_read = {}
def report_file_read(filename, extra=None):
    import os.path
    realpath = os.path.realpath(filename)
    read = "READ" if realpath in files_read else "read"
    extra = (" " + extra) if extra else ""
    sys.stderr.write("# %s %s%s\n" % (read, filename, extra))
    files_read[realpath] = True


def search_data_node(children, modulename, identifier, last_skipped = None):
    skip = ['choice', 'case', 'input', 'output']
    if last_skipped is not None:
        skip.append(last_skipped)
    for child in children:
        if child.keyword in skip:
            r = search_data_node(child.i_children,
                                 modulename, identifier)
            if r is not None:
                return r
        elif ((child.arg == identifier) and
              (child.i_module.i_modulename == modulename)):
            return child
    return None

def closest_ancestor_data_node(node):
    if node.keyword in ['choice', 'case']:
        return closest_ancestor_data_node(node.parent)
    return node

def data_node_up(node):
    skip = ['choice', 'case', 'input', 'output']
    p = node.parent
    if node.keyword in skip:
        return data_node_up(p)
    if p and p.keyword in skip:
        return closest_ancestor_data_node(p)
    return p
