"""
   Copyright 2006-2008 SpringSource (http://springsource.com), All Rights Reserved

   Licensed under the Apache License, Version 2.0 (the "License");
   you may not use this file except in compliance with the License.
   You may obtain a copy of the License at

       http://www.apache.org/licenses/LICENSE-2.0

   Unless required by applicable law or agreed to in writing, software
   distributed under the License is distributed on an "AS IS" BASIS,
   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
   See the License for the specific language governing permissions and
   limitations under the License.
"""
try:
    import cElementTree as etree
except ImportError:
    try:
        import xml.etree.ElementTree as etree
    except ImportError:
        from elementtree import ElementTree as etree

import re
import types
import inspect
import logging

from springpython.context import scope
from decorator import decorator, partial
from springpython.context import ApplicationContextAware
from springpython.factory import PythonObjectFactory
from springpython.factory import ReflectiveObjectFactory
from springpython.container import InvalidObjectScope

yaml_mappings = {
    "str":"types.StringType", "unicode":"types.UnicodeType",
    "int":"types.IntType", "long":"types.LongType",
    "float":"types.FloatType", "decimal":"decimal.Decimal",
    "bool":"types.BooleanType", "complex":"types.ComplexType",
    "list":"types.ListType", "tuple":"types.TupleType",
    "dict":"types.DictType",
}

xml_mappings = {
    "str":"types.StringType", "unicode":"types.UnicodeType",
    "int":"types.IntType", "long":"types.LongType",
    "float":"types.FloatType", "decimal":"decimal.Decimal",
    "bool":"types.BooleanType", "complex":"types.ComplexType",
}

class ObjectDef(object):
    """
    ObjectDef is a format-neutral way of storing object definition information. It includes
    a handle for the actual ObjectFactory that should be used to utilize this information when
    creating an instance of a object.
    """
    def __init__(self, id, props=None, factory=None, scope=scope.SINGLETON,
                 lazy_init=False, abstract=False, parent=None):
        super(ObjectDef, self).__init__()
        self.id = id
        self.factory = factory
        if props is None:
            self.props = []
        else:
            self.props = props
        self.scope = scope
        self.lazy_init = lazy_init
        self.abstract = abstract
        self.parent = parent
        self.pos_constr = []
        self.named_constr = {}

    def __str__(self):
        return "id=%s props=%s scope=%s factory=%s" % (self.id, self.props, self.scope, self.factory)

class ReferenceDef(object):
    """
    This class represents a definition that is referencing another object.
    """
    def __init__(self, name, ref):
        self.name = name
        self.ref = ref

    def prefetch(self, container):
        self.get_value(container)

    def get_value(self, container):
        return container.get_object(self.ref)

    def set_value(self, obj, container):
        setattr(obj, self.name, container.objects[self.ref])

    def __str__(self):
        return "name=%s ref=%s" % (self.name, self.ref)

class InnerObjectDef(object):
    """
    This class represents an inner object. It is optional whether or not the object
    has its own name.
    """
    def __init__(self, name, inner_comp):
        self.name = name
        self.inner_comp = inner_comp

    def prefetch(self, container):
        self.get_value(container)

    def get_value(self, container):
        return container.get_object(self.inner_comp.id)

    def set_value(self, obj, container):
        setattr(obj, self.name, self.get_value(container))

    def __str__(self):
        return "name=%s inner_comp=%s" % (self.name, self.inner_comp)

class ValueDef(object):
    """
    This class represents a property that holds a value. The value can be simple value, or
    it can be a complex container which internally holds references, inner objects, or
    any other type.
    """
    def __init__(self, name, value):
        self.name = name
        if value == "True":
            self.value = True
        elif value == "False":
            self.value= False
        else:
            self.value = value
        self.logger = logging.getLogger("springpython.config.ValueDef")

    def scan_value(self, container, value):
        if hasattr(value, "get_value"):
            return value.get_value(container)
        elif isinstance(value, tuple):
            new_list = [self.scan_value(container, item) for item in value]
            results = tuple(new_list)
            return results
        elif isinstance(value, list):
            new_list = [self.scan_value(container, item) for item in value]
            return new_list
        elif isinstance(value, set):
            results = set([self.scan_value(container, item) for item in value])
            return results
        elif isinstance(value, frozenset):
            results = frozenset([self.scan_value(container, item) for item in value])
            return results
        else:
            if value == "True":
                return True
            elif value == "False":
                return False
            else:
                return value

    def get_value(self, container):
        val = self._replace_refs_with_actuals(self.value, container)
        if val is None:
            return self.value
        else:
            return val

    def set_value(self, obj, container):
        setattr(obj, self.name, self.value)
        val = self._replace_refs_with_actuals(obj, container)

    def _replace_refs_with_actuals(self, obj, container):
        """Normal values do nothing for this step. However, sub-classes are defined for
        the various containers, like lists, set, dictionaries, etc., to handle iterating
        through and pre-fetching items."""
        pass

    def __str__(self):
        return "name=%s value=%s" % (self.name, self.value)

class DictDef(ValueDef):
    """Handles behavior for a dictionary-based value."""
    def __init__(self, name, value):
        super(DictDef, self).__init__(name, value)

    def _replace_refs_with_actuals(self, obj, container):
        for key in self.value.keys():
            if hasattr(self.value[key], "ref"):
                self.value[key] = container.get_object(self.value[key].ref)
            else:
                self.value[key] = self.scan_value(container, self.value[key])

class ListDef(ValueDef):
    """Handles behavior for a list-based value."""
    def __init__(self, name, value):
        super(ListDef, self).__init__(name, value)
        self.logger = logging.getLogger("springpython.config.ListDef")

    def _replace_refs_with_actuals(self, obj, container):
        for i in range(0, len(self.value)):
            self.logger.debug("Checking out %s, wondering if I need to do any replacement..." % str(self.value[i]))
            if hasattr(self.value[i], "ref"):
                self.value[i] = container.get_object(self.value[i].ref)
            else:
                self.value[i] = self.scan_value(container, self.value[i])

class TupleDef(ValueDef):
    """Handles behavior for a tuple-based value."""

    def __init__(self, name, value):
        super(TupleDef, self).__init__(name, value)

    def _replace_refs_with_actuals(self, obj, container):
        new_value = list(self.value)
        for i in range(0, len(new_value)):
            if hasattr(new_value[i], "ref"):
                new_value[i] = container.get_object(new_value[i].ref)
            else:
                new_value[i] = self.scan_value(container, new_value[i])
        try:
            setattr(obj, self.name, tuple(new_value))
        except AttributeError:
            pass
        return tuple(new_value)

class SetDef(ValueDef):
    """Handles behavior for a set-based value."""
    def __init__(self, name, value):
        super(SetDef, self).__init__(name, value)
        self.logger = logging.getLogger("springpython.config.SetDef")

    def _replace_refs_with_actuals(self, obj, container):
        self.logger.debug("Replacing refs with actuals...")
        self.logger.debug("set before changes = %s" % self.value)
        new_set = set()
        for item in self.value:
            if hasattr(item, "ref"):
                self.logger.debug("Item !!!%s!!! is a ref, trying to replace with actual object !!!%s!!!" % (item, item.ref))
                #self.value.remove(item)
                #self.value.add(container.get_object(item.ref))
                newly_fetched_value = container.get_object(item.ref)
                new_set.add(newly_fetched_value)
                self.logger.debug("Item !!!%s!!! was removed, and newly fetched value !!!%s!!! was added." % (item, newly_fetched_value))
                #new_set.add(container.get_object(item.ref))
            else:
                self.logger.debug("Item !!!%s!!! is NOT a ref, trying to replace with scanned value" % str(item))
                #self.value.remove(item)
                #self.value.add(self.scan_value(container, item))
                newly_scanned_value = self.scan_value(container, item)
                new_set.add(newly_scanned_value)
                self.logger.debug("Item !!!%s!!! was removed, and newly scanned value !!!%s!!! was added." % (item, newly_scanned_value))
                #new_set.add(self.scan_value(container, item))
        #self.value = new_set
        self.logger.debug("set after changes = %s" % new_set)
        #return self.value
        try:
            setattr(obj, self.name, new_set)
        except AttributeError:
            pass
        return new_set

class FrozenSetDef(ValueDef):
    """Handles behavior for a frozen-set-based value."""
    def __init__(self, name, value):
        super(FrozenSetDef, self).__init__(name, value)
        self.logger = logging.getLogger("springpython.config.FrozenSetDef")

    def _replace_refs_with_actuals(self, obj, container):
        self.logger.debug("Replacing refs with actuals...")
        self.logger.debug("set before changes = %s" % self.value)
        new_set = set()
        for item in self.value:
            if hasattr(item, "ref"):
                self.logger.debug("Item <<<%s>>> is a ref, trying to replace with actual object <<<%s>>>" % (item, item.ref))
                #new_set.remove(item)
                #debug begin
                newly_fetched_value = container.get_object(item.ref)
                new_set.add(newly_fetched_value)
                self.logger.debug("Item <<<%s>>> was removed, and newly fetched value <<<%s>>> was added." % (item, newly_fetched_value))
                #debug end
                #new_set.add(container.get_object(item.ref))
            else:
                self.logger.debug("Item <<<%s>>> is NOT a ref, trying to replace with scanned value" % str(item))
                #new_set.remove(item)
                #debug begin
                newly_scanned_value = self.scan_value(container, item)
                new_set.add(newly_scanned_value)
                self.logger.debug("Item <<<%s>>> was removed, and newly scanned value <<<%s>>> was added." % (item, newly_scanned_value))
                #debug end
                #new_set.add(self.scan_value(container, item))
        #self.logger.debug("Newly built set = %s" % new_set)
        #self.value = frozenset(new_set)
        new_frozen_set = frozenset(new_set)
        self.logger.debug("set after changes = %s" % new_frozen_set)
        #return self.value
        try:
            setattr(obj, self.name, new_frozen_set)
        except AttributeError:
            pass
        except TypeError:
            pass
        return new_frozen_set

class Config(object):
    """
    Config is an interface that defines how to read object definitions from an input source.
    """
    def read_object_defs(self):
        """Abstract method definition - should return an array of Object objects"""
        raise NotImplementedError()

class PyContainerConfig(Config):
    """
    PyContainerConfig supports the legacy XML dialect (PyContainer) of reading object definitions.
    """

    NS = "{http://www.springframework.org/springpython/schema/pycontainer-components}"

    def __init__(self, config_location):
        if isinstance(config_location, list):
            self.config_location = config_location
        else:
            self.config_location = [config_location]
        self.logger = logging.getLogger("springpython.config.PyContainerConfig")

    def read_object_defs(self):
        self.logger.debug("==============================================================")
        objects = []
        for config in self.config_location:
            self.logger.debug("* Parsing %s" % config)
            components = etree.parse(config).getroot()
            objects.extend([self._convert_component(component) for component in components])
        self.logger.debug("==============================================================")
        return objects


    def _convert_component(self, component):
        "This function generates a object definition, then converts scope and property elements."
        self.logger.debug("component: Processing %s" % component)
        c = ObjectDef(component.get("id"), factory=ReflectiveObjectFactory(component.get("class")))
        if "scope" in component.attrib:
            c.scope = scope.convert(component.get("scope"))
        c.props = [self._convert_prop_def(p) for p in component.findall(self.NS+"property")]
        return c

    def _convert_prop_def(self, p):
        "This function translates object properties into useful dictionaries of information for the container."
        if "local" in p.attrib or p.find(self.NS+"local") is not None:
            if "local" in p.attrib:
                return ReferenceDef(p.get("name"), p.get("local"))
            else:
                return ReferenceDef(p.get("name"), p.find(self.NS+"local"))
        elif "list" in p.attrib or p.find(self.NS+"list") is not None:
            if "list" in p.attrib:
                return ListDef(p.name, [ReferenceDef(p.name + ".list", prop_list.local) for prop_list in p.list])
            else:
                return ListDef(p.name, [ReferenceDef(p.name + ".list", prop_list.local) for prop_list in p.list])
        else:
            self.logger.debug("py: name = %s code = %s" % (p.get("name"), p.text))
            thing = eval(str(p.text).strip())
            self.logger.debug("py: You have parsed %s" % thing)
            return ValueDef(p.get("name"), eval(str(p.text).strip()))

class SpringJavaConfig(Config):
    """
    SpringJavaConfig supports current Spring Java format of XML bean definitions.
    """
    NS = "{http://www.springframework.org/schema/beans}"

    def __init__(self, config_location):
        if isinstance(config_location, list):
            self.config_location = config_location
        else:
            self.config_location = [config_location]
        self.logger = logging.getLogger("springpython.config.SpringJavaConfig")

        # By making this an instance-based property (instead of function local), inner object
        # definitions can add themselves to the list in the midst of parsing an input.
        self.objects = []

    def read_object_defs(self):
        self.logger.debug("==============================================================")
        # Reset, in case the file is re-read
        self.objects = []
        for config in self.config_location:
            self.logger.debug("* Parsing %s" % config)
            beans = etree.parse(config).getroot()
            self.objects.extend([self._convert_bean(bean) for bean in beans])
        self.logger.debug("==============================================================")
        return self.objects

    def _convert_bean(self, bean, prefix=""):
        "This function generates a object definition, then converts scope and property elements."
        if prefix != "":
            if "id" in bean.attrib:
                bean.set("id", prefix + bean.get("id"))
            else:
                bean.set("id", prefix + "<anonymous>")

        c = ObjectDef(bean.get("id"), factory=ReflectiveObjectFactory(bean.get("class")))

        if "scope" in bean.attrib:
            c.scope = scope.convert(bean.get("scope"))
        self.logger.debug("bean: %s" % bean)
        c.pos_constr = [self._convert_prop_def(bean, constr, bean.get("id") + ".constr") for constr in bean.findall(self.NS+"constructor-arg")]
        self.logger.debug("Constructors = %s" % c.pos_constr)
        c.props = [self._convert_prop_def(bean, p, p.get("name")) for p in bean.findall(self.NS+"property")]

        return c

    def _convert_prop_def(self, bean, p, name):
        "This function translates object constructors/properties into useful collections of information for the container."

        if "ref" in p.keys() or p.find(self.NS+"ref") is not None:
            if "ref" in p.keys():
                return ReferenceDef(name, p.get("ref"))
            else:
                return ReferenceDef(name, p.find(self.NS+"ref").get("bean"))
        elif "value" in p.keys() or p.find(self.NS+"value") is not None:
            if "value" in p.keys():
                return ValueDef(name, p.get("value"))
            else:
                return ValueDef(name, p.find(self.NS+"value").text)
        elif p.find(self.NS+"map") is not None:
            dict = {}
            for entry in p.find(self.NS+"map"):
                key = entry.find(self.NS+"key").find(self.NS+"value").text
                if entry.find(self.NS+"value") is not None:
                    dict[key] = str(entry.find(self.NS+"value").text)
                elif entry.find(self.NS+"ref") is not None:
                    dict[key] = ReferenceDef(key, entry.find(self.NS+"ref").get("bean"))
                else:
                    self.logger.debug("Don't know how to handle %s" % entry)
            return DictDef(name, dict)
        elif p.find(self.NS+"props") is not None:
            dict = {}
            for prop in p.find(self.NS+"props"):
                dict[prop.get("key")] = str(prop.text)
            return DictDef(name, dict)
        elif p.find(self.NS+"list") is not None:
            list = []
            for element in p.find(self.NS+"list"):
                if element.tag == self.NS+"value":
                    list.append(element.text)
                elif element.tag == self.NS+"ref":
                    list.append(ReferenceDef(name + ".list", element.get("bean")))
                else:
                    self.logger.debug("Don't know how to handle %s" % element.tag)
            return ListDef(name, list)
        elif p.find(self.NS+"set") is not None:
            s = set()
            for element in p.find(self.NS+"set"):
                if element.tag == self.NS+"value":
                    s.add(element.text)
                elif element.tag == self.NS+"ref":
                    s.add(ReferenceDef(name + ".set", element.get("bean")))
                else:
                    self.logger.debug("Don't know how to handle %s" % element.tag)
            return SetDef(name, s)
        elif p.find(self.NS+"bean"):
            inner_object_def = self._convert_bean(p.find(self.NS+"bean"), prefix=bean.get("id") + "." + name + ".")
            self.objects.append(inner_object_def)
            return InnerObjectDef(name, inner_object_def)

class XMLConfig(Config):
    """
    XMLConfig supports current Spring Python format of XML object definitions.
    """

    NS = "{http://www.springframework.org/springpython/schema/objects}"
    NS_11 = "{http://www.springframework.org/springpython/schema/objects/1.1}"

    def __init__(self, config_location):
        if isinstance(config_location, list):
            self.config_location = config_location
        else:
            self.config_location = [config_location]
        self.logger = logging.getLogger("springpython.config.XMLConfig")

        # By making this an instance-based property (instead of function local), inner object
        # definitions can add themselves to the list in the midst of parsing an input.
        self.objects = []

    def read_object_defs(self):
        self.logger.debug("==============================================================")
        # Reset, in case the file is re-read
        self.objects = []
        for config in self.config_location:
            self.logger.debug("* Parsing %s" % config)

            # A flat list of objects, as found in the XML document.
            objects = etree.parse(config).getroot()

            # We need to handle both 1.0 and 1.1 XSD schemata *and* we may be
            # passed a list of config locations of different XSD versions so we
            # must find out here which one is used in the current config file
            # and pass the correct namespace down to other parts of XMLConfig.
            ns = objects.tag[:objects.tag.find("}") + 1]

            # A dictionary of abstract objects, keyed by their IDs, used in
            # traversing the hierarchies of parents; built upfront here for
            # convenience.
            abstract_objects = {}
            for obj in objects:
                if obj.get("abstract"):
                    abstract_objects[obj.get("id")] = obj

            for obj in objects:
                if obj.get("class") is None and not obj.get("parent"):
                    self._map_custom_class(obj, xml_mappings, ns)

                elif obj.get("parent"):
                    # Children are added to self.objects during the children->abstract parents traversal.
                    pos_constr = self._get_pos_constr(obj, ns)
                    named_constr = self._get_named_constr(obj, ns)
                    props = self._get_props(obj, ns)
                    self._traverse_parents(obj, obj, ns, pos_constr, named_constr, props, abstract_objects)
                    continue

                self.objects.append(self._convert_object(obj, ns=ns))

        self.logger.debug("==============================================================")
        for object in self.objects:
            self.logger.debug("Parsed %s" % object)
        return self.objects

    def _map_custom_class(self, obj, mappings, ns):
        """ Fill in the missing attributes of Python objects and make it look
        to the rest of XMLConfig as if they already were in the XML config file.
        """
        for class_name in mappings:
            tag_no_ns = obj.tag.replace(ns, "")
            if class_name == tag_no_ns:

                obj.set("class", mappings[class_name])
                constructor_arg = etree.Element("%s%s" % (ns, "constructor-arg"))
                value = etree.Element("%s%s" % (ns, "value"))
                value.text = obj.text
                obj.append(constructor_arg)
                constructor_arg.append(value)
                obj.text = ""

                break

        else:
            self.logger.warning("No matching type found for object %s" % obj)

    def _traverse_parents(self, leaf, child, ns, pos_constr,
                            named_constr, props, abstract_objects):

        parent = abstract_objects[child.get("parent")]

        # At this point we only build up the lists of parameters but we don't create
        # the object yet because the current parent object may still have its
        # own parent.

        # Positional constructors

        parent_pos_constrs = self._get_pos_constr(parent, ns)

        # Make sure there are as many child positional parameters as there
        # are in the parent's list.

        len_pos_constr = len(pos_constr)
        len_parent_pos_constrs = len(parent_pos_constrs)

        if len_pos_constr < len_parent_pos_constrs:
            pos_constr.extend([None] * (len_parent_pos_constrs - len_pos_constr))

        for idx, parent_pos_constr in enumerate(parent_pos_constrs):
            if not pos_constr[idx]:
                pos_constr[idx] = parent_pos_constr

        # Named constructors
        child_named_constrs = named_constr
        parent_named_constrs = self._get_named_constr(parent, ns)

        for parent_named_constr in parent_named_constrs:
            if parent_named_constr not in child_named_constrs:
                named_constr[parent_named_constr] = parent_named_constrs[parent_named_constr]

        # Properties
        child_props = [prop.name for prop in props]
        parent_props = self._get_props(parent, ns)

        for parent_prop in parent_props:
            if parent_prop.name not in child_props:
                props.append(parent_prop)

        if parent.get("parent"):
            self._traverse_parents(leaf, parent, ns, pos_constr, named_constr, props, abstract_objects)
        else:
            # Now we know we can create an object out of all the accumulated values.

            # The object's class is its topmost parent's class.
            class_ = parent.get("class")
            id, factory, lazy_init, abstract, parent, scope_ = self._get_basic_object_data(leaf, class_)

            c = self._create_object(id, factory, lazy_init, abstract, parent,
                           scope_, pos_constr, named_constr, props)

            self.objects.append(c)

        return parent

    def _get_pos_constr(self, object, ns):
        """ Returns a list of all positional constructor arguments of an object.
        """
        return [self._convert_prop_def(object, constr, object.get("id") + ".constr", ns) for constr in object.findall(ns+"constructor-arg")
                if not "name" in constr.attrib]

    def _get_named_constr(self, object, ns):
        """ Returns a dictionary of all named constructor arguments of an object.
        """
        return dict([(str(constr.get("name")), self._convert_prop_def(object, constr, object.get("id") + ".constr", ns))
                    for constr in object.findall(ns+"constructor-arg")  if "name" in constr.attrib])

    def _get_props(self, object, ns):
        """ Returns a list of all properties defined by an object.
        """
        return [self._convert_prop_def(object, p, p.get("name"), ns) for p in object.findall(ns+"property")]

    def _create_object(self, id, factory, lazy_init, abstract, parent,
                           scope, pos_constr, named_constr, props):
        """ A helper function which creates an object out of the supplied
        arguments.
        """

        c = ObjectDef(id=id, factory=factory, lazy_init=lazy_init,
            abstract=abstract, parent=parent)

        c.scope = scope
        c.pos_constr = pos_constr
        c.named_constr = named_constr
        c.props = props

        self.logger.debug("object: props = %s" % c.props)
        self.logger.debug("object: There are %s props" % len(c.props))

        return c

    def _get_basic_object_data(self, object, class_):
        """ A convenience method which creates basic object's data so that
        the code is not repeated.
        """

        if "scope" in object.attrib:
            scope_ = scope.convert(object.get("scope"))
        else:
            scope_ = scope.SINGLETON

        return(object.get("id"),  ReflectiveObjectFactory(class_),
            object.get("lazy-init", False), object.get("abstract", False),
               object.get("parent"), scope_)

    def _convert_object(self, object, prefix="", ns=None):
        """ This function collects all parameters required for an object creation
        and then calls a helper function which creates it.
        """
        if prefix != "":
            if "id" in object.attrib:
                object.set("id", prefix + "." + object.get("id"))
            else:
                object.set("id", prefix + ".<anonymous>")

        id, factory, lazy_init, abstract, parent, scope_ = self._get_basic_object_data(object, object.get("class"))

        pos_constr = self._get_pos_constr(object, ns)
        named_constr = self._get_named_constr(object, ns)
        props = self._get_props(object, ns)

        return self._create_object(id, factory, lazy_init, abstract, parent,
            scope_, pos_constr, named_constr, props)

    def _convert_ref(self, ref_node, name):
        if hasattr(ref_node, "attrib"):
            results = ReferenceDef(name, ref_node.get("object"))
            self.logger.debug("ref: Returning %s" % results)
            return results
        else:
            results = ReferenceDef(name, ref_node)
            self.logger.debug("ref: Returning %s" % results)
            return results

    def _convert_value(self, value, id, name, ns):
        if value.text is not None and value.text.strip() != "":
            self.logger.debug("value: Converting a direct value <%s>" % value.text)
            return value.text
        else:
            if value.tag == ns+"value":
                self.logger.debug("value: Converting a value's children %s" % value.getchildren()[0])
                results = self._convert_value(value.getchildren()[0], id, name, ns)
                self.logger.debug("value: results = %s" % str(results))
                return results
            elif value.tag == ns+"tuple":
                self.logger.debug("value: Converting a tuple")
                return self._convert_tuple(value, id, name, ns).value
            elif value.tag == ns+"list":
                self.logger.debug("value: Converting a list")
                return self._convert_list(value, id, name, ns).value
            elif value.tag == ns+"dict":
                self.logger.debug("value: Converting a dict")
                return self._convert_dict(value, id, name, ns).value
            elif value.tag == ns+"set":
                self.logger.debug("value: Converting a set")
                return self._convert_set(value, id, name, ns).value
            elif value.tag == ns+"frozenset":
                self.logger.debug("value: Converting a frozenset")
                return self._convert_frozen_set(value, id, name, ns).value
            else:
                self.logger.debug("value: %s.%s Don't know how to handle %s" % (id, name, value.tag))

    def _convert_dict(self, dict_node, id, name, ns):
        dict = {}
        for entry in dict_node.findall(ns+"entry"):
            self.logger.debug("dict: entry = %s" % entry)
            key = entry.find(ns+"key").find(ns+"value").text
            self.logger.debug("dict: key = %s" % key)
            if entry.find(ns+"value") is not None:
                dict[key] = self._convert_value(entry.find(ns+"value"), id, "%s.dict['%s']" % (name, key), ns)
            elif entry.find(ns+"ref") is not None:
                dict[key] = self._convert_ref(entry.find(ns+"ref"), "%s.dict['%s']" % (name, key))
            elif entry.find(ns+"object") is not None:
                self.logger.debug("dict: Parsing an inner object definition...")
                dict[key] = self._convert_inner_object(entry.find(ns+"object"), id, "%s.dict['%s']" % (name, key), ns)
            else:
                for token in ["dict", "tuple", "set", "frozenset", "list"]:
                    if entry.find(ns+token) is not None:
                        dict[key] = self._convert_value(entry.find(ns+token), id, "%s.dict['%s']" % (name, key), ns)
                        break
                if key not in dict:
                    self.logger.debug("dict: Don't know how to handle %s" % entry.tag)

        self.logger.debug("Dictionary is now %s" % dict)
        return DictDef(name, dict)

    def _convert_props(self, props_node, name, ns):
        dict = {}
        self.logger.debug("props: Looking at %s" % props_node)
        for prop in props_node:
            dict[prop.get("key")] = str(prop.text)
        self.logger.debug("props: Dictionary is now %s" % dict)
        return DictDef(name, dict)

    def _convert_list(self, list_node, id, name, ns):
        list = []
        self.logger.debug("list: Parsing %s" % list_node)
        for element in list_node:
            if element.tag == ns+"value":
                list.append(str(element.text))
            elif element.tag == ns+"ref":
                list.append(self._convert_ref(element, "%s.list[%s]" % (name, len(list))))
            elif element.tag == ns+"object":
                self.logger.debug("list: Parsing an inner object definition...")
                list.append(self._convert_inner_object(element, id, "%s.list[%s]" % (name, len(list)), ns))
            elif element.tag in [ns+token for token in ["dict", "tuple", "set", "frozenset", "list"]]:
                self.logger.debug("This list has child elements of type %s." % element.tag)
                list.append(self._convert_value(element, id, "%s.list[%s]" % (name, len(list)), ns))
                self.logger.debug("List is now %s" % list)
            else:
                self.logger.debug("list: Don't know how to handle %s" % element.tag)
        self.logger.debug("List is now %s" % list)
        return ListDef(name, list)

    def _convert_tuple(self, tuple_node, id, name, ns):
        list = []
        self.logger.debug("tuple: Parsing %s" % tuple_node)
        for element in tuple_node:
            self.logger.debug("tuple: Looking at %s" % element)
            if element.tag == ns+"value":
                self.logger.debug("tuple: Appending %s" % element.text)
                list.append(str(element.text))
            elif element.tag == ns+"ref":
                list.append(self._convert_ref(element, "%s.tuple(%s}" % (name, len(list))))
            elif element.tag == ns+"object":
                self.logger.debug("tuple: Parsing an inner object definition...")
                list.append(self._convert_inner_object(element, id, "%s.tuple(%s)" % (name, len(list)), ns))
            elif element.tag in [ns+token for token in ["dict", "tuple", "set", "frozenset", "list"]]:
                self.logger.debug("tuple: This tuple has child elements of type %s." % element.tag)
                list.append(self._convert_value(element, id, "%s.tuple(%s)" % (name, len(list)), ns))
                self.logger.debug("tuple: List is now %s" % list)
            else:
                self.logger.debug("tuple: Don't know how to handle %s" % element.tag)
        self.logger.debug("Tuple is now %s" % str(tuple(list)))
        return TupleDef(name, tuple(list))

    def _convert_set(self, set_node, id, name, ns):
        s = set()
        self.logger.debug("set: Parsing %s" % set_node)
        for element in set_node:
            self.logger.debug("Looking at element %s" % element)
            if element.tag == ns+"value":
                s.add(str(element.text))
            elif element.tag == ns+"ref":
                s.add(self._convert_ref(element, name + ".set"))
            elif element.tag == ns+"object":
                self.logger.debug("set: Parsing an inner object definition...")
                s.add(self._convert_inner_object(element, id, "%s.set(%s)" % (name, len(s)), ns))
            elif element.tag in [ns+token for token in ["dict", "tuple", "set", "frozenset", "list"]]:
                self.logger.debug("set: This set has child elements of type %s." % element.tag)
                s.add(self._convert_value(element, id, "%s.set(%s)" % (name,len(s)), ns))
            else:
                self.logger.debug("set: Don't know how to handle %s" % element.tag)
        self.logger.debug("Set is now %s" % s)
        return SetDef(name, s)

    def _convert_frozen_set(self, frozen_set_node, id, name, ns):
        item = self._convert_set(frozen_set_node, id, name, ns)
        self.logger.debug("frozenset: Frozen set is now %s" % frozenset(item.value))
        return FrozenSetDef(name, frozenset(item.value))

    def _convert_inner_object(self, object_node, id, name, ns):
        inner_object_def = self._convert_object(object_node, prefix="%s.%s" % (id, name), ns=ns)
        self.logger.debug("innerobj: Innerobject is now %s" % inner_object_def)
        self.objects.append(inner_object_def)
        return InnerObjectDef(name, inner_object_def)

    def _convert_prop_def(self, comp, p, name, ns):
        "This function translates object properties into useful collections of information for the container."
        #self.logger.debug("Is %s.%s a ref? %s" % (comp.get("id"), p.get("name"), p.find(ns+"ref") is not None or "ref" in p.attrib))
        #self.logger.debug("Is %s.%s a value? %s" % (comp.get("id"), p.get("name"), p.find(ns+"value") is not None or "value" in p.attrib))
        #self.logger.debug("Is %s.%s an inner object? %s" % (comp.get("id"), p.get("name"), p.find(ns+"object") is not None or "object" in p.attrib))
        #self.logger.debug("Is %s.%s a dict? %s" % (comp.get("id"), p.get("name"), p.find(ns+"dict") is not None or "dict" in p.attrib))
        #self.logger.debug("Is %s.%s a list? %s" % (comp.get("id"), p.get("name"), p.find(ns+"list") is not None or "list" in p.attrib))
        #self.logger.debug("Is %s.%s a tuple? %s" % (comp.get("id"), p.get("name"), p.find(ns+"tuple") is not None or "tuple" in p.attrib))
        #self.logger.debug("Is %s.%s a set? %s" % (comp.get("id"), p.get("name"), p.find(ns+"set") is not None or "set" in p.attrib))
        #self.logger.debug("Is %s.%s a frozenset? %s" % (comp.get("id"), p.get("name"), p.find(ns+"frozenset") is not None or "frozenset" in p.attrib))
        #self.logger.debug("")
        if "ref" in p.attrib or p.find(ns+"ref") is not None:
            if "ref" in p.attrib:
                return self._convert_ref(p.get("ref"), name)
            else:
                return self._convert_ref(p.find(ns+"ref"), name)
        elif "value" in p.attrib or p.find(ns+"value") is not None:
            if "value" in p.attrib:
                return ValueDef(name, str(p.get("value")))
            else:
                text = p.find(ns+"value").text
                try:
                    text = str(text)
                except UnicodeEncodeError:
                    text = unicode(text)
                return ValueDef(name, text)
        elif "dict" in p.attrib or p.find(ns+"dict") is not None:
            if "dict" in p.attrib:
                return self._convert_dict(p.get("dict"), comp.get("id"), name, ns)
            else:
                return self._convert_dict(p.find(ns+"dict"), comp.get("id"), name, ns)
        elif "props" in p.attrib or p.find(ns+"props") is not None:
            if "props" in p.attrib:
                return self._convert_props(p.get("props"), name, ns)
            else:
                return self._convert_props(p.find(ns+"props"), name, ns)
        elif "list" in p.attrib or p.find(ns+"list") is not None:
            if "list" in p.attrib:
                return self._convert_list(p.get("list"), comp.get("id"), name, ns)
            else:
                return self._convert_list(p.find(ns+"list"), comp.get("id"), name, ns)
        elif "tuple" in p.attrib or p.find(ns+"tuple") is not None:
            if "tuple" in p.attrib:
                return self._convert_tuple(p.get("tuple"), comp.get("id"), name, ns)
            else:
                return self._convert_tuple(p.find(ns+"tuple"), comp.get("id"), name, ns)
        elif "set" in p.attrib or p.find(ns+"set") is not None:
            if "set" in p.attrib:
                return self._convert_set(p.get("set"), comp.get("id"), name, ns)
            else:
                return self._convert_set(p.find(ns+"set"), comp.get("id"), name, ns)
        elif "frozenset" in p.attrib or p.find(ns+"frozenset") is not None:
            if "frozenset" in p.attrib:
                return self._convert_frozen_set(p.get("frozenset"), comp.get("id"), name, ns)
            else:
                return self._convert_frozen_set(p.find(ns+"frozenset"), comp.get("id"), name, ns)
        elif "object" in p.attrib or p.find(ns+"object") is not None:
            if "object" in p.attrib:
                return self._convert_inner_object(p.get("object"), comp.get("id"), name, ns)
            else:
                return self._convert_inner_object(p.find(ns+"object"), comp.get("id"), name, ns)

class YamlConfig(Config):
    """
    YamlConfig provides an alternative YAML-based version of objects.
    """
    def __init__(self, config_location):
        if isinstance(config_location, list):
            self.config_location = config_location
        else:
            self.config_location = [config_location]
        self.logger = logging.getLogger("springpython.config.YamlConfig")

        # By making this an instance-based property (instead of function local), inner object
        # definitions can add themselves to the list in the midst of parsing an input.
        self.objects = []

    def read_object_defs(self):
        import yaml

        self.logger.debug("==============================================================")
        # Reset, in case the file is re-read
        self.objects = []
        for config in self.config_location:
            self.logger.debug("* Parsing %s" % config)
            stream = file(config)
            doc = yaml.load(stream)
            self.logger.debug(doc)

            # A dictionary of abstract objects, keyed by their IDs, used in
            # traversing the hierarchies of parents; built upfront here for
            # convenience.
            abstract_objects = {}
            for object in doc["objects"]:
                if "abstract" in object:
                    abstract_objects[object["object"]] = object

            for object in doc["objects"]:
                if not "class" in object and not "parent" in object:
                    self._map_custom_class(object, yaml_mappings)

                elif "parent" in object:
                    # Children are added to self.objects during the children->abstract parents traversal.
                    pos_constr = self._get_pos_constr(object)
                    named_constr = self._get_named_constr(object)
                    props = self._get_props(object)
                    self._traverse_parents(object, object, pos_constr, named_constr, props, abstract_objects)
                    continue

                self._print_obj(object)
                self.objects.append(self._convert_object(object))

        self.logger.debug("==============================================================")
        self.logger.debug("objects = %s" % self.objects)
        return self.objects

    def _map_custom_class(self, obj, mappings):
        """ Enrich the object's attributes and make it look to the rest of
        YamlConfig as if the object had all of them right in the definition.
        """
        for class_name in mappings:
            if class_name in obj:
                self.logger.debug("Found a matching type: %s -> %s" % (obj["object"],
                    class_name))

                obj["class"] = mappings[class_name]
                obj["constructor-args"] = [obj[class_name]]
                break
        else:
            self.logger.warning("No matching type found for object %s" % obj)

    def _traverse_parents(self, leaf, child, pos_constr,
                            named_constr, props, abstract_objects):

        parent = abstract_objects[child["parent"]]

        # At this point we only build up the lists of parameters but we don't create
        # the object yet because the current parent object may still have its
        # own parent.

        # Positional constructors

        parent_pos_constrs = self._get_pos_constr(parent)

        # Make sure there are as many child positional parameters as there
        # are in the parent's list.

        len_pos_constr = len(pos_constr)
        len_parent_pos_constrs = len(parent_pos_constrs)

        if len_pos_constr < len_parent_pos_constrs:
            pos_constr.extend([None] * (len_parent_pos_constrs - len_pos_constr))

        for idx, parent_pos_constr in enumerate(parent_pos_constrs):
            if not pos_constr[idx]:
                pos_constr[idx] = parent_pos_constr

        # Named constructors
        child_named_constrs = named_constr
        parent_named_constrs = self._get_named_constr(parent)

        for parent_named_constr in parent_named_constrs:
            if parent_named_constr not in child_named_constrs:
                named_constr[parent_named_constr] = parent_named_constrs[parent_named_constr]

        # Properties
        child_props = [prop.name for prop in props]
        parent_props = self._get_props(parent)

        for parent_prop in parent_props:
            if parent_prop.name not in child_props:
                props.append(parent_prop)

        if "parent" in parent:
            self._traverse_parents(leaf, parent, pos_constr, named_constr, props, abstract_objects)
        else:
            # Now we know we can create an object out of all the accumulated values.

            # The object's class is its topmost parent's class.
            class_ = parent["class"]
            id, factory, lazy_init, abstract, parent, scope_ = self._get_basic_object_data(leaf, class_)

            c = self._create_object(id, factory, lazy_init, abstract, parent,
                           scope_, pos_constr, named_constr, props)

            self.objects.append(c)

        return parent

    def _get_pos_constr(self, object):
        """ Returns a list of all positional constructor arguments of an object.
        """
        if "constructor-args" in object and isinstance(object["constructor-args"], list):
            return [self._convert_prop_def(object, constr, object["object"]) for constr in object["constructor-args"]]
        return []

    def _get_named_constr(self, object):
        """ Returns a dictionary of all named constructor arguments of an object.
        """
        if "constructor-args" in object and isinstance(object["constructor-args"], dict):
            return dict([(name, self._convert_prop_def(object, constr, object["object"]))
                            for (name, constr) in object["constructor-args"].items()])
        return {}

    def _get_props(self, object):
        """ Returns a list of all properties defined by an object.
        """
        if "properties" in object:
            return [self._convert_prop_def(object, p, name) for (name, p) in object["properties"].items()]
        return []

    def _create_object(self, id, factory, lazy_init, abstract, parent,
                           scope, pos_constr, named_constr, props):
        """ A helper function which creates an object out of the supplied
        arguments.
        """

        c = ObjectDef(id=id, factory=factory, lazy_init=lazy_init,
            abstract=abstract, parent=parent)

        c.scope = scope
        c.pos_constr = pos_constr
        c.named_constr = named_constr
        c.props = props

        self.logger.debug("object: props = %s" % c.props)
        self.logger.debug("object: There are %s props" % len(c.props))

        return c

    def _get_basic_object_data(self, object, class_):
        """ A convenience method which creates basic object's data so that
        the code is not repeated.
        """

        if "scope" in object:
            scope_ = scope.convert(object["scope"])
        else:
            scope_ = scope.SINGLETON

        return(object["object"],  ReflectiveObjectFactory(class_),
            object.get("lazy-init", False), object.get("abstract", False),
               object.get("parent"), scope_)

    def _convert_object(self, object, prefix=""):
        "This function generates a object definition, then converts scope and property elements."
        if prefix != "":
            if "object" in object and object["object"] is not None:
                object["object"] = prefix + "." + object["object"]
            else:
                object["object"] = prefix + ".<anonymous>"

        id, factory, lazy_init, abstract, parent, scope_ = self._get_basic_object_data(object, object.get("class"))

        pos_constr = self._get_pos_constr(object)
        named_constr = self._get_named_constr(object)
        props = self._get_props(object)

        return self._create_object(id, factory, lazy_init, abstract, parent,
            scope_, pos_constr, named_constr, props)

    def _print_obj(self, obj, level=0):
        self.logger.debug("%sobject = %s" % ("\t"*level, obj["object"]))
        self.logger.debug("%sobject id = %s" % ("\t"*level, obj["object"]))
        self.logger.debug("%sclass = %s" % ("\t"*(level+1), obj["class"]))

        if "scope" in obj:
            self.logger.debug("%sscope = %s" % ("\t"*(level+1), obj["scope"]))
        else:
            self.logger.debug("%sscope = singleton (default)" % ("\t"*(level+1)))

        if "properties" in obj:
            self.logger.debug("%sproperties:" % ("\t"*(level+1)))
            for prop in obj["properties"].keys():
                if "object" in obj["properties"][prop]:
                    self.logger.debug("%s%s = ..." % ("\t"*(level+2), prop))
                    self._print_obj(obj["properties"][prop], level+3)
                else:
                    self.logger.debug("%s%s = %s" % ("\t"*(level+2), prop, obj["properties"][prop]))
        self.logger.debug("")

    def _convert_ref(self, ref_node, name):
        self.logger.debug("ref: Parsing %s, %s" % (ref_node, name))
        if "object" in ref_node:
            return ReferenceDef(name, ref_node["object"])
        else:
            return ReferenceDef(name, ref_node)

    def _convert_value(self, value, id, name):
        results = []

        if isinstance(value, dict):
            if "tuple" in value:
                self.logger.debug("value: Converting tuple")
                return self._convert_tuple(value["tuple"], id, name)
            elif "list" in value:
                self.logger.debug("value: Converting list")
                return self._convert_list(value["list"], id, name)
            elif "dict" in value:
                self.logger.debug("value: Converting dict")
                return self._convert_dict(value["dict"], id, name)
            elif "set" in value:
                self.logger.debug("value: Converting set")
                return self._convert_set(value["set"], id, name)
            elif "frozenset" in value:
                self.logger.debug("value: Converting frozenset")
                return self._convert_frozen_set(value["frozenset"], id, name)
        else:
            self.logger.debug("value: Plain ole value = %s" % value)
            return value

        return results

    def _convert_dict(self, dict_node, id, name):
        d = {}
        for (k, v) in dict_node.items():
            if isinstance(v, dict):
                self.logger.debug("dict: You have a special type stored at %s" % k)
                if "ref" in v:
                    self.logger.debug("dict/ref: k,v = %s,%s" % (k, v))
                    d[k] = self._convert_ref(v["ref"], "%s.dict['%s']" % (name, k))
                    self.logger.debug("dict: Stored %s => %s" % (k, d[k]))
                elif "tuple" in v:
                    self.logger.debug("dict: Converting a tuple...")
                    d[k] = self._convert_tuple(v["tuple"], "%s.dict['%s']" % ( name, k))
                else:
                    self.logger.debug("dict: Don't know how to handle type %s" % v)
            else:
                self.logger.debug("dict: %s is NOT a dict, so going to convert as a value." % v)
                d[k] = self._convert_value(v, id, "%s.dict['%s']" % (name, k))
        return DictDef(name, d)

    def _convert_props(self, props_node, name):
        dict = {}
        for prop in props_node.prop:
            dict[prop.key] = str(prop)
        return DictDef(name, dict)

    def _convert_list(self, list_node, id, name):
        list = []
        for item in list_node:
            self.logger.debug("list: Adding %s to list..." % item)
            if isinstance(item, dict):
                if "ref" in item:
                    list.append(self._convert_ref(item["ref"], "%s.list[%s]" % (name, len(list))))
                elif "object" in item:
                    list.append(self._convert_inner_object(item, id, "%s.list[%s]" % (name, len(list))))
                elif len(set(["dict", "tuple", "set", "frozenset", "list"]) & set(item)) > 0:
                    list.append(self._convert_value(item, id, "%s.list[%s]" % (name, len(list))))
                else:
                    self.logger.debug("list: Don't know how to handle %s" % item.keys())
            else:
                list.append(item)
        return ListDef(name, list)

    def _convert_tuple(self, tuple_node, id, name):
        list = []
        self.logger.debug("tuple: tuple_node = %s, id = %s, name = %s" % (tuple_node, id, name))
        for item in tuple_node:
            if isinstance(item, dict):
                if "ref" in item:
                    list.append(self._convert_ref(item["ref"], name + ".tuple"))
                elif "object" in item:
                    list.append(self._convert_inner_object(item, id, "%s.tuple[%s]" % (name, len(list))))
                elif len(set(["dict", "tuple", "set", "frozenset", "list"]) & set(item)) > 0:
                    list.append(self._convert_value(item, id, "%s.tuple[%s]" % (name, len(list))))
                else:
                    self.logger.debug("tuple: Don't know how to handle %s" % item)
            else:
                list.append(item)
        return TupleDef(name, tuple(list))

    def _convert_set(self, set_node, id, name):
        s = set()
        self.logger.debug("set: set_node = %s, id = %s, name = %s" % (set_node, id, name))
        for item in set_node:
            if isinstance(item, dict):
                if "ref" in item:
                    s.add(self._convert_ref(item["ref"], name + ".set"))
                elif "object" in item:
                    s.add(self._convert_inner_object(item, id, "%s.set[%s]" % (name, len(s))))
                elif len(set(["dict", "tuple", "set", "frozenset", "list"]) & set(item)) > 0:
                    s.add(self._convert_value(item, id, "%s.set[%s]" % (name, len(s))))
                else:
                    self.logger.debug("set: Don't know how to handle %s" % item)
            else:
                s.add(item)
        return SetDef(name, s)

    def _convert_frozen_set(self, frozen_set_node, id, name):
        item = self._convert_set(frozen_set_node, id, name)
        self.logger.debug("frozenset: Just got back converted set %s" % item)
        self.logger.debug("frozenset: value is %s, which will be turned into %s" % (item.value, frozenset(item.value)))
        return FrozenSetDef(name, frozenset(item.value))

    def _convert_inner_object(self, object_node, id, name):
        self.logger.debug("inner object: Converting %s" % object_node)
        inner_object_def = self._convert_object(object_node, prefix="%s.%s" % (id, name))
        self.objects.append(inner_object_def)
        return InnerObjectDef(name, inner_object_def)

    def _convert_prop_def(self, comp, p, name):
        "This function translates object properties into useful collections of information for the container."
        self.logger.debug("prop_def: Trying to read property %s -> %s" % (name, p))
        if isinstance(p, dict):
            if "ref" in p:
                self.logger.debug("prop_def: >>>>>>>>>>>>Call _convert_ref(%s, %s)" % (p["ref"], name))
                return self._convert_ref(p["ref"], name)
            elif "tuple" in p:
                self.logger.debug("prop_def: Call _convert_tuple(%s,%s,%s)" % (p["tuple"], comp["object"], name))
                return self._convert_tuple(p["tuple"], comp["object"], name)
            elif "set" in p:
                self.logger.debug("prop_def: Call _convert_set(%s,%s,%s)" % (p["set"], comp["object"], name))
                return self._convert_set(p["set"], comp["object"], name)
            elif "frozenset" in p:
                self.logger.debug("prop_def: Call _convert_frozen_set(%s,%s,%s)" % (p["frozenset"], comp["object"], name))
                return self._convert_frozen_set(p["frozenset"], comp["object"], name)
            elif "object" in p:
                self.logger.debug("prop_def: Call _convert_inner_object(%s,%s,%s)" % (p, comp["object"], name))
                return self._convert_inner_object(p, comp["object"], name)
            else:
                #self.logger.debug("prop_def: Don't know how to handle %s" % p)
                return self._convert_dict(p, comp["object"], name)
        elif isinstance(p, list):
            return self._convert_list(p, comp["object"], name)
        elif isinstance(p, unicode):
            return ValueDef(name, unicode(p))
        else:
            return ValueDef(name, str(p))
        return None

        if hasattr(p, "ref"):
            return self._convert_ref(p.ref, name)
        elif hasattr(p, "value"):
            return ValueDef(name, str(p.value))
        elif hasattr(p, "dict"):
            return self._convert_dict(p.dict, comp.id, name)
        elif hasattr(p, "props"):
            return self._convert_props(p.props, name)
        elif hasattr(p, "list"):
            return self._convert_list(p.list, comp.id, name)
        elif hasattr(p, "tuple"):
            return self._convert_tuple(p.tuple, comp.id, name)
        elif hasattr(p, "set"):
            return self._convert_set(p.set, comp.id, name)
        elif hasattr(p, "frozenset"):
            self.logger.debug("Converting frozenset")
            return self._convert_frozen_set(p.frozenset, comp.id, name)
        elif hasattr(p, "object"):
            return self._convert_inner_object(p.object, comp.id, name)

class PythonConfig(Config, ApplicationContextAware):
    """
    PythonConfig supports using pure python code to define objects.
    """

    def __init__(self):
        self.logger = logging.getLogger("springpython.config.PythonConfig")
        super(PythonConfig, self).__init__()

    def read_object_defs(self):
        self.logger.debug("==============================================================")
        objects = []
        self.logger.debug("Parsing %s" % self)
        for name, method in inspect.getmembers(self, inspect.ismethod):
            if name not in _pythonConfigMethods:
                try:
                    wrapper = method.im_func.func_globals["_call_"]

                    if wrapper.func_name.startswith("object"):
                        c = ObjectDef(id=name, factory=PythonObjectFactory(method, wrapper),
                                scope=wrapper.scope, lazy_init=wrapper.lazy_init,
                                abstract=wrapper.abstract, parent=wrapper.parent)
                        objects.append(c)
                except KeyError, e:
                    pass
        self.logger.debug("==============================================================")
        return objects

    def set_app_context(self, app_context):
        super(PythonConfig, self).set_app_context(app_context)
        try:
            _object_context[(self,)]["container"] = app_context
        except KeyError, e:
            _object_context[(self,)] = {"container": app_context}


_pythonConfigMethods = [name for (name, method) in inspect.getmembers(PythonConfig, inspect.ismethod)]

_object_context = {}

def _object_wrapper(f, scope, parent, log_func_name, *args, **kwargs):
    """
    This function checks if the object already exists in the container. If so,
    it will retrieve its results. Otherwise, it calls the function.

    For prototype objects, the function is basically a pass through,
    because everytime a prototype function is called, there should be no
    caching of results.

    Using the @decorator library greatly simplifies the implementation of this.
    """

    def _deco(f, scope, parent, log_func_name, *args, **kwargs):
        log = logging.getLogger("springpython.config.%s%s - %s%s" % (log_func_name,
                                    f, str(args), scope))
        if f.func_name != top_func:
            log.debug("This is NOT the top-level object %s, deferring to container." % top_func)
            container = _object_context[args]["container"]
            log.debug("Container = %s" % container)

            if parent:
                parent_result = container.get_object(parent, ignore_abstract=True)
                log.debug("This IS the top-level object, calling %s(%s)" \
                           % (f.func_name, parent_result))
                results = container.get_object(f.func_name)(parent_result)
            else:
                results = container.get_object(f.func_name)

            log.debug("Found %s inside the container" % results)
            return results
        else:
            if parent:
                container = _object_context[(args[0],)]["container"]
                parent_result = container.get_object(parent, ignore_abstract=True)
                log.debug("This IS the top-level object, calling %s(%s)" \
                           % (f.func_name, parent_result))
                results = f(container, parent_result)
            else:
                log.debug("This IS the top-level object, calling %s()." % f.func_name)
                results = f(*args, **kwargs)

            log.debug("Found %s" % results)
            return results

    return _deco(f, scope, parent, log_func_name, *args, **kwargs)

def Object(theScope=scope.SINGLETON, lazy_init=False, abstract=False, parent=None):
    """
    This function is a wrapper around the function which returns the real decorator.
    It decides, based on scope and lazy-init, which decorator to return.
    """
    if type(theScope) == types.FunctionType:
        return Object()(theScope)

    elif theScope == scope.SINGLETON:
        log_func_name = "objectSingleton"

    elif theScope == scope.PROTOTYPE:
        log_func_name = "objectPrototype"

    else:
        raise InvalidObjectScope("Don't know how to handle scope %s" % theScope)

    def object_wrapper(f, *args, **kwargs):
        return _object_wrapper(f, theScope, parent, log_func_name, *args, **kwargs)

    object_wrapper.lazy_init = lazy_init
    object_wrapper.abstract = abstract
    object_wrapper.parent = parent
    object_wrapper.scope = theScope

    return decorator(object_wrapper)
