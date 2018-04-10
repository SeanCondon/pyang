"""SQL plugin

Creates SQL schema from YANG files.
"""

from pyang import plugin, statements, error
from pyang.util import unique_prefixes
from os.path import normpath, basename, splitext

import optparse

"""Dictionary of SQL namespaces. Key will be the prefix of yang model"""
schemata={}

type_class = dict([
    ("int8", "INTEGER"),
    ("uint8", "INTEGER"),
    ("int16", "INTEGER"),
    ("uint16", "INTEGER"),
    ("int32", "INTEGER"),
    ("uint32", "INTEGER"),
    ("int64", "INTEGER"),
    ("uint64", "INTEGER"),
    ("decimal64", "FLOAT"),
    ("boolean", "INTEGER"),
    ("enumeration", "TEXT"),
    ("binary", "TEXT")])

type_class.update((t, "TEXT") for t in
                  ("string", "identityref", "instance-identifier", "bits", 'leafref', 'empty', 'anyxml'))


def pyang_plugin_init():
    plugin.register_plugin(SqlPlugin())


class SqlPlugin(plugin.PyangPlugin):

    outputDoc = ""
    headersH = ""
    headersC = ""
    attachList = list()

    ancestorcount = 100
    sampledata = False
    dbschema = False
    headers = False

    def add_opts(self, optparser):
        optlist = [
            optparse.make_option("--sql-ancestor-count",
                                 dest="ancestorcount",
                                 default=100,
                                 help="The number of ancestors to include in the name of a table (default 100 (all))."),
            optparse.make_option("--sql-sample-data",
                                 action="store_true",
                                 dest="sqlsample",
                                 default=False,
                                 help="Export sample data along with table definitions."),
            optparse.make_option("--db-schema",
                                 action="store_true",
                                 dest="dbschema",
                                 default=False,
                                 help="Prefix each table with an autogenerated db schema name"),
            optparse.make_option("--headers",
                                 action="store_true",
                                 dest="headers",
                                 default=False,
                                 help="Generate a comment block with C file constants"),
        ]
        g = optparser.add_option_group(
            "SQL generator output specific options")
        g.add_options(optlist)

    def add_output_format(self, fmts):
        self.multiple_modules = True
        fmts['sql'] = self

    def setup_fmt(self, ctx):
        ctx.implicit_errors = False

    def emit(self, ctx, modules, fd):
        """Main control function.

        Set up the top-level parts of the schema, then process
        recursively all nodes in all data trees, and finally emit the
        serialized schema.
        """

        self.verbose=ctx.opts.verbose
        for (epos, etag, eargs) in ctx.errors:
            if error.is_error(error.err_level(etag)):
                raise error.EmitError("Sql plugin needs a valid module")
        self.real_prefix = unique_prefixes(ctx)

        self.ancestorcount = int(ctx.opts.ancestorcount)
        self.sampledata = bool(ctx.opts.sqlsample)
        self.dbschema = bool(ctx.opts.dbschema)
        self.headers = bool(ctx.opts.headers)

        genstmt = "--SQL DDL auto-generated by pyang with '-f sql'"
        genstmt += "\n--Generated with -sql-ancestor-count=" + str(self.ancestorcount)
        genstmt += ", -sql-sample-data:" + str(self.sampledata) + " -db-schema:" + str(self.dbschema)
        genstmt += " and -headers:" + str(self.headers)

        if self.headers:
            self.headersH += genstmt.replace("--", "//")
            headerkey = splitext(basename(normpath(str(ctx.opts.outfile))))[0].replace("-", "_").upper()
            self.headersH += "\n#ifndef " + headerkey + "_H\n#define " + headerkey + "_H\n\n"

            self.headersC += genstmt.replace("--", "//")
            self.headersC += "\n#include <string.h>"
            self.headersC += "\n#include \"" + splitext(normpath(str(ctx.opts.outfile)))[0] + ".h\"\n"
            self.headersC += "\nconst char* getKey(const char *keyPath) {"

        for module in self.real_prefix.keys():
            self.process_module(module)

        attachstmt = ""
        for a in self.attachList:
            for mod in self.real_prefix:
                if self.real_prefix[mod] == a:
                    attachstmt += ("\n--ATTACH DATABASE '" + mod.arg)
                    if mod.i_latest_revision is not None:
                        attachstmt += ("@" + mod.i_latest_revision)
                    attachstmt += (".db' AS " + self.underscore(a) + ";")

        fd.write("%s%s%s\n" % (genstmt, attachstmt, self.outputDoc))
        if self.headers:
            #   Write out Headers in both .h and .c format
            self.headersH += "\n\n//Database file name constants"
            for a in self.attachList:
                for mod in self.real_prefix:
                    if self.real_prefix[mod] == a:
                        self.headersH += "\n#define " + mod.arg.upper().replace("-", "_") + "_DB \"" + mod.arg
                        if mod.i_latest_revision is not None:
                            self.headersH += ("@" + mod.i_latest_revision)
                        self.headersH += ".db\""

            self.headersH += "\n\nconst char* getKey(const char *keyPath);\n"
            self.headersH += "\n#endif\n"
            outfilebase = splitext(normpath(str(ctx.opts.outfile)))[0]
            if ctx.opts.outfile is None:
                print("Headers\n", self.headersH)
            else:
                hfile = open(outfilebase + ".h", 'w')
                hfile.write(self.headersH)
                hfile.close()
                print("Header file created at ", outfilebase + ".h")

            self.headersC += "\n\t}\n\n\treturn NULL;\n}"
            if ctx.opts.outfile is None:
                print("C file\n", self.headersC)
            else:
                cfile = open(outfilebase + ".c", 'w')
                cfile.write(self.headersC)
                cfile.close()
                print("C file created at ", outfilebase + ".c")

    def process_module(self, yam):
        if self.real_prefix[yam] in schemata:
            print("Module "+yam.arg+" already processed. Continuing.")
            return
        ns_uri = yam.search_one("namespace").arg
        if self.verbose:
            print("Handling module "+yam.arg+" as "+self.real_prefix[yam]+":"+ns_uri)

        for ch in yam.i_children[:]:
            if ch.keyword == "rpc":
                None
                #   self.process_rpc(ch,yam) TODO Add function
            elif ch.keyword == "notification":
                None
                # self.process_notifications(ch,yam) TODO Add function
            else:
                continue
            yam.i_children.remove(ch)
        self.process_tables(yam, self.real_prefix[yam], toplevel=True)

    """Process all children of `node`.
    This is a recursive function that will follow containers and lists down the tree until it has no more children
    except for leafs
    For each iteration of this function a SQL CREATE TABLE is called (and if sample data is being generated an INSERT 
    statement is generated too)
    The output can look like its' in reverse order - this happens because each childs result is added to the file before
    its parents result is added
    Each time a container or list is found, 2 functions are called
    i) process columns - this goes off and finds any leafs and adds them to the column list
    ii) process tables - this does a further iteration to handle any child containers or lists of this object
    
    Lists are handled differently to Containers in that a container maintains a one-to-one relationship with a child 
    container. 
    
    That is inside the upper container there is a foreign key to the primary key of the lower container. This
    is marked NOT NULL, so that the child container must exist before the parent can be created, and its PK must be 
    used in the FK field of the parent. 

    On the other hand lists have a many to one relationship with their parent container. 0 to many of the list table 
    rows can exist which means the relationship goes the other way - the upper container does not have a FK to the 
    (lower) list - instead the (lower) list has a FK to the PK of the (upper) container.
    
    The arguments
    node: This is a YANG object - it could be the top level module, or a container or a list or a choice etc
    prefix: This is a string value with the YANG module's prefix e.g. 'if' or 'sys'
    childtables: This is an array that will be added to by the function - returning back to the caller the list of 
                    children (but only the containers) - this is used to build the parent child relationship  
    childlisttables: An array that will be added to by the function when it finds lists. It is used to return to the 
                    caller the list children, so that the delete trigger can include them
    ancestors[]: An array of strings of all of the parents of this node - used to create prefixes for table names
    toplevel: A boolean flag that marks the start of the recursion - should be True only on the call from module
    """
    def process_tables(self, node, prefix, childtables=[], childlisttables=[], ancestors=[], toplevel=False):
        chs = node.i_children

        if toplevel and len(chs) > 0:
            self.outputDoc += ("\n.open " + node.arg)
            if node.i_latest_revision is not None:
                self.outputDoc += "@" + node.i_latest_revision
            self.outputDoc += ".db"
            if self.headers:
                self.headersH += "\n\n//List key attributes for " + node.arg

        for ch in chs:
            if ch.keyword in ['leaf']:
                continue
            cfg = ch.search_one("config")
            if cfg is not None and cfg.arg == "false":
                continue
            sqlcreatetable = "\nCREATE TABLE "
            sqlinserttable = "\nINSERT INTO "
            if self.dbschema:  # Add in a schema name to the table name
                schema = self.underscore(prefix)
                sqlcreatetable += (schema + ".")
                sqlinserttable += (schema + ".")
            if prefix not in self.attachList:
                self.attachList.append(prefix)
            if self.verbose:
                print("Processing Tbl: ", end="")
                print(ch.arg+"("+ch.keyword+")")
            headerenums = ""
            # Very important that if the scope that adds to ancestors is changed here a corresponding change is made
            # where ancestors are popped from the list at the end of this method
            if not (toplevel or node.keyword in ['case']):  # Leave out the module name from ancestors
                ancestors.append(node.arg.replace("-", "_"))

            if ch.keyword in ['container']:
                childtables.append(ch)
                children = []
                childrenlist = []
                self.process_tables(ch, prefix, children, childrenlist, ancestors)
                cols, samplecols, headerenums = \
                    self.process_columns(ch, childtables=children, ancestors=ancestors)
                sqlcreatetable = "\n--container" + sqlcreatetable + self.underscore(ch.arg, ancestors) + cols + ";"
                sqlinserttable = "\n" + sqlinserttable + self.underscore(ch.arg, ancestors) + samplecols + ";\n"
                if len(children) > 0:
                    sqldeletetrigger = "\nCREATE TRIGGER " + self.underscore(ch.arg, ancestors) + \
                                       "_dt AFTER DELETE ON " + self.underscore(ch.arg, ancestors) + "\nBEGIN"
                    ancestors.append(ch.arg.replace("-", "_"))  # Temporarily add it in
                    for table in children:
                        triggerdelete = "\n    DELETE FROM " + self.underscore(table.arg, ancestors) +\
                                        " WHERE revision = OLD.revision;"
                        sqldeletetrigger += triggerdelete
                    for table in childrenlist:
                        triggerdelete = "\n    DELETE FROM " + self.underscore(table, ancestors) +\
                                        " WHERE revision = OLD.revision;"
                        sqldeletetrigger += triggerdelete
                    ancestors.pop()
                    sqldeletetrigger += "\nEND;"
                    sqlcreatetable += sqldeletetrigger

            elif ch.keyword in ['list']:
                childlisttables.append(ch.arg)
                children = []
                keys = ch.search("key")
                if self.headers:  # If C-style header files are required - this lines will be added to a .c and a .h
                    keybase = str.upper(self.underscore(ch.arg, ancestors))
                    self.headersH += "\n#define " + keybase + " \"/" + self.keypath(ch.arg, ancestors) + "\""
                    self.headersH += "\n#define " + keybase + "_KEY \""
                    isfirst = True
                    for key in keys:
                        if isfirst:
                            isfirst = False
                        else:
                            self.headersH += " "
                        self.headersH += key.arg.replace("-", "_")
                    self.headersH += "\""
                    isfirst = self.headersC.endswith("keyPath) {")
                    self.headersC += ("\n\t" if isfirst else "\n\t} else ") + \
                        "if (strcmp(" + keybase + ", keyPath) == 0) {\n\t\treturn " + keybase + "_KEY;"

                self.process_tables(ch, prefix, children, ancestors=ancestors)
                cols, samplecols, headerenums = \
                    self.process_columns(ch, childtables=children, keys=keys,
                                         ancestors=ancestors, parentkeys=self.getparentkeys(node), idx=0)
                sqlcreatetable = "\n--list" + sqlcreatetable + self.underscore(ch.arg, ancestors) + cols + ";\n"
                if self.sampledata:
                    sqlinserttable = "\n" + sqlinserttable + self.underscore(ch.arg, ancestors) + samplecols + ";\n"

                    # For lists we'd like a few instances to be created
                    cols, samplecols, headerenums = \
                        self.process_columns(ch, childtables=children, keys=keys, ancestors=ancestors,
                                             parentkeys=self.getparentkeys(node), idx=1)
                    sqlinserttable += "\nINSERT INTO "
                    if self.dbschema:  # Add in a schema name to the table name
                        sqlinserttable += (self.underscore(prefix) + ".")
                    sqlinserttable += self.underscore(ch.arg, ancestors) + samplecols + ";\n"

                    # And another with idx=2
                    cols, samplecols, headerenums = \
                        self.process_columns(ch, childtables=children, keys=keys, ancestors=ancestors,
                                             parentkeys=self.getparentkeys(node), idx=2)
                    sqlinserttable += "\nINSERT INTO "
                    if self.dbschema:  # Add in a schema name to the table name
                        sqlinserttable += (self.underscore(prefix) + ".")
                    sqlinserttable += self.underscore(ch.arg, ancestors) + samplecols + ";\n"

            elif ch.keyword in ['leaf-list']:
                childlisttables.append(ch.arg)
                keys = [ch]
                cols, samplecols, headerenums = self.process_columns(ch, keys=keys, ancestors=ancestors,
                                                        parentkeys=self.getparentkeys(node))
                sqlcreatetable = "\n--leaf-list" + sqlcreatetable + self.underscore(ch.arg, ancestors) + cols + ";\n"
                sqlinserttable = "\n" + sqlinserttable + self.underscore(ch.arg, ancestors) + samplecols + ";\n"

            elif ch.keyword in ['choice']:
                sqlcreatetable = ""
                sqlinserttable = ""
                for case in ch.i_children:
                    self.process_tables(case, prefix, childtables, childlisttables, ancestors=ancestors)

            self.outputDoc += "\n" + sqlcreatetable
            if self.verbose:
                print(sqlcreatetable)
            if self.sampledata:
                self.outputDoc += "\n" + sqlinserttable
                if self.verbose:
                    print(sqlinserttable)
            if self.headers:
                if headerenums != "":
                    self.headersH += "\n\n//From YANG enumerations for " + self.underscore(node.arg, ancestors)
                    self.headersH += headerenums

            if not (toplevel or node.keyword in ['case']):
                ancestors.pop()

    """ A method for processing YANG leafs
    Each leaf will become a column. This method is only ever called from process_tables 
    This method simply formats a SQL snippet that will be fed back in to process_tables. It does not write to the output
    file itself
    
    The arguments:
    node: A YANG object - it can be a container, list, leaf-list, choice etc
    keys[]: A set of key attributes if the node is a list - usually there's only one
    childtables[]: The list of childtables of the node. These are passed in to facilitate creating the foreign keys
    parentkeys: A dictionary containing names and types of parent keys (used for lists)
    idx: An optional index used for creating sample data in the case of lists
    
    Returns three lists - the column DDL statements and the column sample statements and header enumerations
    """
    def process_columns(self, node, keys=[], childtables=[], ancestors=[], parentkeys=[], idx=0):
        ancestors.append(node.arg.replace("-", "_"))  # Temporarily add it in to represent the node in the ancestors
        columns = []
        samplenames = []
        samplevalues = []
        headerenums = []
        if node.keyword in ['leaf-list']:
            chs = [node]
        else:
            chs = node.i_children
        if len(keys) == 0:
            rev = "revision INTEGER NOT NULL PRIMARY KEY";
            columns.append(rev)
            samplenames.append("revision")
            samplevalues.append("0")

        for ch in chs:
            if ch.keyword in ['container', 'list', 'case'] or \
                    (ch.keyword in ['leaf-list'] and node.keyword not in ['leaf-list']):
                continue
            cfg = ch.search_one("config")
            if cfg is not None and cfg.arg == "false":
                continue
            if self.verbose:
                print("Processing Col: ", end="")
                print(ch.arg+"("+ch.keyword+")")
            if ch.keyword in ['leaf', 'leaf-list']:
                column = self.create_sql_column(ch)
                columns.append(column)

                if self.sampledata:  # Create sample values
                    samplenames.append(column.split(" ")[0])
                    default = ch.search_one("default")
                    if default is not None:
                        if default.arg.isdigit():
                            samplevalues.append(default.arg)
                        elif default.arg in ['true']:
                            samplevalues.append("1")
                        elif default.arg in ['false']:
                            samplevalues.append("0")
                        else:
                            samplevalues.append("'" + default.arg + "'")
                    else:
                        samplevalues.append(str(idx))

                if self.headers:
                    enums = self.enum_column(ch)
                    if enums is not None:
                        for e in enums:
                            defbase = ("_".join(ancestors) + "_" + ch.arg + "_" + str(e[0])).upper().replace("-", "_")
                            enumstr = defbase + " " + str(e[1])
                            enumstr2 = defbase + "_ENUM \""
                            for a in ancestors:
                                enumstr2 += "/" + a.replace("-", "_")
                            enumstr2 += "/" + ch.arg.replace("-", "_") + "#" + str(e[0]).replace("-", "_") + "\""
                            if enumstr not in headerenums:
                                headerenums.append(enumstr)
                                headerenums.append(enumstr2)

            elif ch.keyword in ['choice']:  # For leaf(s) directly under a case statement
                for case in ch.i_children:
                    for caseelem in case.i_children:
                        if caseelem.keyword in ['leaf']:
                            print("Processing choice:case:leaf", ch.arg, case.arg, caseelem.arg)
                            column = self.create_sql_column(caseelem)
                            columns.append(self.underscore(column))
                            samplenames.append(column.split(" ")[0])
                            samplevalues.append(str(idx))

            else:
                print("Unexpected type", ch.keyword, "when processing", ch.arg)
        for ct in childtables:
            definefk = self.underscore(ct.arg) + "_fk INTEGER"
            parent = ct.parent
            if parent and parent.keyword in ['case']:
                print(ct.arg, "has a case as a parent")
            if not (ct.search_one("presence") or (ct.parent is not None and ct.parent.keyword in ['case'])):
                definefk += " NOT NULL"
            columns.append(definefk)
            samplenames.append(definefk.split(" ")[0])
            samplevalues.append("0")

        pk_constraint = "CONSTRAINT " + self.underscore(node.arg) + "_pk PRIMARY KEY ("

        for pkey in parentkeys:
            fk_constraint1 = self.underscore(pkey['name']) + "_fk " + pkey['type'] + " NOT NULL"
            columns.append(fk_constraint1)
            samplenames.append(fk_constraint1.split(" ")[0])
            samplevalues.append("0")
            pk_constraint += self.underscore(pkey['name']) + "_fk, "

        for ct in childtables:  # All of the Foreign key constraints must come after the columns
            fk_constraint1 = "FOREIGN KEY(" + self.underscore(ct.arg) + "_fk) REFERENCES "
            if self.dbschema:
                fk_constraint1 += (self.underscore(ct.top.i_prefix) + ".")
            fk_constraint1 += self.underscore(ct.arg, ancestors) + "(revision)"
            columns.append(fk_constraint1)
        ancestors.pop()  # Important to pop it here as we do not want the last piece in the parent keys

        for pkey in parentkeys:
            fk_constraint2 = "FOREIGN KEY(" + self.underscore(pkey['name']) + "_fk) REFERENCES "
            if self.dbschema:
                fk_constraint2 += (self.underscore(node.top.i_prefix) + ".")
            fk_constraint2 += self.underscore(None, ancestors) + "(" + self.underscore(pkey['name']) + ")"
            columns.append(fk_constraint2)

        if len(keys) > 0:  # In the case of lists need to add a compound primary key
            for key in keys:
                pk_constraint += self.underscore(key.arg)
            pk_constraint += ")"
            columns.append(pk_constraint)

        return " (\n    " + ",\n    ".join(columns) + "\n)", \
               " (\n    " + ",\n    ".join(samplenames) + "\n) VALUES (\n    " + ",\n    ".join(samplevalues) + "\n)", \
               ("\n#define " if len(headerenums) > 0 else "") + "\n#define ".join(headerenums)

    def create_sql_column(self, node):
        types=self.get_types(node)
        columndef = self.underscore(node.arg)
        if len(types)>1:
            columndef += " TEXT"
        for ftyp in types:
            columndef += (" " + type_class[ftyp.arg])

        if node.search_one("mandatory"):
            columndef += " NOT NULL"
        return columndef

    def enum_column(self, node):
        types = self.get_types(node)
        if len(types) == 1 and types[0].arg == 'enumeration':
            return types[0].i_type_spec.enums

    @staticmethod
    def get_types(node):
        res = []

        def resolve(typ):
            if typ.arg == "union":
                for ut in typ.i_type_spec.types: resolve(ut)
            elif typ.i_typedef is not None:
                resolve(typ.i_typedef.search_one("type"))
            else:
                res.append(typ)
        typ = node.search_one("type")
        if typ.arg == "leafref":
            resolve(node.i_leafref_ptr[0].search_one("type"))
        else:
            resolve(typ)
        return res

    """ A function for formatting table and column names.
    Because SQLite cannot have '-' characters in table or columns names these must all be replaced
    Also SQLite has a set of reserved keywords that cannot be used in table of column names - these must be changed
    Ancestors must be joined by under scores. The number of ancestors to include in a table name can be controlled 
    through the ancestorcount global attribute. This uses python slicing of the list to include only the last 'n' items 
    Note: this could be a static function except for the fact that it needs to use the 'ancestorcount' parameter
    The arguments:
    name: An string value with the table or column name
    ancestors: An array of string values with the ancestry of the table or column
    """
    def underscore(self, name, ancestors=[]):
        result=""
        if len(ancestors) == 0 and name is None:
            return name
        elif len(ancestors) == 0:
            result = name.replace("-", "_")
        elif name is None:
            result = "_".join(ancestors[-(self.ancestorcount+1):])
        else:
            result = "_".join(ancestors[-self.ancestorcount:]) + "_" + name.replace("-", "_")

        if result in ['group', 'create', 'insert', 'select', 'if', 'index']:  # Also remove SQLite keywords
            result = result + "1"
        return result

    def keypath(self, name, ancestors=[]):
        result = "/".join(ancestors) + "/" + name
        return result.replace("-", "_")

    @staticmethod
    def getparentkeys(parentnode):
        parentkeys = list()
        if parentnode.keyword in ['module']:
            return parentkeys
        parentkeys.append({"name": "revision", "type": "INTEGER"})
        for k in parentnode.search("key"):
            parentkey = dict()
            parentkey["name"] = k.arg
            # kattr = parentnode.i_children[k.arg]
            # parentkey["type"] = type_class[SqlPlugin.get_types(kattr)]
            parentkey["type"] = "TEXT"
            parentkeys.append(parentkey)

        return parentkeys
