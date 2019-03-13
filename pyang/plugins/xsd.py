# Copyright (c) Sean Condon, Microsemi sean.condon@microsemi.com
#
# Pyang plugin generating an XSD schema.
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

"""XSD output plugin

This plugin takes a YANG data model and produces an XSD schema
that is able to validate XML messages
"""

import os
import sys
import optparse
import xml.etree.ElementTree as ET
import xml.dom.minidom as DOM

from pyang import plugin, statements, error
from pyang.util import unique_prefixes
from collections import defaultdict

XSNS="http://www.w3.org/2001/XMLSchema"
XSNSB="{"+XSNS+"}"
XS="xs"
xsNamespace = {XS: XSNS, "xmlns": XSNS}

"""Dictionary of XML root elements of the target XSD Schemae. Key will be the prefix of yang model"""
schemata={}

type_class = dict([
    ("int8", "byte"),
    ("uint8", "unsignedByte"),
    ("int16", "short"),
    ("uint16", "unsignedShort"),
    ("int32", "integer"),
    ("uint32","unsignedInt"),
    ("int64", "long"),
    ("uint64","unsignedLong"),
    ("decimal64","decimal"),
    ("boolean","boolean"),
    ("binary","base64Binary")])

type_class.update((t,"string") for t in
                  ("string", "enumeration", "identityref", "instance-identifier","bits",'leafref','empty','anyxml'))


union_class = dict((t,"integer") for t in
                   ("int8", "int16", "int32",
                   "uint8", "uint16", "uint32"))
"""Classification of types needed for resolving union-typed values."""

union_class.update({"boolean": "boolean"})

def pyang_plugin_init():
    plugin.register_plugin(XsdPlugin())

class XsdPlugin(plugin.PyangPlugin):
    
    def add_opts(self, optparser):
        optlist = [
            optparse.make_option("--xsd-no-doc",
                action="store_true",
                dest="xsd_no_doc",
                default=False,
                help="Do not add YANG documentation to XSD."),
            optparse.make_option("--xsd-inline-simple-type",
                action="store_true",
                dest="xsd_inline_st",
                default=False,
                help="Make simple types inline rather than separate entities."),
            ]
        g = optparser.add_option_group(
            "XSD generator output specific options")
        g.add_options(optlist)

    def add_output_format(self, fmts):
        self.multiple_modules = True
        fmts['xsd'] = self

    def setup_fmt(self, ctx):
        ctx.implicit_errors = False

    def emit(self, ctx, modules, fd):
        """Main control function.

        Set up the top-level parts of the schema, then process
        recursively all nodes in all data trees, and finally emit the
        serialized schema.
        """
        
        self.nodocs=ctx.opts.xsd_no_doc
        self.inlineSt=ctx.opts.xsd_inline_st
        self.verbose=ctx.opts.verbose
        for (epos, etag, eargs) in ctx.errors:
            if error.is_error(error.err_level(etag)):
                raise error.EmitError("XSD plugin needs a valid module")
        self.real_prefix = unique_prefixes(ctx)
        self.top_names = []
        identityList=[]
        for m in modules:
            self.top_names.extend([c.arg for c in m.i_children if
                                   c.keyword not in ("rpc", "notification")])
            identityList.extend(self.process_identities(m))
        self.identityTree=self.buildIdentityHierarchy(identityList)

        for module in self.real_prefix.keys():
            self.process_module(module)

        for module in self.real_prefix.keys():
            if len(schemata[self.real_prefix[module]]) == 0:
                continue
            tree = ET.ElementTree(schemata[self.real_prefix[module]])
            xmlStr=""
            if sys.version > "3":
                xmlStr=ET.tostring(schemata[self.real_prefix[module]], encoding="unicode", method="xml")
            else:
                xmlStr=ET.tostring(schemata[self.real_prefix[module]], encoding="UTF-8", method="xml")

            filename=module.arg
            if module.i_latest_revision is not None:
                filename+="@"+module.i_latest_revision
            filename+=".xsd"
            with open(filename, "w") as text_file:
                print("Writing file: "+filename)
                text_file.write(DOM.parseString(xmlStr).toprettyxml())


    def process_module(self, yam):
        if self.real_prefix[yam] in schemata:
            print("Module "+yam.arg+" already processed. Continuing.")
            return
        """Process data nodes, RPCs and notifications in a single module."""
        schemata[self.real_prefix[yam]] = ET.Element(XSNSB+"schema",
                {"version": "1.0",
                 "elementFormDefault": "qualified",
                 "attributeFormDefault": "unqualified"})
        ns_uri = yam.search_one("namespace").arg
        schemata[self.real_prefix[yam]].attrib["xmlns:" + self.real_prefix[yam]] = ns_uri
        schemata[self.real_prefix[yam]].attrib["targetNamespace"] = ns_uri
        if self.verbose:
            print("Handling module "+yam.arg+" as "+self.real_prefix[yam]+":"+ns_uri)
        
        for ann in yam.search(("ietf-yang-metadata", "annotation")):
            self.process_annotation(ann,yam)
        for ch in yam.i_children[:]:
            if ch.keyword == "rpc":
                self.process_rpc(ch,yam)
            elif ch.keyword == "notification":
                self.process_notifications(ch,yam)
            else:
                continue
            yam.i_children.remove(ch)
        ancestors=[]
        self.process_children(yam, None,None,None, ancestors)
        

    def process_children(self, node, name, parent=None, elemParent=None, ancestors=[]):
        """Process all children of `node`."""
        data_parent = parent if parent else node
        elem_parent = elemParent if elemParent is not None else schemata[self.real_prefix[node]]
        if parent is not None:
            ancestors.append(data_parent)
        chs = node.i_children
        for ch in chs:
            if self.verbose==True:
                print("Processing: ", end="")
                for a in ancestors:
                    print(str(a.arg), end=" ")
                print(ch.arg+"("+ch.keyword+")")
            elemName = self.qname(ch);
            minElem,maxElem=self.checkNumberElems(ch)
            if ch.keyword in ["list", "container"]:
                #Top level element cannot contain min and max
                stElem = self.add_child_elem(elem_parent, elemName, (elemParent is None),
                    minElem,"1" if ch.keyword in "container" else maxElem,ancestors=ancestors);
                self.complex_type(ch, elemName, self.elem_name(elemName)[0],ancestors)
                if ch.keyword in ["list"]:
                    self.addListKeyAttr(elemName, ch.search_one("key"),ancestors)
            elif ch.keyword in ["leaf", "leaf-list"]:
                isMandatory,isKey,default=self.checkMandatory(ch,data_parent,elemName)
                elem=self.add_child_elem(elem_parent, elemName,
                        (elemParent is None),
                        "1" if isMandatory or isKey else minElem,
                        "1" if ch.keyword in "leaf" else maxElem,
                        default,self.inlineSt,ancestors=ancestors)
                self.simple_type(elemName, ch, self.elem_name(elemName)[0],elem,ancestors)
            elif ch.keyword in ["choice"]:
                chEleme = ET.SubElement(elemParent, XSNSB+"choice")
                self.process_children(ch, elemName, None, chEleme, ancestors)
            elif ch.keyword in ["case"]:
                seqEleme = ET.SubElement(elemParent, XSNSB+"sequence")
                self.process_children(ch, elemName, None, seqEleme, ancestors)
            elif ch.keyword in ["anyxml"]:
                comment1 = ET.Comment('Note XSD 1.0 does not support <xs:any/> inside '+
                         'the same container (sequence) as other elements. A solution '+
                         'is to comment out the other elements and comment in the <xs:any/>'+
                         'leaving the whole container (e.g. sequence) as type xs:any')
                elemParent.insert(1, comment1)
                comment2 = ET.Comment('<xs:any id="'+ch.arg+'" maxOccurs="unbounded" processContents="lax"/>')
                elemParent.insert(2, comment2)
                # seqEleme = ET.SubElement(elemParent, XSNSB+"any",id=ch.arg,maxOccurs="unbounded",processContents="lax")
            else:
                if self.verbose==True: print("Unexpected node:"+str(name)+", Type:"+str(ch.keyword))
        if parent is not None and len(ancestors)>0:
            ancestors.pop()

    def addListKeyAttr(self, elemName, key,ancestors):
        pfx=self.elem_name(elemName)[0]
        name=self.elem_name(elemName)[1]
        keyName=self.type_name(":",ancestors,False)
        if len(ancestors)==0:
            return
        elif self.verbose:
            print("No ancestors found for ", elemName)

        exists = schemata[pfx].find(".//xs:element[@name='"+ancestors[-1].arg+"']", xsNamespace)
        if exists is not None:
            keyElem=ET.SubElement(exists, XSNSB+"key", name=keyName+name+"_k")
            ET.SubElement(keyElem, XSNSB+"selector", xpath="./"+elemName)
            for k in key.arg.split():
                ET.SubElement(keyElem, XSNSB+"field", xpath=pfx+":"+k)
        elif self.verbose:
            print("Element not found for", ancestors[-1].arg, "when adding key for", elemName)


    def checkNumberElems(self,ch):
        minElemObj=ch.search_one("min-elements")
        maxElemObj=ch.search_one("max-elements")
        minElem=minElemObj.arg if minElemObj is not None else "0"
        maxElem=maxElemObj.arg if maxElemObj is not None else "unbounded"
        return minElem,maxElem
        
    def checkMandatory(self, node, data_parent,elemName):
        mandatory=node.search_one("mandatory")
        parentKey=data_parent.search_one("key")
        defaultKey=node.search_one("default")
        isKey = parentKey is not None and parentKey.arg==self.elem_name(elemName)[1]
        isMandatory=mandatory is not None and mandatory.arg=="true"
        default=defaultKey.arg if defaultKey is not None else None;
        return isMandatory, isKey, default

    def complex_type(self, node, path, prefix, ancestors):
        """Create a complex type for each container"""
        exists = schemata[prefix].find("./xs:complexType[@name='"+self.type_name(path, ancestors)+"']", xsNamespace)
        if exists is None:
            self.add_import(prefix,ancestors[-1].i_module.i_prefix if len(ancestors)>0 else None)
            ctElem = ET.SubElement(schemata[prefix], XSNSB+"complexType",
                                   name=self.type_name(path, ancestors))
            seqElem = ET.SubElement(ctElem, XSNSB+"sequence")
            self.add_description(seqElem, node);
            self.process_children(node, path, 0, seqElem, ancestors)
            return ctElem
        elif self.verbose:
            print("Not repeating complexType: "+self.type_name(path, ancestors))
        
    def simple_type(self, path, ch, prefix,elem_parent=None,ancestors=[],inAnnot=False):
        """Create a simple type for each leaf, but only if it does not exist already"""
        stEleme=None
        if prefix not in schemata:
            self.load_module_now(prefix)
        stExists=None
        annGrp = schemata[prefix].find("./xs:attributeGroup[@name='yang-annotations']", xsNamespace)
        if annGrp is not None and not inAnnot:
            ctExists = schemata[prefix].find("./xs:complexType[@name='"+self.type_name(path, ancestors)+"']", xsNamespace)
            if ctExists is None:
                ctElem = ET.SubElement(schemata[prefix], XSNSB+"complexType",name=self.type_name(path, ancestors))
                scElem = ET.SubElement(ctElem, XSNSB+"simpleContent")
                extElem = ET.SubElement(scElem, XSNSB+"extension", base=prefix+":"+self.type_name(path, ancestors)+"b")
                scElem = ET.SubElement(extElem, XSNSB+"attributeGroup",ref=prefix+":yang-annotations")
            stExists = schemata[prefix].find("./xs:simpleType[@name='"+self.type_name(path, ancestors)+"b']", xsNamespace)
        else:
            stExists = schemata[prefix].find("./xs:simpleType[@name='"+self.type_name(path, ancestors)+"']", xsNamespace)
        if stExists is None:
            if (self.inlineSt and elem_parent is not None) or inAnnot:
                stEleme = ET.SubElement(elem_parent, XSNSB+"simpleType")
            else:
                stName=self.type_name(path, ancestors)
                if annGrp is not None and not inAnnot: stName+="b"
                stEleme = ET.SubElement(schemata[prefix], XSNSB+"simpleType",name=stName)
            self.add_description(stEleme, ch);
            self.type_param(stEleme,ch,path,ancestors)
        elif self.verbose:
            print("Not repeating simpleType: "+self.type_name(path, ancestors))

    def add_child_elem(self, elemParent, fullElemName, isTop, min="0", max="unbounded", default=None,inline=False,ancestors=[]):
        pfx=self.elem_name(fullElemName)[0]
        elemName=self.type_name(fullElemName, ancestors,False)
        #Check to see if this is in the same schema or not
        pfxParent=None
        if len(ancestors)>0 and ancestors[-1].i_module is not None and ancestors[-1].i_module.arg is not None:
            pfxParent, nsParent, locParent = self.find_module_by_mod_name(ancestors[-1].i_module.arg)

        #If it's in the same schema add an element
        if pfxParent is None or pfxParent==pfx:
            stEleme = ET.SubElement(elemParent, XSNSB+"element", name=self.elem_name(fullElemName)[1])
            if not inline:
                stEleme.attrib["type"]=pfx+":"+elemName+"_t"
            if not isTop and min is not None:
                stEleme.attrib["minOccurs"]=min
            if not isTop and max is not None:
                stEleme.attrib["maxOccurs"]=max
            if default is not None:
                stEleme.attrib["default"]=default
            return stEleme
        else:
            #If it's not in the same schema then add a ref
            stEleme = ET.SubElement(elemParent, XSNSB+"element", ref=fullElemName)
            if min is not None and min != "1":
                stEleme.attrib["minOccurs"]=min
            if max is not None and max != "1":
                stEleme.attrib["maxOccurs"]=max
            if pfx not in schemata:
                self.load_module_now(pfx)
            exists = schemata[pfx].find("./xs:element[@name='"+self.elem_name(fullElemName)[1]+"']", xsNamespace)
            if exists is None:
                self.add_import(pfx, pfxParent)
                return ET.SubElement(schemata[pfx], XSNSB+"element", name=self.elem_name(fullElemName)[1], type=pfx+":"+elemName+"_t")

    def add_annotation_attr(self, prefix, elemParent):
        exists = schemata[prefix].find(".//xs:simpleType", xsNamespace)

    def add_description(self, elemParent, node):
        description = node.search_one("description");
        annElem=None
        appInfo=None
        if not self.nodocs and description is not None:
            annElem = ET.SubElement(elemParent, XSNSB+"annotation")
            docElem=ET.SubElement(annElem, XSNSB+"documentation",
                                  attrib={"xml:lang":"en"})
            docElem.text=description.arg
        units = node.search_one("units")
        if units is not None and units.arg is not None:
            if annElem is None:
                annElem=ET.SubElement(elemParent, XSNSB+"annotation")
            appInfo=ET.SubElement(annElem, XSNSB+"appinfo")
            ET.SubElement(appInfo, "units", value=units.arg)
        config=node.search_one("config")
        if config is not None:
            if annElem is None:
                annElem=ET.SubElement(elemParent, XSNSB+"annotation")
            if appInfo is None:
                appInfo=ET.SubElement(annElem, XSNSB+"appinfo")
            ET.SubElement(appInfo, "config", value=config.arg)
        presence=node.search_one("presence")
        if presence is not None:
            if annElem is None:
                annElem=ET.SubElement(elemParent, XSNSB+"annotation")
            if appInfo is None:
                appInfo=ET.SubElement(annElem, XSNSB+"appinfo")
            presElem=ET.SubElement(appInfo, "presence")
            if not self.nodocs:
                presElem.attrib["value"]=presence.arg
        must=node.search_one("must")
        if must is not None:
            if annElem is None:
                annElem=ET.SubElement(elemParent, XSNSB+"annotation")
            if appInfo is None:
                appInfo=ET.SubElement(annElem, XSNSB+"appinfo")
            mustElem=ET.SubElement(appInfo, "must",value=must.arg)
            errMsg=must.search_one("error-message")
            if errMsg is not None:
                ET.SubElement(mustElem, "error-message",value=errMsg.arg)
            desc=must.search_one("description")
            if desc is not None:
                ET.SubElement(mustElem, "description",value=desc.arg)

        when=node.search_one("when")
        if when is not None:
            if annElem is None:
                annElem=ET.SubElement(elemParent, XSNSB+"annotation")
            if appInfo is None:
                appInfo=ET.SubElement(annElem, XSNSB+"appinfo")
            whenElem=ET.SubElement(appInfo, "when",value=when.arg)

    def type_name(self,name,ancestors=[],isType=True):
        nameParts=self.elem_name(name)
        type_name=""
        isFirst=True
        for a in ancestors:
            if isFirst:
                isFirst=False
            else:
                type_name=type_name+"_"
            type_name+=a.arg
        fullName=type_name+"_"+nameParts[1] if type_name!="" else nameParts[1]
        if isType: fullName=fullName+"_t"
        return fullName
    
    def elem_name(self,name):
        return name.split(":")

    def type_param(self, stEleme, node,path=None,ancestors=[]):
        """Resolve the type of a leaf or leaf-list node for XSD
        """
        types=self.get_types(node,stEleme)
        unionElem=None
        if len(types)>1:
            unionElem = ET.SubElement(stEleme, XSNSB+"union")
        for ftyp in self.get_types(node,stEleme):
            if unionElem is not None:
                stEleme=ET.SubElement(unionElem, XSNSB+"simpleType")
                
            type=node.search_one("type")
            if type is not None and type.arg == "leafref":
                leafref_path=type.search_one("path")
                if leafref_path is not None:
                    self.add_keyref(node, leafref_path.arg,path,ancestors)

            typeElem=None
            range = ftyp.search_one("range")
            if range is not None and range.arg is not None:
                typeElem=self.handle_mult_range(stEleme,ftyp.arg,range.arg)

            length = ftyp.search_one("length")
            if length is not None and length.arg is not None:
                typeElem=self.handle_mult_range(stEleme,ftyp.arg,length.arg,True)

            if typeElem is None:
                typeElem = ET.SubElement(stEleme, XSNSB+"restriction", base="xs:"+type_class[ftyp.arg])

            fracdigits = ftyp.search_one("fraction-digits")
            if fracdigits is not None and fracdigits.arg is not None:
                ET.SubElement(typeElem, XSNSB+"fractionDigits", value=fracdigits.arg)
                
            for enum in ftyp.search("enum"):
                ET.SubElement(typeElem, XSNSB+"enumeration", value=enum.arg)

            if ftyp.arg in "identityref":
                for idbase in ftyp.i_type_spec.idbases:
                    self.find_instances_of_idbase(self.identityTree,
                          self.getFqname(idbase.arg,idbase.i_module.i_prefix),typeElem,
                                                  ftyp.i_module.i_prefix)

            pattern = ftyp.search_one("pattern")
            if pattern is not None and pattern.arg is not None:
                ET.SubElement(typeElem, XSNSB+"pattern", value=pattern.arg)

    def find_instances_of_idbase(self,idenTree, base,typeElem,pfx,isMatch=False):
        for k, v in idenTree.items():
            if isMatch or k==base:
                if pfx is not None and str(k).startswith(pfx+":"):
                    ET.SubElement(typeElem, XSNSB+"enumeration", value=str(k)[len(pfx)+1:])
                else:
                    ET.SubElement(typeElem, XSNSB+"enumeration", value=k)
            if isinstance(v, dict):
                self.find_instances_of_idbase(v,base,typeElem,pfx,isMatch or k==base)

    def handle_mult_range(self, typeElem, yangtype, rangeStr,isLength=False):
        if "|" in rangeStr:
            un=ET.SubElement(typeElem, XSNSB+"union")
            for r in rangeStr.split("|"):
                st=ET.SubElement(un, XSNSB+"simpleType")
                restElem = ET.SubElement(st, XSNSB+"restriction", base="xs:"+type_class[yangtype])
                self.handle_range(restElem,r,isLength)
            return un
        else:
            restElem = ET.SubElement(typeElem, XSNSB+"restriction", base="xs:"+type_class[yangtype] )
            self.handle_range(restElem,rangeStr,isLength)
            return restElem

    def handle_range(self, typeElem, rangeStr,isLength=False):
        incl="Inclusive" if not isLength else "Length"
        if ".." not in rangeStr:
            if "min" not in rangeStr:
                ET.SubElement(typeElem, XSNSB+"min"+incl, value=rangeStr)
            if "max" not in rangeStr:
                ET.SubElement(typeElem, XSNSB+"max"+incl, value=rangeStr)
        elif rangeStr is not None:
            if "min" not in rangeStr.split("..")[0]:
                ET.SubElement(typeElem, XSNSB+"min"+incl, value=rangeStr.split("..")[0])
            if "max" not in rangeStr.split("..")[1]:
                ET.SubElement(typeElem, XSNSB+"max"+incl, value=rangeStr.split("..")[1])

    def handle_identity_ref(self, typeElem, base):
        irElem = ET.SubElement(typeElem, XSNSB+"restriction", base="xs:string")
        ET.SubElement(irElem, XSNSB+"enum")
        print("handling identity ref:" + base.arg)
        return irElem

    def get_types(self, node, stEleme):
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

    def add_keyref(self,node,keypath,path=None,ancestors=[]):
        refPfx=pfx=self.elem_name(path)[0]
        keyref=""
        keyElemType=""
        krXpath="."
        keyref_anc=""
        keyref_name=node.arg+"_kr"
        isFirst=True
        splitparts=keypath.split("/")
        level=0
        if keypath.startswith("/"):
            #It's a global key reference, could be in another file. Will have a namespace 
            for part in splitparts[::-1]: #Reverse - work backwards through xpath
                level-=1
                if part is None or part=="": continue
                if ":" in part:
                    refPfx=part.split(":")[0]
                    refName=part.split(":")[1]
                else:
                    refName=part
                if refName in [node.i_leafref_ptr[0].arg]: continue
                keyref=refName+"_"+keyref
                if level >= -2: continue
                keyElemType+=refName+"_"
            for a in ancestors:
                keyref_anc+="/"+pfx+":"+a.arg
                keyref_name=a.arg+"_"+keyref_name
            krXpath="."+keyref_anc
        elif keypath.startswith("../"):
            for part in splitparts[::-1]: #Reverse - work backwards through xpath
                if ":" in part:
                    refPfx=part.split(":")[0]
                    refName=part.split(":")[1]
                else:
                    refName=part
                if refName in [node.i_leafref_ptr[0].arg]:
                    continue
                elif refName in [".."]:
                    level+=1
                    continue
                keyref+=refName
            for i in range(0,len(ancestors)):
                if i < len(ancestors)-level+1:
                    keyElemType=keyElemType+ancestors[i].arg+"_"
                    keyref_anc=keyref_anc+ancestors[i].arg+"_"
                else:
                    krXpath+="/"+pfx+":"+ancestors[i].arg
                    keyref_name=ancestors[i].arg+"_"+keyref_name
            keyref=keyref_anc+keyref+"_"
        #Find the element to attach the key ref to by its complex type name
        if refPfx not in schemata:
            self.load_module_now(refPfx)
        exists = schemata[refPfx].find(".//xs:element[@type='"+refPfx+":"+keyElemType+"t']", xsNamespace)
        if exists is not None:
            self.add_import(pfx, refPfx)
            print("Trying to add import in "+refPfx+" to "+node.arg)
            # self.add_import(pfx, node)
            keyRefElem=ET.SubElement(exists, XSNSB+"keyref", name=keyref_name, refer=refPfx+":"+keyref+"k")
            ET.SubElement(keyRefElem, XSNSB+"selector", xpath=krXpath)
            for k in node.arg.split():
                ET.SubElement(keyRefElem, XSNSB+"field", xpath=pfx+":"+k)
            return keyRefElem
        else:
            print("Error could not find key with type:"+keyElemType)

    def load_module_now(self, refPfx, pfx="?"):
        if self.verbose:
            print("Loading module "+refPfx+" as it is needed by module "+pfx)
        pfx, ns, loc, rev = self.find_module_by_prefix(refPfx)
        for k in self.real_prefix.keys():
            if k.arg in [loc]:
                self.process_module(k)

        
    def qname(self, node):
        """Return the qualified name of `node`.

        In JSON, namespace identifiers are YANG module names.
        """
        return self.real_prefix[node.main_module()] + ":" + node.arg
    
    def add_import(self, prefix, refPfx):
        """Create an import for another XSD"""
        ns=""
        loc=""
        pfx, ns, loc, rev = self.find_module_by_prefix(prefix)
        pfxParent, nsParent, locParent, revParent = self.find_module_by_prefix(refPfx)

        if pfxParent is None or loc==locParent or pfxParent not in schemata: return

        #         print "Adding import for "+loc+" if it does not exist"
        exists = schemata[pfxParent].find("./xs:import[@namespace='"+ns+"']", xsNamespace)
        if exists is None:
            print("Adding import to: "+pfxParent)
            fileloc=loc
            if rev is not None:
                fileloc+=("@"+rev)
            fileloc+=".xsd"
            impElem = ET.Element(XSNSB+"import",
                                   namespace=ns, schemaLocation=fileloc)
            schemata[pfxParent].insert(0, impElem)
#             ns_uri = yam.search_one("namespace").arg
            schemata[pfxParent].attrib["xmlns:"+prefix]=ns
        elif(self.verbose):
            print("Import already exists for "+ns)
            
    def find_module_by_mod_name(self, modName):
        for key in self.real_prefix:
            pfx=key.search_one("prefix").arg
            ns=key.search_one("namespace").arg
            loc=key.arg
            if loc == modName:
                return pfx, ns, loc
            
        return None,None,None
            
    def find_module_by_prefix(self, prefix):
        for key in self.real_prefix:
            pfx=key.search_one("prefix").arg
            ns=key.search_one("namespace").arg
            rev=key.i_latest_revision
            loc=key.arg
            if pfx == prefix:
                break;
        return pfx, ns, loc, rev

    def process_annotation(self, ann, module):
        if self.verbose:
            print("Adding annotation: "+ann.arg+" for module:"+self.real_prefix[module])
        attrGroup = schemata[self.real_prefix[module]].find("./xs:attributeGroup[@name='yang-annotations']", xsNamespace)
        if attrGroup is None:
            attrGroup=ET.SubElement(schemata[self.real_prefix[module]], XSNSB+"attributeGroup", name="yang-annotations")
        typeName=ann.arg+"_ann"
        annElem=ET.SubElement(attrGroup, XSNSB+"attribute", name=ann.arg)
        self.simple_type(self.real_prefix[module]+":"+typeName,ann,self.real_prefix[module],annElem,inAnnot=True)

    def process_rpc(self, ch,yam):
        if self.verbose:
            print("Processing RPC: "+str(ch.arg))
        input = ch.search("input");
        self.process_rpc_in_out(input,yam,ch,True)
        output = ch.search("output");
        self.process_rpc_in_out(output,yam,ch,False)

    def process_notifications(self,ch,yam):
        if self.verbose:
            print("Processing Notification: "+str(ch.arg))
        ancestors=[ch]
        inOutElem=ET.SubElement(schemata[self.real_prefix[yam]],XSNSB+"complexType",name=ch.arg)
        seqElem=ET.SubElement(inOutElem,XSNSB+"sequence")
        self.process_children(ch,None,yam,seqElem, ancestors)

    def process_rpc_in_out(self, inOutList,yam,rpc,processInput=True):
        for inOut in inOutList:
            ancestors=[rpc]
            inOutName="input" if processInput else "output"
            inOutName=rpc.arg+"_"+inOutName+"_t"
            inOutElem=ET.SubElement(schemata[self.real_prefix[yam]],XSNSB+"complexType",name=inOutName)
            seqElem=ET.SubElement(inOutElem,XSNSB+"sequence")
            self.process_children(inOut,None,yam,seqElem, ancestors)

    def process_identities(self, module):
        identityList=[]
        for iden,idenStmt in module.i_identities.items():
            identityList.append(idenStmt)
        return identityList

    def buildIdentityHierarchy(self, identityList):
        identityHierarchy=RecursiveDict()
        iterations=0
        iterationsLimit=20
        while len(identityList) > 0 and iterations < iterationsLimit:
            iterations+=1;
            for iden in identityList:
                fqid=self.getFqname(iden.arg,iden.i_module.i_prefix)
                # if self.verbose: print("Identity "+fqid+" Iter="+str(iterations))
                bases=iden.search("base");
                if len(bases)==0:
                    identityHierarchy[fqid]=None
                    identityList.remove(iden)
                    break;
                else:
                    for baseStmt in bases:
                        fqbase=self.getFqname(baseStmt.arg,baseStmt.i_module.i_prefix)
                        added = self.find_instances_of_identity(identityHierarchy, fqbase, fqid)
                        if added:
                            identityList.remove(iden)
                            break;
        if iterations > iterationsLimit:
            print("Warning: Number of iterations in buildIdentityHierarchy() is "
                  "close to the limit: "+str(iterationsLimit))
        if self.verbose: print(identityHierarchy)
        return identityHierarchy

    def find_instances_of_identity(self,idenTree, base, iden):
        for k, v in idenTree.items():
            if k==base:
                if isinstance(idenTree[base],dict):
                    idenTree[base][iden]=None
                else:
                    idenTree[base]={iden:None}
                return True;
            elif isinstance(v, dict):
                added = self.find_instances_of_identity(v,base,iden)
                if added: return True

    def getFqname(self,idenName, prefix=None):
        if idenName.find(":") == -1:
            return str(prefix+":"+idenName)
        else:
            return idenName;


class RecursiveDict(defaultdict):
    def __init__(self):
        super(RecursiveDict, self).__init__(RecursiveDict)

    def __repr__(self):
        return repr(dict(self))